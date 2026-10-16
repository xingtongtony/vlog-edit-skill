"""Tighten a talking-to-camera stretch: drop fillers, false starts / repeats and long pauses, using word timestamps.

usage (inspect): python3 clean_speech.py <project_dir> <clip> <from> <to>
In code: pieces(project, clip, a, b) -> [(start, end, text)], each a sub-range to keep as its own shot.

Rules
- fillers (嗯 啊 呃 额 哎 噢 that are standalone words) are cut out
- a phrase repeated back-to-back keeps only its last take ("想要上薄荷岛, 想要上薄荷岛的话" -> second)
- gaps between words longer than MAX_GAP split the speech; each piece gets PAD of air on both sides
"""
import json, os, re, sys

FILLERS = {'嗯', '啊', '呃', '额', '哎', '噢', '哦', '唔', 'um', 'uh', 'Um', 'Uh'}
MAX_GAP, PAD, MIN_PIECE = 0.35, 0.08, 0.5

def words(project, clip):
    segs = json.load(open(os.path.join(project, 'ingest', 'transcripts', f'{clip}.json')))['segments']
    out = []
    for s in segs:
        for w in s.get('words', []):
            t = w['w'].strip()
            if t: out.append({'w': t, 's': w['s'], 'e': w['e']})
    return out

def norm(t): return re.sub(r'[\W_]', '', t)

def drop_repeats(ws, max_len=8):
    """If the n words just before position i equal the n words starting at i, drop the earlier take."""
    keep = [True] * len(ws); i = 1
    while i < len(ws):
        for n in range(min(max_len, i, len(ws) - i), 1, -1):
            a = ''.join(norm(w['w']) for w in ws[i - n:i]); b = ''.join(norm(w['w']) for w in ws[i:i + n])
            if a and a == b:
                for k in range(i - n, i): keep[k] = False
                break
        i += 1
    return [w for w, k in zip(ws, keep) if k]

def pieces(project, clip, a, b):
    ws = [w for w in words(project, clip) if w['s'] >= a - 0.05 and w['e'] <= b + 0.05]
    ws = [w for w in ws if norm(w['w']) not in FILLERS and norm(w['w'])]
    ws = drop_repeats(ws)
    out, cur = [], []
    for w in ws:
        if cur and w['s'] - cur[-1]['e'] > MAX_GAP:
            out.append(cur); cur = []
        cur.append(w)
    if cur: out.append(cur)
    res = []
    for p in out:
        s, e = max(a, p[0]['s'] - PAD), min(b, p[-1]['e'] + PAD)
        if e - s >= MIN_PIECE: res.append((round(s, 2), round(e, 2), ''.join(w['w'] for w in p)))
    return res

if __name__ == '__main__':
    project, clip, a, b = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    ps = pieces(project, clip, a, b); total = sum(e - s for s, e, _ in ps)
    for s, e, t in ps: print(f'{s:7.2f}-{e:7.2f}  {t}')
    print(f'kept {total:.1f}s of {b - a:.1f}s')
