"""Character-level alignment of the user's narration takes, to cut out every stumble (used by narration_cut.py).

Whisper cleans up what it hears: "去北岸的小，去北岸的小镇" comes out as one clean phrase, "树，树长起来" as "树长起来",
and its word times are 0.1-0.3 s off - Bali v7 went out with eleven repeats inside phrases and a stray syllable of the
neighbouring phrase at a dozen joins. A CTC acoustic model (wav2vec2 XLSR-53, Chinese) writes down what was said,
character by character, at 20 ms:

  * realize(): the take of one phrase is transcribed as heard and aligned to its subtitle by sound (pinyin, so 岸/安
    and 石/实 are one syllable) with affine-gap dynamic programming. What the subtitle does not say is cut out:
    syllables of the neighbouring phrase at either end, a false start or a word said twice (the last complete reading
    is kept; a restart from the top keeps the whole second reading), fillers (嗯 呃 啊). Kept, and reported for a
    listen: an extra syllable with no stop around it (one drawn-out syllable heard as two), extra syllables where a
    subtitle syllable is missing (卧 heard as 耶或), the last syllable heard as something else (黾 as 莹) or split in
    two before Whisper's end of the phrase (鳅 as 一久).
  * every cut falls on the quietest 20 ms between two characters, so a syllable is never clipped;
  * a pause inside a phrase where the subtitle has no punctuation is a stall: longer than 0.25 s, it becomes 0.12 s.

Model: jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn (Apache-2.0, 1.3 GB) in ~/.cache/vlog-models/wav2vec2-zh;
runs in ~/.venvs/tts (torch + transformers). Latin words (Kelingking, Finns) are not in its vocabulary; they match
anything heard in their place.
"""
import os, re
import numpy as np

MODEL = os.path.expanduser('~/.cache/vlog-models/wav2vec2-zh')
SR = 16000
HOP = 0.02                                   # one CTC frame
FILLERS = set('嗯呃啊额哦唔')
PUNCT = set('，。！？；：、,.!?;:…—')
CN = '零一二三四五六七八九'

def numerals(s):
    """Arabic numbers as they are read (2 月 15 日 -> 二月十五日), so the subtitle matches the speech."""
    def say(m):
        n = int(m.group())
        if n < 10: return CN[n]
        if n < 100: return ('' if n // 10 == 1 else CN[n // 10]) + '十' + (CN[n % 10] if n % 10 else '')
        return ''.join(CN[int(d)] for d in m.group())
    return re.sub(r'\d+', say, s)

class CTC:
    def __init__(self):
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        self.torch = torch
        self.proc = Wav2Vec2Processor.from_pretrained(MODEL)
        self.model = Wav2Vec2ForCTC.from_pretrained(MODEL).eval()
        self.dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.model.to(self.dev)
        vocab = self.proc.tokenizer.get_vocab()
        self.id2c = {i: c for c, i in vocab.items()}
        self.blank = self.proc.tokenizer.pad_token_id

    def heard(self, x):
        """Greedy CTC transcript of 16 kHz audio: [(char, first frame, last frame)]."""
        torch = self.torch
        inp = self.proc(x, sampling_rate=SR, return_tensors='pt').input_values.to(self.dev)
        with torch.no_grad(): ids = self.model(inp).logits[0].argmax(-1).cpu().numpy()
        out, prev = [], None
        for f, i in enumerate(ids):
            if i != prev and i != self.blank:
                c = self.id2c.get(int(i), '')
                if c and c not in ('|', '<unk>', '<s>', '</s>'): out.append([c, f, f])
            elif i == prev and i != self.blank and out: out[-1][2] = f
            prev = i
        return [tuple(o) for o in out]

def key(c):
    """What a character sounds like, loosely: the recogniser writes 安 for 岸 and 实 for 石, and the user's z/zh, s/sh,
    an/ang are not told apart either. Latin wildcards match nothing exactly."""
    if c == '*': return '*'
    from pypinyin import lazy_pinyin
    k = lazy_pinyin(c)[0]
    k = re.sub(r'^([zcs])h', r'\1', k); k = re.sub(r'(an|en|in)g$', r'\1', k)
    return k

def near(a, b):
    """Two syllables the recogniser might write for one another (树 / 受: su, sou)."""
    if a == b: return True
    if '*' in (a, b) or not a or not b: return False
    ia, ib = re.match(r'^[^aeiouv]*', a).group(), re.match(r'^[^aeiouv]*', b).group()
    fa, fb = a[len(ia):], b[len(ib):]
    return ia == ib and bool(fa) and bool(fb) and (fa in fb or fb in fa)

def syllables(word):
    return max(1, len(re.findall(r'[aeiouy]+', word.lower())))

def energy(x):
    n = int(SR * HOP); x = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt(np.mean(x ** 2, axis=1)) + 1e-7)

