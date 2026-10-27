"""Cut the user's narration recordings into one clean audio file per paragraph, every stumble taken out, with the time
of every character for the subtitles.

usage: ~/.venvs/tts/bin/python narration_cut.py <phrases.json> <picks.py> <out dir> <recording> [<recording> ...]

phrases.json  every phrase of the recordings, numbered (narration_takes.py: file index, start, end, text)
picks.py      PICKS = {paragraph: [(phrase, subtitle text for it[, opts]), ...]} - the take chosen for every phrase;
              opts: {'from': s, 'to': s} look only at that part of the recording (absolute seconds)
recordings    the audio files, in the same order as the phrase file indices

Per picked phrase (narration_align.realize, a CTC model that writes down what was said, character by character):
  * the audio around the phrase is transcribed as heard and matched to its subtitle; a false start, a word said twice,
    a whole phrase read twice (the last complete reading stays), fillers and the neighbouring phrase's syllables are cut
    out, each cut on the quietest 20 ms between two characters. Whisper hid these: it hears "去北岸的小，去北岸的小镇"
    as one clean phrase, and its word times put a syllable of the next phrase onto this one (Bali v7: eleven repeats and
    a dozen stray syllables went out to the user);
  * a pause where the subtitle has no punctuation is a stall: over 0.25 s it becomes 0.12 s; at punctuation a pause
    over 0.5 s becomes 0.3 s.
Per paragraph: the next phrase of the same reading continues the audio as it was said (no cut, no inserted silence);
a phrase from another take is spliced in after 0.3 s, at least 0.4 s after the end of a sentence; 10 ms fades at every
cut. The recording is cleaned once (80 Hz high-pass, light FFT denoise) and every paragraph is levelled to the same
speech loudness, peaks held under -2 dBFS. The finished paragraph is transcribed again and anything heard beyond the
subtitle is listed, with every cut and every extra syllable that was kept because it might be a recognition slip.
Writes <out>/pNN.wav and <out>/narration.json: {paragraph: {wav, dur, text, pieces: [[subtitle text, t0, t1], ...],
chars: [[start, end], ...], heard}}; `chars` times every character of the text (punctuation and spaces left out).
"""
import difflib, json, os, re, runpy, subprocess, sys, tempfile
import numpy as np
from scipy.io import wavfile

FF = '/opt/homebrew/bin/ffmpeg'
SR = 48000
FRAME = int(SR * 0.02)
SPEECH_RMS = 10 ** (-20 / 20)

CLEAN = re.compile(r'[\s，。！？；：、,.!?;:"“”‘’…—]')          # the same set as edl_builder._rec_lines

