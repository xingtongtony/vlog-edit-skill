"""Add a stock clip (an aerial the user could not shoot) to a project, colour-matched to the user's own footage.

usage: python3 add_stock.py <project> <stock file> <from> <to> --ref c360:3 c367:9 ... [--url U] [--author A]
                            [--license L] [--sources DIR]

  --ref      the user's own shots to match: clip:second, same day and similar framing (sky / sea / green in similar
             proportions). Frames are read from clips.json paths, or from --sources (local copies, cache_sources.py)
  from, to   the window of the stock clip that will be used (the colour statistics are measured there)

The match is a 3D LUT (.cube) built in Lab: brightness and contrast move toward the reference, colour moves only in
its mean and at most 15 % in spread, all at 60 % strength. Matching the whole frame fully dragged a deep-blue sea to
the reference's shallow turquoise and made green cliffs garish (Bali, 2026-10-25). The film look is still applied by
the renderer like on every other shot.

Writes <project>/stock/matched/<name>_m.mp4 (1080p, 29.97 fps) and its .cube, appends the clip to ingest/clips.json as
s01, s02, ... (existing ids never change; the same stock file keeps its id), makes the proxy, and adds a row to
<project>/stock/LICENSES.md. Then run motion.py <project> <id>. Re-running ingest.py rebuilds clips.json without them.
"""
import json, os, subprocess, sys, datetime
import cv2, numpy as np

FF, FP = '/opt/homebrew/bin/ffmpeg', '/opt/homebrew/bin/ffprobe'
STRENGTH, N = 0.6, 33

def frame(path, t, w=960):
    raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.2f}', '-i', path, '-frames:v', '1', '-vf', f'scale={w}:-2',
                          '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, w, 3)

def stats(imgs):
    px = np.concatenate([cv2.cvtColor(i.astype(np.float32) / 255, cv2.COLOR_BGR2Lab).reshape(-1, 3)[::7] for i in imgs])
    return px.mean(0), px.std(0) + 1e-6

def write_lut(path, sm, ss, rm, rs):
    ratio = rs / ss; ratio[1:] = np.clip(ratio[1:], 0.85, 1.15)
    g = np.linspace(0, 1, N, dtype=np.float32)
    bb, gg, rr = np.meshgrid(g, g, g, indexing='ij')            # .cube order: red fastest, then green, then blue
    rgb = np.stack([rr, gg, bb], -1).reshape(-1, 1, 3)
    L = cv2.cvtColor(rgb[..., ::-1].copy(), cv2.COLOR_BGR2Lab).reshape(-1, 3)
    M = L + ((L - sm) * ratio + rm - L) * STRENGTH
    o = np.clip(cv2.cvtColor(M.reshape(-1, 1, 3).astype(np.float32), cv2.COLOR_Lab2BGR).reshape(-1, 3)[:, ::-1], 0, 1)
    with open(path, 'w') as f:
        f.write(f'LUT_3D_SIZE {N}\n')
        for r, gr, bl in o: f.write(f'{r:.5f} {gr:.5f} {bl:.5f}\n')

def opt(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default

def main():
    project, src, a, b = sys.argv[1], os.path.abspath(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    refs = []
    if '--ref' in sys.argv:
        for x in sys.argv[sys.argv.index('--ref') + 1:]:
            if x.startswith('--'): break
            refs.append(x)
    if not refs: sys.exit('--ref: give the user\'s own shots to match (clip:second)')
    ing = os.path.join(project, 'ingest'); cp = os.path.join(ing, 'clips.json')
    clips = json.load(open(cp)); by = {c['id']: c for c in clips}
    local = opt('--sources')
    def path(c):
        p = os.path.join(os.path.expanduser(local), os.path.basename(c['path'])) if local else ''
        return p if p and os.path.exists(p) else c['path']
    w, h = map(int, subprocess.run([FP, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                                    '-of', 'csv=p=0', src], capture_output=True, text=True).stdout.strip().split(','))
    if abs(w / h - 16 / 9) > 0.01: sys.exit(f'{src} is {w}x{h}: only 16:9 landscape stock fits the film')

    rm, rs = stats([frame(path(by[r.split(':')[0]]), float(r.split(':')[1])) for r in refs])
    sm, ss = stats([frame(src, t) for t in np.linspace(a, b, 8)])
    name = os.path.splitext(os.path.basename(src))[0]
    out_dir = os.path.join(project, 'stock', 'matched'); os.makedirs(out_dir, exist_ok=True)
    lut, out = os.path.join(out_dir, f'lut_{name}.cube'), os.path.join(out_dir, f'{name}_m.mp4')
    write_lut(lut, sm, ss, rm, rs)
    subprocess.run([FF, '-v', 'error', '-y', '-i', src, '-vf', f'fps=30000/1001,scale=1920:1080:flags=lanczos,lut3d=file={lut},format=yuv420p',
                    '-an', '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', out], check=True)
    dur = float(subprocess.run([FP, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out],
                               capture_output=True, text=True).stdout)

    old = next((c for c in clips if c.get('stock', {}).get('file') == src), None)
    sid = old['id'] if old else f"s{1 + max([int(c['id'][1:]) for c in clips if c['id'].startswith('s')] or [0]):02d}"
    rec = {'id': sid, 'name': name, 'path': os.path.abspath(out), 'kind': 'drone', 'audio': False, 'duration': dur,
           'width': 1920, 'height': 1080, 'fps': '30000/1001', 'creation': None, 'loc': None,
           'time': by[refs[0].split(':')[0]].get('time', ''),     # it stands in for the user's own visit
           'stock': {'file': src, 'window': [a, b], 'url': opt('--url', ''), 'author': opt('--author', ''),
                     'license': opt('--license', ''), 'lut': os.path.abspath(lut), 'ref': refs}}
    clips = [c for c in clips if c['id'] != sid] + [rec]
    json.dump(clips, open(cp, 'w'), ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(ing, 'proxy'), exist_ok=True)
    subprocess.run([FF, '-v', 'error', '-y', '-i', out, '-vf', 'scale=-2:360', '-c:v', 'libx264', '-preset', 'fast', '-crf', '28',
                    os.path.join(ing, 'proxy', f'{sid}.mp4')], check=True)

    lic = os.path.join(project, 'stock', 'LICENSES.md')
    if not os.path.exists(lic):
        open(lic, 'w').write('# 图库素材授权\n\n| 编号 | 文件 | 作者 | 页面 | 授权 | 下载 | 用到 |\n|---|---|---|---|---|---|---|\n')
    row = f"| {sid} | {os.path.basename(src)} | {rec['stock']['author']} | {rec['stock']['url']} | {rec['stock']['license']} | {datetime.date.today()} | {a}–{b} s |\n"
    if os.path.basename(src) not in open(lic).read(): open(lic, 'a').write(row)
    print(f"{sid}: {out} ({dur:.1f}s)  L {sm[0]:.0f}/{ss[0]:.0f} a {sm[1]:+.0f} b {sm[2]:+.0f} -> L {rm[0]:.0f}/{rs[0]:.0f} a {rm[1]:+.0f} b {rm[2]:+.0f}")
    print(f"next: ~/.venvs/tts/bin/python scripts/motion.py {project} {sid}")

if __name__ == '__main__':
    main()
