"""What did the user change in Jianying? Compare their exported film with our render.

usage: python3 compare_cut.py <our render.mp4> <their export.mp4> [<project dir> [first_clip last_clip]]

Jianying encrypts a draft on save, so their edits can only be read back from the video they export. This prints:
  * stretches where the picture differs from our render (subtitle area cropped off, so text edits do not count),
    and - with a project dir - which source clip and second each replaced shot comes from (matched on the 360p
    proxies; grading does not get in the way, matches score 0.96+)
  * stretches where the subtitle strip differs while the picture is the same (text / style edits)
  * black stretches, loudness (LUFS) and true peak of both
Their own speech (narration they recorded) is not listed here: run transcribe on the export for that.
Fingerprint: 4 fps, 48x20 grey, mean/variance normalised; correlation > 0.95 = same frame.
"""
import glob, os, re, subprocess, sys
import numpy as np

FF = '/opt/homebrew/bin/ffmpeg'; FPS, W, H = 4, 48, 20

def prints(path, vf):
    raw = subprocess.run([FF, '-nostdin', '-loglevel', 'error', '-hwaccel', 'videotoolbox', '-i', path, '-vf', vf,
                          '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8)

def picture(path): return prints(path, f'fps={FPS},crop=iw:ih*0.78:0:0,scale={W}:{H},format=gray').reshape(-1, W * H).astype(np.float32)
def strip(path): return prints(path, f'fps={FPS},crop=iw*0.6:ih*0.09:iw*0.2:ih*0.88,scale=96:8,format=gray').reshape(-1, 768).astype(np.float32)

def norm(a):
    sd = a.std(1, keepdims=True)
    return (a - a.mean(1, keepdims=True)) / np.maximum(sd, 1e-3)

def tc(f): t = f / FPS; return f'{int(t // 60)}:{t % 60:04.1f}'

def runs(flags, min_len=2):
    out, s = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and s is None: s = i
        if not on and s is not None:
            if i - s >= min_len: out.append((s, i))
            s = None
    return out

def loudness(path):
    err = subprocess.run([FF, '-nostdin', '-hide_banner', '-i', path, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    i = re.findall(r'I:\s+(-?[\d.]+) LUFS', err); p = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', err)
    return (float(i[-1]) if i else None, float(p[-1]) if p else None)

def main():
    ours, theirs = sys.argv[1:3]
    project = sys.argv[3] if len(sys.argv) > 3 else None
    lo, hi = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else (None, None)
    O, T = picture(ours), picture(theirs); On, Tn = norm(O), norm(T)
    with np.errstate(all='ignore'): C = np.nan_to_num(Tn @ On.T / (W * H))
    # follow one offset as long as it stays nearly as good as the best match (cuts inside a run stay a run)
    best, score = C.argmax(1), C.max(1); off = np.zeros(len(T), int); prev = None
    for i in range(len(T)):
        j = best[i]
        if prev is not None and 0 <= i + prev < len(O) and C[i, i + prev] > score[i] - 0.03: j = i + prev
        off[i] = j - i; prev = off[i]
    match = np.array([C[i, i + off[i]] if 0 <= i + off[i] < len(O) else 0 for i in range(len(T))])
    dark = T.mean(1) < 8
    print(f'their export {len(T) / FPS:.1f} s, our render {len(O) / FPS:.1f} s')
    changed = runs((match < 0.95) & ~dark, min_len=6)      # shorter dips are dissolves / leaks rendered differently
    blips = len(runs((match < 0.95) & ~dark)) - len(changed)
    print(f'\npicture differs from our render ({len(changed)} stretches of 1.5 s or more; {blips} shorter blips at transitions not listed):')
    src = []
    if project:
        files = sorted(glob.glob(os.path.join(project, 'ingest', 'proxy', '*.mp4')))
        files = [f for f in files if (lo is None or os.path.basename(f)[:-4] >= lo) and (hi is None or os.path.basename(f)[:-4] <= hi)]
        print(f'  (matching against {len(files)} proxies)')
        S = []
        for f in files:
            a = picture(f); S.append(a); src += [(os.path.basename(f)[:-4], k / FPS) for k in range(len(a))]
        Sn = norm(np.concatenate(S)) if S else None
    for a, b in changed:
        print(f'  {tc(a)}-{tc(b)} ({(b - a) / FPS:.1f} s)  ours here: {tc(a + off[a])}')
        if src:
            prev = None
            for i in range(a, b):
                with np.errstate(all='ignore'): c = np.nan_to_num(Tn[i] @ Sn.T / (W * H))
                j = int(c.argmax()); clip, t = src[j]; key = (clip, round(t - i / FPS))
                if key != prev: print(f'      {tc(i)}  {clip} @ {t:.2f} s  (corr {c[j]:.2f})')
                prev = key
    So, St = strip(ours), strip(theirs)
    diff = [i + off[i] < len(So) and match[i] > 0.95 and np.abs(St[i] - So[i + off[i]]).mean() > 12 for i in range(len(St))]
    print(f'\nsubtitle strip differs, picture the same (look at these; underwater noise gives false hits):')
    print('  ' + (', '.join(f'{tc(a)}-{tc(b)}' for a, b in runs(diff, min_len=4)) or 'none'))
    print('\nblack stretches in their export:', ', '.join(f'{tc(a)}-{tc(b)}' for a, b in runs(dark)) or 'none')
    for name, p in (('ours', ours), ('theirs', theirs)):
        i, pk = loudness(p); print(f'loudness {name}: {i} LUFS, true peak {pk} dBFS')

if __name__ == '__main__':
    main()
