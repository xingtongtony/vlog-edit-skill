"""Assemble a long EDL in Python - with the rules that keep a cut watchable enforced as errors:

  * no footage twice: a source second may be used once; duplicate files (ingest/duplicates.json) count as one clip;
    clips shown in the cold-open teaser do not come back in the body
  * chronological: in the body, every shot must be shot later than the one before (photo EXIF / clip time + offset);
    `times=` overrides clips whose timestamps are unreliable; `back=True` allows a deliberate flashback
  * breathing room: talk keeps 0.25 s before the first word and 0.7 s after the last; pauses shorter than 1.2 s stay
    inside one shot instead of becoming jump cuts
  * steady footage (ingest/motion.json, scripts/motion.py): B-roll may not shake or roll; drone shots must move in one
    direction (no back-and-forth); talking shots get looser limits. `motion_ok='why'` overrides after looking at it
  * on save: no title / chapter / end card while a subtitle is on screen; no shot without sound (drone, photo, mute)
    while no music plays - unless it carries a `bed` of natural sound from another clip

    b = Builder(project, times={'c066': '2026-09-30T15:35'})
    b.begin_teaser(); ...; b.end_teaser()
    b.talk('c001', [(40.3, 47.1, ['line', 'line'])])
    b.vo('一整段旁白，带标点。一口气读完。', broll=[('c007', 2.0), ('c008', 1.0)], xfade=0.6)
    b.photo('p084', 3.0, caption='TARSIER · LOBOC')
    b.save('edl/full.json')
"""
import json, math, os, re, datetime

# Narration pacing, measured 2026-10-19: the user reads at 4.0-4.6 syllables/s; a reference creator's narration runs
# 4.7/s overall with 0.4-0.6 s between sentences. The old timing (chars/4.2 + 0.7 s per line, + 0.6 s between lines)
# left the user waiting 1-2.6 s for every next subtitle while recording.
VO_RATE = 4.5                                   # syllables per second of speech
VO_PAUSE = {'sentence': 0.45, 'semi': 0.3, 'clause': 0.15, 'split': 0.0}
SUB_MAX = 16                                    # display width of one subtitle line (CJK 1, Latin letter 0.55)

def syllables(text):
    """Spoken length: a CJK character is 1, a Latin word 2, a number one per digit ('30' is 三十)."""
    return (len(re.findall(r'[一-鿿]', text)) + 2 * len(re.findall(r'[A-Za-z]+', text))
            + len(re.findall(r'\d', text)))

def vo_duration(text): return round(max(0.6, syllables(text) / VO_RATE), 2)

def _width(s): return sum(0.55 if c.isascii() else 1 for c in s if not c.isspace())

def _display(s): return re.sub(r'\s+', ' ', re.sub(r'[，,、；;：:——“”"]', ' ', s)).strip()

def _halve(clause):
    """Cut a clause wider than SUB_MAX into even pieces, never inside a Latin word or a number."""
    n = math.ceil(_width(clause) / SUB_MAX); target = _width(clause) / n; out, cur = [], ''
    for i, c in enumerate(clause):
        if cur and len(out) < n - 1 and _width(cur) >= target and not (cur[-1].isascii() and cur[-1].isalnum() and c.isascii() and c.isalnum()):
            out.append(cur); cur = ''
        cur += c
    return out + [cur]

def split_paragraph(text):
    """A narration paragraph -> subtitle lines [(display text, pause after)]. Sentences end at 。！？…; a sentence
    wider than SUB_MAX breaks at ，、：—, and a still-wide clause in even pieces; ； always ends a line. Display text has
    no punctuation."""
    parts = re.split(r'([。！？!?…]+|[，,、；;：:]|——)', text.strip())
    clauses = []                                # (text, pause kind after it)
    for k in range(0, len(parts), 2):
        t, d = parts[k].strip(), parts[k + 1] if k + 1 < len(parts) else '。'
        if not t: continue
        kind = 'sentence' if re.fullmatch(r'[。！？!?…]+', d) else 'semi' if d in '；;' else 'clause'
        pieces = _halve(t) if _width(t) > SUB_MAX else [t]
        clauses += [(p.strip(), 'split') for p in pieces[:-1]] + [(pieces[-1].strip(), kind)]
    lines, cur, cur_kind = [], '', None
    for t, kind in clauses:
        if cur and (cur_kind in ('sentence', 'semi') or _width(cur + t) + 1 > SUB_MAX):
            lines.append((cur, cur_kind)); cur = ''
        cur = f'{cur} {t}' if cur else t; cur_kind = kind
    if cur: lines.append((cur, cur_kind))
    return [(_display(t), VO_PAUSE[kind]) for t, kind in lines]