def load_clean(path, tmp):
    out = os.path.join(tmp, os.path.basename(path) + '.wav')
    subprocess.run([FF, '-v', 'error', '-y', '-i', path, '-ac', '1', '-ar', str(SR), '-af', 'highpass=f=80,afftdn=nr=10:nf=-45',
                    '-c:a', 'pcm_s16le', out], check=True)
    sr, x = wavfile.read(out); x = x.astype(np.float32) / 32768
    env = 20 * np.log10(np.sqrt(np.mean(x[: len(x) // FRAME * FRAME].reshape(-1, FRAME) ** 2, axis=1)) + 1e-7)
    floor = np.percentile(env, 10)
    return x, env, max(floor + 12, -55)

def fade(y, ms=10, start=True, end=True):
    n = min(len(y) // 2, int(SR * ms / 1000)); r = np.linspace(0, 1, n, dtype=np.float32)
    y = y.copy()
    if start: y[:n] *= r
    if end: y[-n:] *= r[::-1]
    return y

def silences(env, thr, fa, fb, least):
    """Silent runs (frames) inside [fa, fb) at least `least` seconds long."""
    q = env[fa:fb] <= thr; out, i = [], 0
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]: j += 1
            if (j - i) * 0.02 >= least: out.append((fa + i, fa + j))
            i = j
        else: i += 1
    return out

def main():
    import scipy.signal as ss
    import narration_align as na
    ph = json.load(open(sys.argv[1])); picks = runpy.run_path(sys.argv[2])['PICKS']; out = sys.argv[3]; recs = sys.argv[4:]
    os.makedirs(out, exist_ok=True); result = {}; warn = []; ctc = na.CTC()
    with tempfile.TemporaryDirectory() as tmp:
        src = [load_clean(r, tmp) for r in recs]
        x16 = [ss.resample_poly(x, 1, 3).astype(np.float32) for x, _, _ in src]
        for para, items in sorted(picks.items()):
            done = []
            for item in items:                    # every pick realised character by character
                n, text = item[0], item[1]; o = item[2] if len(item) > 2 else {}
                p = ph[n]; f = p['file']; dur = len(x16[f]) / 16000
                a0, b0 = max(0.0, o.get('from', p['start'] - 0.6)), min(dur, o.get('to', p['end'] + 0.8))
                if 'from' not in o and n > 0 and ph[n - 1]['file'] == f: a0 = max(a0, ph[n - 1]['end'] - 0.3)   # not deep
                if 'to' not in o and n + 1 < len(ph) and ph[n + 1]['file'] == f: b0 = min(b0, ph[n + 1]['start'] + 0.3)   # into the neighbours
                r = na.realize(ctc, x16[f][int(a0 * 16000): int(b0 * 16000)], text, a0, until=o.get('to', p['end']))
                if r is None:
                    warn.append(f'p{para:02d} phrase {n} "{text}": nothing recognised, cut on Whisper times')
                    r = {'keep': [[p['start'] - 0.08, p['end'] + 0.15]], 'chars': [], 'removed': [], 'check': []}
                for what, ra, rb, why in r['removed']:
                    if why != 'neighbour' or len(what) > 2: warn.append(f'p{para:02d} "{text}": cut {why} "{what}" ({ra:.2f}-{rb:.2f}s)')
                warn += [f'p{para:02d} "{text}": {c}' for c in r['check']]
                done.append({'n': n, 'file': f, 'text': text, 'keep': [list(k) for k in r['keep']], 'chars': r['chars'],
                             'wild': r.get('wild', (False, False))})
            # joins: the next phrase of the same reading continues the audio as it was said
            for k in range(1, len(done)):
                q, d = done[k - 1], done[k]
                if q['file'] == d['file'] and d['n'] == q['n'] + 1 and d['keep'][0][0] - q['keep'][-1][1] < 0.35:
                    if d['keep'][0][0] < q['keep'][-1][1]:      # never twice; a Latin word (Kelingking) swallows what
                        if q['wild'][1] and not d['wild'][0]: q['keep'][-1][1] = d['keep'][0][0]    # is around it, so the
                        else: d['keep'][0][0] = q['keep'][-1][1]                                    # Chinese side decides
                    d['join'] = 'flow'
                else: d['join'] = 'apart' if q['file'] == d['file'] and d['n'] == q['n'] + 1 else 'splice'
            chunks, tmap, pieces, t = [], [], [], 0.0      # tmap: (source file, a, b, output t) for every kept stretch
            for k, d in enumerate(done):
                x, env, thr = src[d['file']]
                if k:
                    prev = done[k - 1]; gap = 0.0
                    if d['join'] == 'flow': gap = max(0.0, d['keep'][0][0] - prev['keep'][-1][1])
                    elif d['join'] == 'apart': gap = 0.3
                    else: gap = 0.3
                    if d['join'] != 'flow' and re.search(r'[。？！：]$', prev['text'].strip()): gap = max(gap, 0.4)
                    if d['join'] == 'flow' and gap: chunks.append(x[int(prev['keep'][-1][1] * SR): int(d['keep'][0][0] * SR)]); tmap.append((d['file'], prev['keep'][-1][1], d['keep'][0][0], t)); t += gap
                    elif gap: chunks.append(np.zeros(int(gap * SR), np.float32)); t += gap
                p0 = t
                ctimes = sorted(d['chars'], key=lambda c: c[1])
                for j, (a, b) in enumerate(d['keep']):
                    if j: chunks.append(np.zeros(int(0.1 * SR), np.float32)); t += 0.1     # a stumble cut out of the middle
                    fa, fb = int(a / 0.02), int(b / 0.02); cut = []
                    for sa, sb in silences(env, thr, fa, fb, 0.25):    # pauses: at punctuation a breath, inside a phrase a stall
                        mid = (sa + sb) / 2 * 0.02
                        before = [c for c in ctimes if c[1] < mid]; after = [c for c in ctimes if c[1] >= mid]
                        brk = not before or not after or any(ch in na.PUNCT for ch in d['text'][before[-1][0]:after[0][0]])
                        longest, keep = (0.5, 0.3) if brk else (0.25, 0.12)
                        if (sb - sa) * 0.02 > longest:
                            h = int(keep / 2 / 0.02); cut.append((sa + h, sb - h))
                    pos = fa
                    for ca, cb in cut + [(fb, fb)]:
                        if ca > pos:
                            seg = x[pos * FRAME: ca * FRAME]
                            fin = not (j == 0 and pos == fa and k and d['join'] == 'flow')
                            fout = not (j == len(d['keep']) - 1 and ca == fb and k + 1 < len(done) and done[k + 1]['join'] == 'flow')
                            chunks.append(fade(seg, start=fin, end=fout)); tmap.append((d['file'], pos * 0.02, ca * 0.02, t)); t += len(seg) / SR
                        pos = cb
                pieces.append([d['text'], round(p0, 3), round(t, 3)])
                d['out'] = (p0, t)
            def at(f, s):                            # source time -> output time
                best = None
                for ff, a, b, o in tmap:
                    if ff != f: continue
                    if a <= s <= b: return o + s - a
                    if s < a and (best is None or a < best[0]): best = (a, o)
                return best[1] if best else t
            # every character of the subtitle, timed where it was said
            text = ''.join(d['text'] for d in done); chars = []
            for d in done:
                known = {i: at(d['file'], s) for i, s in d['chars']}
                idx = [i for i, ch in enumerate(d['text']) if not CLEAN.match(ch)]
                ts = [known.get(i) for i in idx]
                if ts: ts[0] = d['out'][0] + 0.1 if ts[0] is None else min(ts[0], d['out'][0] + 0.12)   # the piece was cut
                                                       # 0.1 s before its first sound; the model marks a soft F late (Finns)
                anchors = [m for m, v in enumerate(ts) if v is not None] + [len(ts)]
                for m0, m1 in zip(anchors, anchors[1:]):  # Latin letters and missed characters: spread between neighbours
                    hi = ts[m1] if m1 < len(ts) else d['out'][1]
                    for m in range(m0 + 1, m1): ts[m] = ts[m0] + (hi - ts[m0]) * (m - m0) / (m1 - m0)
                # no clamping to this piece: in a reading that runs on, the first words can sit in the piece before
                # (跟门口的 | 石像: clamped, the line came up 0.6 s late, Bali v7)
                for m in range(len(ts)): chars.append([round(ts[m], 3), round(ts[m + 1] if m + 1 < len(ts) else d['out'][1], 3)])
            for m in range(1, len(chars)): chars[m][0] = max(chars[m][0], chars[m - 1][0])
            y = np.concatenate(chunks)
            sp = y[np.abs(y) > 0.01]; g = SPEECH_RMS / (np.sqrt(np.mean(sp ** 2)) + 1e-9) if len(sp) else 1.0
            y = y * g; pk = np.max(np.abs(y)); y = y * min(1.0, 10 ** (-2 / 20) / pk) if pk > 0 else y
            wav = os.path.join(out, f'p{para:02d}.wav'); wavfile.write(wav, SR, (np.clip(y, -1, 1) * 32767).astype(np.int16))
            # check the result the same way: anything the finished paragraph would still have cut
            r2 = na.realize(ctc, ss.resample_poly(y, 1, 3).astype(np.float32), text)
            heard = r2['heard'] if r2 else ''
            left = [f'{w} "{what}" at {a:.1f}s' for what, a, b, w in (r2['removed'] if r2 else [])] + (r2['check'] if r2 else [])
            if left: warn.append(f'p{para:02d} finished paragraph still has: {left}')
            result[para] = {'wav': os.path.abspath(wav), 'dur': round(len(y) / SR, 3), 'text': text, 'pieces': pieces, 'chars': chars,
                            'heard': heard}
            print(f'p{para:02d} {len(y) / SR:5.1f}s', flush=True)
    json.dump(result, open(os.path.join(out, 'narration.json'), 'w'), ensure_ascii=False, indent=1)
    print(f'{len(result)} paragraphs, {sum(r["dur"] for r in result.values()) / 60:.1f} min of narration')
    for w in warn: print('  check:', w)

if __name__ == '__main__':
    main()