def quietest(env, a, b):
    """Frame of least energy in [a, b] (frames); a when the range is empty."""
    a, b = max(0, a), min(len(env) - 1, b)
    return a if b <= a else a + int(np.argmin(env[a:b + 1]))

def realize(ctc, x, text, t0=0.0, until=None):
    """How phrase `text` was said in audio x (16 kHz, starting at source time t0): {keep: [(a, b)] source seconds,
    chars: [(i, t)] subtitle character i spoken at source time t, removed: [(what, a, b, why)], heard, check: [...]}.
    `until`: where the phrase ends by Whisper's reckoning (source time); syllables heard before it, run on from the last
    one kept, are the phrase's own last syllable split in two (鳅 heard as 一久, 芯儿 as 些儿), not the next phrase."""
    env = energy(x); thr = max(np.percentile(env, 10) + 12, -50)
    H = ctc.heard(x)
    # the subtitle as characters, Latin words as one wildcard each
    toks, idx, words = [], [], {}                 # idx: subtitle char index of each token; words: Latin words
    for m in re.finditer(r'[A-Za-z]+|\d+|[^\s' + re.escape(''.join(PUNCT)) + r'"“”‘’]', text):
        w = m.group()
        if re.fullmatch(r'[A-Za-z]+', w): toks.append('*'); idx.append(m.start()); words[len(toks) - 1] = w
        elif w.isdigit():
            for c in numerals(w): toks.append(c); idx.append(m.start())
        else: toks.append(w); idx.append(m.start())
    heard = ''.join(c for c, _, _ in H)
    # Align the subtitle to what was heard, by sound (dynamic programming with affine gaps). Syllables heard before and
    # after the phrase cost nothing (the neighbours); a run of extra syllables inside it costs 1 + 0.1 per syllable, so
    # a long false start is cut rather than the phrase's first word dropped; a subtitle syllable not heard costs 1; on
    # a tie the later reading wins - a stretch said twice keeps its last complete reading. Longest-common-block
    # matching paired 得 with a 的 of the next phrase (挪得呢 heard as 拿度的…跟门口的) and put a line 0.6 s late.
    Tk, Vk = [key(c) for c in toks], [key(c) for c, _, _ in H]
    n, m = len(Tk), len(Vk)
    sc = lambda a, b: 5 if a == '*' else 20 if a == b else 10 if near(a, b) else -12     # tenths, in integers: float
    NEG = -10 ** 9                                                                         # error broke ties wrongly
    M = np.full((n + 1, m + 1), NEG, np.int64); X = np.full((n + 1, m + 1), NEG, np.int64); Y = np.full((n + 1, m + 1), NEG, np.int64)
    bM = np.zeros((n + 1, m + 1), np.int8); bX = np.zeros((n + 1, m + 1), np.int8); bY = np.zeros((n + 1, m + 1), np.int8)
    M[0, :] = 0                                    # start anywhere: what comes before is the neighbour
    # the last syllable of a phrase is often heard as another one (黾 as 莹, 浪 as 了, 券 as 爪): right next to the
    # syllable before it, it is that syllable misheard, not the next phrase - dropping it clipped eleven endings
    gap = lambda a, b: H[b][1] - H[a][2] if 0 <= a < m and 0 <= b < m else 99
    edge = lambda i, j: (i == n and gap(j - 2, j - 1) < 15) or (i == 1 and gap(j - 1, j) < 15)
    for i in range(1, n + 1):
        wild = Tk[i - 1] == '*'                   # a Latin word takes whatever was heard in its place
        for j in range(m + 1):
            Y[i, j], bY[i, j] = max((M[i - 1, j] - (0 if wild else 10), 0), (Y[i - 1, j] - (0 if wild else 10), 2),
                                    (X[i - 1, j] - (0 if wild else 10), 1), key=lambda t: t[0])
            if not j: continue
            M[i, j], bM[i, j] = max((M[i - 1, j - 1], 0), (Y[i - 1, j - 1], 2), (X[i - 1, j - 1], 1), key=lambda t: t[0])
            v = sc(Tk[i - 1], Vk[j - 1])
            M[i, j] += -6 if v < 0 and edge(i, j) else v
            op, ext = (-4, -4) if wild else (10, 1)
            X[i, j], bX[i, j] = max((M[i, j - 1] - op, 0), (X[i, j - 1] - ext, 1), (Y[i, j - 1] - op, 2), key=lambda t: t[0])
    ends = [max((M[n, j], j, 0), (Y[n, j], j, 2), (X[n, j] if n and Tk[-1] == '*' else NEG, j, 1)) for j in range(m + 1)]   # a Latin
                                                                                                    # word may still be taking syllables
    _, j, st = max(ends, key=lambda t: (t[0], t[1]))
    keep_v, removed, check, tmap = [False] * m, [], [], {}       # tmap: heard index -> subtitle token index
    i = n
    while i > 0:
        if st == 0: keep_v[j - 1] = True; tmap[j - 1] = i - 1; st = bM[i, j]; i -= 1; j -= 1
        elif st == 2: st = bY[i, j]; i -= 1
        else:
            if Tk[i - 1] == '*': keep_v[j - 1] = True; tmap[j - 1] = i - 1
            st = bX[i, j]; j -= 1
    for w in (0, n - 1):                          # a Latin word at either end of the phrase: no more syllables than it has
        if n and Tk[w] == '*':
            got = sorted(q for q, t in tmap.items() if t == w and keep_v[q])
            extra = len(got) - (syllables(words[w]) + 1)
            for q in (got[:extra] if w == 0 and extra > 0 else got[len(got) - extra:] if extra > 0 else []):
                keep_v[q] = False
    if until is not None and any(keep_v):
        q = max(k for k in range(m) if keep_v[k])
        while q + 1 < m and t0 + H[q + 1][1] * HOP < until - 0.05 and gap(q, q + 1) < 15:
            keep_v[q + 1] = True; tmap[q + 1] = tmap[q]; q += 1
    slips = [q for q in range(m) if keep_v[q] and q in tmap and Tk[tmap[q]] != '*' and sc(Tk[tmap[q]], Vk[q]) < 0]
    if len(slips) > max(2, 0.3 * n): check.append(f"heard as '{''.join(H[q][0] for q in range(m) if keep_v[q])}'")
    # what is left over (inserted) is a stumble, a filler or a neighbour's syllable - or a slip to listen to
    first = next((k for k in range(len(H)) if keep_v[k]), None); last = next((k for k in range(len(H) - 1, -1, -1) if keep_v[k]), None)
    if first is None: return None
    k = 0
    while k < len(H):
        if keep_v[k]: k += 1; continue
        j = k
        while j < len(H) and not keep_v[j]: j += 1
        ins = ''.join(c for c, _, _ in H[k:j])
        if k < first or j > last: why = 'neighbour'
        elif tmap[j] - tmap[k - 1] > 1: why = None         # subtitle syllables are missing right here: this is how
                                                           # they were heard (卧 as 耶或), not something extra
        elif all(c in FILLERS for c in ins): why = 'filler'
        elif j - k >= 2: why = 'stumble'         # two or more syllables the subtitle does not have: a false start
        elif (near(key(ins), key(H[j][0])) or near(key(ins), key(H[k - 1][0]))) and \
                (env[H[k - 1][2] + 1: H[j][1]] <= thr).sum() >= 4: why = 'repeat'   # 树，树(受)长起来 - with a stop
        else: why = None                          # between them; without one it is a drawn-out syllable (所以——)
        if why == 'stumble':                       # a restart from the top of the phrase: keep the whole second reading
            run = [q for q in range(first, k) if keep_v[q]]
            for m in range(k, j - 1):
                tail = H[m:j]
                if len(tail) > len(run) or not near(key(tail[0][0]), key(H[run[0]][0])): continue   # starts over
                same = sum(near(key(a[0]), key(H[b][0])) for a, b in zip(tail, run))
                if same >= max(2, 0.6 * len(tail)):
                    for i, q in enumerate(run[:len(tail)]): keep_v[m + i] = True; tmap[m + i] = tmap[q]; keep_v[q] = False
                    removed.append([''.join(H[q][0] for q in run[:len(tail)]) + ''.join(c for c, _, _ in H[k:m]), run[0], m, 'restart'])
                    why = 'done'; break
        if why == 'repeat' and near(key(ins), key(H[k - 1][0])) and keep_v[k - 1]:     # keep the second saying: the
            keep_v[k] = True; tmap[k] = tmap[k - 1]; keep_v[k - 1] = False                # first was the stutter
            removed.append([H[k - 1][0], k - 1, k, 'repeat']); k = j; continue
        if why == 'done': k = j; continue
        if why is None:
            check.append(f"extra '{ins}' kept (listen)")
            for q in range(k, j): keep_v[q] = True; tmap[q] = tmap.get(j, tmap.get(k - 1, 0))
        else:
            removed.append([ins, k, j, why])
        k = j
    # source intervals: cut at the quietest frame between a kept character and a removed one
    removed.sort(key=lambda r: r[1])
    kept = [q for q in range(len(H)) if keep_v[q]]
    runs, cur = [], [kept[0]]
    for q in kept[1:]:
        if q == cur[-1] + 1: cur.append(q)
        else: runs.append(cur); cur = [q]
    runs.append(cur)
    def onset(q):                                  # first voiced frame of character q, not before the one heard before it
        lim, f = (H[q - 1][2] + 1 if q else max(0, H[q][1] - 25)), H[q][1]
        while f > lim and env[f - 1] > thr: f -= 1
        return f
    def offset(q):
        lim, f = (H[q + 1][1] - 1 if q < len(H) - 1 else min(len(env) - 1, H[q][2] + 35)), H[q][2]
        while f < lim and env[f + 1] > thr: f += 1
        return f
    keep = []
    for r in runs:                                 # 100 ms of air before the voice, 120 ms after; never past the
        q0, q1 = r[0], r[-1]                       # quietest frame between a kept character and a cut one
        fa = onset(q0) - 5
        if q0 > 0: fa = max(fa, quietest(env, H[q0 - 1][2] + 1, H[q0][1] - 1))
        fb = offset(q1) + 6
        if q1 < len(H) - 1: fb = min(fb, quietest(env, H[q1][2] + 1, H[q1 + 1][1] - 1))
        keep.append([t0 + max(0, fa) * HOP, t0 + (min(len(env) - 1, fb) + 1) * HOP])
    for rm in removed:                             # report in source time
        rm[1], rm[2] = round(t0 + H[rm[1]][1] * HOP, 2), round(t0 + H[rm[2] - 1][2] * HOP + HOP, 2)
    chars = sorted({tmap[q]: t0 + H[q][1] * HOP for q in reversed(kept)}.items())   # first heard frame of each token
    chars = [(idx[i], t) for i, t in chars]
    return {'keep': keep, 'chars': chars, 'wild': (toks[0] == '*', toks[-1] == '*'), 'removed': [tuple(r) for r in removed], 'heard': heard, 'check': check,
            'env': env, 'thr': thr, 'spikes': [(t0 + H[q][1] * HOP, t0 + H[q][2] * HOP + HOP, tmap.get(q)) for q in kept], 'toks': toks}
