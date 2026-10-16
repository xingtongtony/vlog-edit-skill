"""Find the best-looking steady windows across many clips and lay them out on boards to choose from.

usage: ~/.venvs/tts/bin/python candidates.py <project> <out_prefix> <clip> [<clip> ...] [--dur 4] [--top 48]
For every clip: windows that pass the motion check (scripts/motion.py), scored by what is in the picture -
colourfulness (Hasler & Suesstrunk) and fine detail (mean gradient), measured on the proxy at 2 fps. Empty blue water
scores low on both; a reef, a fish school or a face in good light scores high. Writes <out_prefix>N.jpg boards (12 windows
each, 3 frames per window) and prints the ranking, so Claude can look before choosing - the score only sorts, it
does not decide.
"""
import json, os, subprocess, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motion
FF = '/opt/homebrew/bin/ffmpeg'

def frames(proxy, w=160, fps=2):
    raw = subprocess.run([FF, '-v', 'error', '-i', proxy, '-vf', f'fps={fps},scale={w}:90', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 90, w, 3).astype(np.float32)

def score(f):
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colour = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    grey = f.mean(-1); detail = np.abs(np.diff(grey, axis=0)).mean() + np.abs(np.diff(grey, axis=1)).mean()
    return colour, detail

def main():
    args = sys.argv[1:]; dur = float(args[args.index('--dur') + 1]) if '--dur' in args else 4.0
    top = int(args[args.index('--top') + 1]) if '--top' in args else 48
    flags = {a for i, a in enumerate(args) if a.startswith('--')} | {args[i + 1] for i, a in enumerate(args) if a in ('--dur', '--top')}
    project, prefix, clips = args[0], args[1], [a for a in args[2:] if a not in flags]
    ing = os.path.join(project, 'ingest'); mo = json.load(open(os.path.join(ing, 'motion.json')))
    kinds = {c['id']: c['kind'] for c in json.load(open(os.path.join(ing, 'clips.json')))}
    cands = []
    for c in clips:
        if c not in mo: continue
        fr = frames(os.path.join(ing, 'proxy', f'{c}.mp4')); per = [score(x) for x in fr]
        starts = motion.best_windows(mo[c], dur, drone=kinds[c] == 'drone', n=999)
        taken = []
        for s in sorted(starts, key=lambda s: -np.mean([sum(p) for p in per[int(s * 2):int((s + dur) * 2)]] or [0])):
            if any(abs(s - t) < dur for t in taken): continue        # non-overlapping windows per clip
            w = per[int(s * 2):int((s + dur) * 2)]
            if not w: continue
            cands.append((float(np.mean([p[0] for p in w])), float(np.mean([p[1] for p in w])), c, s)); taken.append(s)
    cands.sort(key=lambda x: -(x[0] / 30 + x[1] / 12))
    cands = cands[:top]
    from PIL import Image, ImageDraw
    for page in range(0, len(cands), 12):
        rows = cands[page:page + 12]; W, H = 300, 169
        sheet = Image.new('RGB', (3 * W, len(rows) * (H + 18)), 'white')
        for k, (col, det, c, s) in enumerate(rows):
            for j, t in enumerate((s + 0.3, s + dur / 2, s + dur - 0.3)):
                raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.2f}', '-i', os.path.join(ing, 'proxy', f'{c}.mp4'), '-frames:v', '1',
                                      '-vf', f'scale={W}:{H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
                if len(raw) == W * H * 3: sheet.paste(Image.frombytes('RGB', (W, H), raw), (j * W, k * (H + 18) + 18))
            ImageDraw.Draw(sheet).text((4, k * (H + 18) + 3), f'#{page + k} {c} {s:.0f}-{s + dur:.0f}s  colour {col:.0f} detail {det:.1f}', fill='black')
        sheet.save(f'{prefix}{page // 12 + 1}.jpg', quality=85)
    for i, (col, det, c, s) in enumerate(cands): print(f'#{i:2d} {c} {s:5.0f}  colour {col:5.1f} detail {det:4.1f}')

if __name__ == '__main__':
    main()