class EditError(Exception): pass

class Builder:
    LEAD, TAIL, MERGE_GAP = 0.25, 0.7, 1.2

    def __init__(self, project, fps=30, look='film', times=None):
        self.project, self.fps = project, fps
        ing = os.path.join(project, 'ingest')
        self.clips = {c['id']: c for c in json.load(open(os.path.join(ing, 'clips.json')))}
        rd = lambda f, d: json.load(open(os.path.join(ing, f))) if os.path.exists(os.path.join(ing, f)) else d
        self.stab = rd('stability.json', {}); self.dups = rd('duplicates.json', {}); self.motion = rd('motion.json', {})
        self.photos = {p['id']: p for p in rd('photos.json', [])}
        self.times = {k: datetime.datetime.fromisoformat(v) for k, v in (times or {}).items()}
        self.e = {'project': project, 'size': [1920, 1080], 'fps': fps, 'look': look, 'shots': [], 'subtitles': [],
                  'vo': [], 'cards': [], 'typewriter': [], 'fx': [], 'music': []}
        self.allowed = []
        self.pending_cards, self.used, self.teaser, self.teaser_clips, self.last_t, self.warnings = [], {}, False, set(), None, []

    # ---------------- bookkeeping ----------------
    @property
    def n(self): return len(self.e['shots'])
    def dur(self, i): s = self.e['shots'][i]; return round((s['out'] - s['in']) * self.fps) / self.fps
    def canon(self, clip): return self.dups.get(clip, clip)

    def when(self, clip, a=0.0):
        if clip in self.times: base = self.times[clip]
        elif clip in self.photos: base = datetime.datetime.strptime(self.photos[clip]['time'], '%Y:%m:%d %H:%M:%S')
        else:
            t = self.clips[clip].get('time', '')
            if not t: return None
            base = datetime.datetime.fromisoformat(t[:19])
        return base + datetime.timedelta(seconds=a)

    def begin_teaser(self): self.teaser = True
    def end_teaser(self): self.teaser = False

    def _check(self, clip, a, b, back):
        key = self.canon(clip)
        for (x, y) in self.used.get(key, []):
            if min(b, y) - max(a, x) > 0.1:
                raise EditError(f'{clip} {a:.1f}-{b:.1f}s overlaps footage already used ({key} {x:.1f}-{y:.1f}s)')
        if not self.teaser and key in self.teaser_clips:
            raise EditError(f'{clip} was shown in the teaser; pick other footage for the body')
        self.used.setdefault(key, []).append((a, b))
        if self.teaser: self.teaser_clips.add(key); return
        t = self.when(clip, a)
        if t is None: self.warnings.append(f'{clip}: no timestamp, order not checked'); return
        if self.last_t and t < self.last_t - datetime.timedelta(minutes=2) and not back:
            raise EditError(f'{clip} @{t:%m-%d %H:%M} goes back in time (previous shot {self.last_t:%m-%d %H:%M})')
        if not back: self.last_t = max(self.last_t or t, t)   # a shot placed out of order (back=True) does not move the clock,
                                                               # so the shots after it are still checked against the main line

    # ---------------- shots ----------------
    def allow_motion(self, clip, a, b, why):
        """Accept motion in [a, b] of a clip after looking at it, e.g. a fish school that fools the motion estimate."""
        self.allowed.append((clip, a, b, why))

    def _motion(self, clip, a, b, talk, ok):
        import motion
        if any(c == clip and x <= a and b <= y for c, x, y, _ in self.allowed): return
        drone = self.clips[clip]['kind'] == 'drone'
        good, why = motion.window_report(self.motion.get(clip), a, b, drone=drone, talk=talk)
        if good or ok: return
        alt = [s for s in motion.best_windows(self.motion[clip], b - a, drone=drone, talk=talk, n=12)
               if not any(min(s + b - a, y) - max(s, x) > 0.1 for x, y in self.used.get(self.canon(clip), []))][:5]
        raise EditError(f'{clip} {a:.1f}-{b:.1f}s: {why}. Steady windows in this clip: {alt or "none - use another clip"}')

    def shot(self, clip, a, b, audio='ambient', note='', back=False, motion_ok=None, **kw):
        c = self.clips[clip]; b = min(b, c['duration'] - 0.05); a = max(0.0, a)
        prev = self.e['shots'][-1] if self.e['shots'] else None
        if prev and prev['clip'] == clip and 0 < a - prev['out'] < 0.5:
            a = prev['out']            # a 0.1 s skip inside one take reads as a stutter: make it continuous
        if b - a < 0.5: raise EditError(f'{clip} {a}-{b} too short')
        self._motion(clip, a, b, audio == 'live', motion_ok)
        self._check(clip, a, b, back)
        if not c['audio'] and audio != 'mute': audio = 'mute'
        self.e['shots'].append(dict(clip=clip, **{'in': round(a, 3), 'out': round(b, 3)}, audio=audio, note=note, **kw))
        return self._placed()

    def photo(self, pid, dur=2.6, caption=None, note='', back=False, bed=None, **kw):
        """bed=(clip, start): natural sound from another clip under the photo (a music-free stretch must not go silent)."""
        if bed:
            if bed[1] + dur > self.clips[bed[0]]['duration'] - 0.05:
                raise EditError(f"bed {bed[0]} {bed[1]}+{dur:.1f}s runs past the end of the clip ({self.clips[bed[0]]['duration']:.1f}s) - the rest is silence")
            kw['bed'] = {'clip': bed[0], 'in': bed[1]}
        p = self.photos[pid]; self._check(pid, 0, dur, back)
        self.e['shots'].append(dict(clip=pid, photo=p['file'], caption=caption, **{'in': 0.0, 'out': round(dur, 3)}, audio='mute', note=note or 'photo', **kw))
        return self._placed()

    def _placed(self):
        i = self.n - 1
        for card in self.pending_cards: card['shot'] = i; self.e['cards'].append(card)
        self.pending_cards = []
        return i

    def steady(self, clip, dur, lo=0.0, hi=None):
        """Start of the steadiest `dur`-second window in [lo, hi] that is not used yet."""
        if clip in self.motion:
            import motion
            used = self.used.get(self.canon(clip), [])
            for s in motion.best_windows(self.motion[clip], dur, drone=self.clips[clip]['kind'] == 'drone', lo=lo, hi=hi, n=40):
                if not any(min(s + dur, y) - max(s, x) > 0.1 for x, y in used): return float(s)
            raise EditError(f'no steady unused {dur}s window in {clip} {lo}-{hi}')
        ps = self.stab.get(clip, {}).get('per_sec')
        hi = min(hi or self.clips[clip]['duration'], self.clips[clip]['duration']) - dur
        used = self.used.get(self.canon(clip), [])
        best, bs = None, 1e9
        for s in range(int(lo), int(hi) + 1):
            if any(min(s + dur, y) - max(s, x) > 0.1 for x, y in used): continue
            w = (ps or [])[s:s + max(1, int(round(dur)))]
            sc = sum(w) / len(w) if w else 0
            if sc < bs: bs, best = sc, float(s)
        if best is None: raise EditError(f'no unused {dur}s window in {clip} {lo}-{hi}')
        return best

    # ---------------- speech ----------------
    def words(self, clip):
        p = os.path.join(self.project, 'ingest', 'transcripts', f'{clip}.json')
        from transcribe import hallucination          # "Thank you." heard in underwater breathing etc.
        return [w for s in json.load(open(p))['segments'] if not hallucination(s) for w in s.get('words', []) if w['w'].strip()]

    def talk(self, clip, pieces, note='', xfade=0, back=False, motion_ok=None):
        """pieces: [(first_word_start, last_word_end, [corrected subtitle lines])]. Each piece is padded with LEAD / TAIL;
        pieces closer than MERGE_GAP are merged into one shot (the pause stays, no jump cut)."""
        dur = self.clips[clip]['duration']
        merged = []
        for a, b, lines in pieces:
            if merged and a - merged[-1][1] < self.MERGE_GAP: merged[-1] = (merged[-1][0], b, merged[-1][2] + [(a, b, lines)])
            else: merged.append((a, b, [(a, b, lines)]))
        first = None
        for k, (a, b, parts) in enumerate(merged):
            nxt = merged[k + 1][0] if k + 1 < len(merged) else dur
            sa, sb = max(0.0, a - self.LEAD), min(dur - 0.05, b + self.TAIL, nxt - 0.05)
            i = self.shot(clip, sa, sb, audio='live', note=note or parts[0][2][0] if parts[0][2] else note,
                          back=back, motion_ok=motion_ok, **({'xfade': xfade} if xfade and first is None else {}))
            first = i if first is None else first
            ws_all = self.words(clip)
            for pa, pb, lines in parts:
                ws = [w for w in ws_all if w['s'] >= pa - 0.05 and w['e'] <= pb + 0.05] or [{'w': 'x', 's': pa, 'e': pb}]
                n_chars = [len(re.sub(r'\W', '', l)) or 1 for l in lines]; tot = sum(n_chars); k0 = 0
                for j, line in enumerate(lines):
                    k1 = len(ws) if j == len(lines) - 1 else max(k0 + 1, round(len(ws) * sum(n_chars[:j + 1]) / tot))
                    seg = ws[k0:k1] or ws[-1:]
                    self.e['subtitles'].append({'shot': i, 'from': round(max(sa, seg[0]['s'] - 0.05), 2),
                                                'to': round(min(sb, seg[-1]['e'] + 0.25), 2), 'text': line})
                    k0 = k1
        return first

    # ---------------- narration over B-roll / photos ----------------
    def vo(self, paras, broll, gap=0.6, lead=0.6, tail=1.0, xfade=0, beds=None, back=False, motion_ok=None):
        """Narration laid over B-roll items: (clip, start[, audio[, seconds]]) or ('photo', pid[, caption]). An item
        with `seconds` keeps that length (a clip with only a short usable window); the others share the rest. A start of
        'next' continues the take where the previous item ended.
        `paras` is one paragraph (str) or a list of them, written with punctuation the way the user will say it in one
        breath; each is cut into short subtitle lines timed at the user's pace (VO_RATE, VO_PAUSE) and `gap` separates
        paragraphs. The block lasts as long as the speech needs plus air; each item gets an equal share (at least 2.2 s).
        A subtitle stays up through the pause after it, so the box does not flicker between lines of one paragraph.
        `back` / `motion_ok` apply to every B-roll item (e.g. to keep an order or a shot the user chose in Jianying)."""
        if isinstance(paras, str): paras = [paras]
        lines = []                              # (display text, speech seconds, pause after, paragraph id, paragraph)
        for p in paras:
            pid = sum(1 for v in self.e['vo'] if 'para_text' in v) + len({l[3] for l in lines})
            split = split_paragraph(p)
            for j, (text, pause) in enumerate(split):
                last = j == len(split) - 1
                lines.append((text, vo_duration(text), gap if last else pause, pid, p if j == 0 else None))
        need = lead + sum(d + a for _, d, a, _, _ in lines) - lines[-1][2] + tail
        fixed = [it[3] if it[0] != 'photo' and len(it) > 3 else None for it in broll]   # (clip, start, audio, seconds)
        free = sum(1 for f in fixed if f is None)
        share = max(2.2, (need - sum(f for f in fixed if f)) / free) if free else 0
        first = None
        for item in broll:
            extra = {'xfade': xfade} if xfade and first is None else {}
            if item[0] == 'photo':
                bed = beds[broll.index(item)] if beds else None
                i = self.photo(item[1], share, caption=item[2] if len(item) > 2 else None, bed=bed, back=back, **extra)
            else:
                clip, a = item[0], item[1]; audio = item[2] if len(item) > 2 else 'ambient'
                d = item[3] if len(item) > 3 else share
                if a == 'next': a = self.e['shots'][-1]['out']          # continue the take of the previous item
                elif a is None: a = self.steady(clip, d)
                i = self.shot(clip, a, a + d, audio=audio, note='VO B-roll', back=back, motion_ok=motion_ok, **extra)
            first = i if first is None else first
        t = lead; durs = [self.dur(k) for k in range(first, self.n)]
        for text, speech, pause, pid, para in lines:
            acc, k = 0.0, 0
            while k < len(durs) - 1 and acc + durs[k] <= t: acc += durs[k]; k += 1
            shown = speech + (pause if pause < gap else 0.3)
            v = {'shot': first + k, 'at': round(t - acc, 2), 'text': text, 'dur': round(shown, 2), 'speech': speech, 'para': pid}
            if para: v['para_text'] = para
            self.e['vo'].append(v)
            t += speech + pause
        return first

    # ---------------- graphics / music ----------------
    def chapter(self, num, title, sub='', at=0.8, dur=3.6):
        self.pending_cards.append({'kind': 'chapter', 'kicker': num, 'text': title, 'sub': sub, 'at': at, 'dur': dur})
    def card(self, text, at=0.4, dur=2.4, **kw): self.pending_cards.append({'text': text, 'at': at, 'dur': dur, **kw})
    def leak_before_next(self, dur=1.0): self.e['fx'].append({'type': 'leak', 'cut': self.n, 'dur': dur})
    def stamp(self, text, sub='', at=0.6, dur=2.6, y=0.10):
        self.e['typewriter'].append({'shot': self.n, 'at': at, 'dur': dur, 'text': text, 'sub': sub, 'y': y, 'step': 0.08})

    def music(self, path, start=0.0, gain=0.55, why='', delay=None):
        """Start a song at the next shot. `why`: the three feeling words / the place it should sound like.
        When it directly replaces another song, it waits `delay` (default 1.2 s) so the old one's fade-out is followed
        by a moment of natural sound instead of running straight into the new song. The track position at every
        timeline second stays the same (start moves with the delay), so beat anchors still hold."""
        prev = self.e['music'][-1] if self.e['music'] else None
        if prev and prev.get('to_shot') is None: prev['to_shot'] = self.n
        if delay is None: delay = 1.2 if prev and prev['to_shot'] == self.n and not prev.get('out_why') else 0.0
        self.e['music'].append({'path': path, 'start': round(start + delay, 3), 'at_offset': delay, 'from_shot': self.n,
                                'gain': gain, 'states': [], 'why': why})

    def music_out(self, why=''):
        """Song ends (fades out) before the next shot; no music until the next b.music(). Use for performances, singing,
        live music, a moment whose own sound is the point, and between chapters."""
        if self.e['music'] and self.e['music'][-1].get('to_shot') is None:
            self.e['music'][-1]['to_shot'] = self.n; self.e['music'][-1]['out_why'] = why
    def music_state(self, state, at=0.0): self.e['music'][-1]['states'].append({'shot': self.n, 'at': at, 'state': state})

    def _final_checks(self):
        t, start = 0.0, []
        for i in range(self.n): start.append(t); t += self.dur(i)
        sh = self.e['shots']
        text = [(start[s['shot']] + s['from'] - sh[s['shot']]['in'], start[s['shot']] + s['to'] - sh[s['shot']]['in'], s['text']) for s in self.e['subtitles']]
        text += [(start[v['shot']] + v['at'], start[v['shot']] + v['at'] + v['dur'], v['text']) for v in self.e['vo']]
        errs = []
        for c in self.e['cards']:
            if c.get('kind') not in ('chapter', 'title', 'end'): continue
            a = start[c['shot']] + c['at']; b = a + c['dur']
            hit = [x for x in text if min(b, x[1]) - max(a, x[0]) > 0]
            if hit: errs.append(f"{c.get('kind')} card '{c['text']}' is on screen with subtitle '{hit[0][2]}' - put the card on a shot nobody talks over")
        # music coverage per shot
        music = [False] * self.n
        for m in self.e['music']:
            for i in range(m['from_shot'], m.get('to_shot') if m.get('to_shot') is not None else self.n): music[i] = True
        for i, s in enumerate(sh):
            silent = s.get('photo') or s.get('audio') == 'mute' or not self.clips.get(s['clip'], {}).get('audio', False)
            if silent and not music[i] and not s.get('bed'):
                errs.append(f"shot {i} {s['clip']} ({s.get('note', '')}) has no sound and no music plays - add music, a bed, or another shot")
        # narration must not talk over audible speech in the picture under it
        for v in self.e['vo']:
            a = start[v['shot']] + v['at']; b = a + v['dur']
            for i in range(self.n):
                s = sh[i]; t0 = start[i]; t1 = t0 + self.dur(i)
                if s.get('photo') or min(b, t1) - max(a, t0) <= 0: continue
                audible = s.get('audio') in ('live', 'half') or (s.get('audio', 'ambient') == 'ambient' and not music[i])
                p = os.path.join(self.project, 'ingest', 'transcripts', f"{s['clip']}.json")
                if not audible or not os.path.exists(p): continue
                x, y = s['in'] + max(a, t0) - t0, s['in'] + min(b, t1) - t0
                said = ''.join(w['w'] for w in self.words(s['clip']) if w['s'] < y and w['e'] > x).strip()
                if len(said) > 1: errs.append(f"narration '{v['text']}' talks over speech in {s['clip']} {x:.1f}-{y:.1f}s ('{said[:20]}')")
        if errs: raise EditError('\n  '.join([''] + errs))

    def save(self, path):
        self._final_checks()
        json.dump(self.e, open(path, 'w'), ensure_ascii=False, indent=1)
        tot = sum(self.dur(i) for i in range(self.n))
        for w in sorted(set(self.warnings)): print('  warning:', w)
        print(f'{path}: {self.n} shots, {tot / 60:.1f} min, {len(self.e["subtitles"])} subtitles, {len(self.e["vo"])} narration lines')
        return tot
