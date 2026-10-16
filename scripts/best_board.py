"""Board of the steadiest windows per clip: one frame at the middle of each best window, labelled with time and shake.
usage: python3 best_board.py <project> <out.jpg> <clip> [<clip> ...]"""
import json, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont
project, out, ids = sys.argv[1], sys.argv[2], sys.argv[3:]
ing = os.path.join(project, 'ingest'); st = json.load(open(f'{ing}/stability.json'))
F = ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', 13); W, H = 213, 120
rows = []
for cid in ids:
    best = st.get(cid, {}).get('best', [])[:6]
    im = Image.new('RGB', (W * 6, H + 16), (20, 20, 20)); d = ImageDraw.Draw(im)
    with tempfile.TemporaryDirectory() as t:
        for i, (a, b, sc) in enumerate(best):
            o = f'{t}/f.jpg'; subprocess.run(['/opt/homebrew/bin/ffmpeg', '-y', '-loglevel', 'error', '-ss', str((a + b) / 2), '-i', f'{ing}/proxy/{cid}.mp4', '-frames:v', '1', '-vf', f'scale={W}:{H}', o])
            if os.path.exists(o): im.paste(Image.open(o), (i * W, 16))
            d.text((i * W + 3, 0), f'{cid} {a}-{b}s  抖{sc}', font=F, fill=(255, 210, 90))
    rows.append(im)
board = Image.new('RGB', (W * 6, sum(r.height for r in rows))); y = 0
for r in rows: board.paste(r, (0, y)); y += r.height
board.save(out, quality=85); print(out, board.size)
