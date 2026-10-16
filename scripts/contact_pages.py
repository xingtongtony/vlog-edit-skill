"""Contact pages of a rendered cut: one frame from the middle of every shot, 30 shots per page, labelled.

usage: python3 contact_pages.py <render.mp4> <out_dir> [--at 0.5]
Reads <render.mp4>.timeline.json. Look at every page before sending a cut: verify cannot see a black clip, a
vertical ceiling shot, someone else's photo, or a card sitting on a face.
"""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont

FF = '/opt/homebrew/bin/ffmpeg'
W, H, COLS, PER = 384, 216, 5, 30

def frame(mp4, t, p):
    subprocess.run([FF, '-v', 'error', '-y', '-ss', f'{t:.2f}', '-i', mp4, '-frames:v', '1', '-vf', f'scale={W}:{H}', p])
    return p

def main():
    mp4, out = sys.argv[1], sys.argv[2]; at = float(sys.argv[sys.argv.index('--at') + 1]) if '--at' in sys.argv else 0.5
    os.makedirs(out, exist_ok=True); tl = json.load(open(mp4 + '.timeline.json'))
    with ThreadPoolExecutor(8) as ex:
        paths = list(ex.map(lambda r: frame(mp4, r['at'] + r['dur'] * at, f"{out}/f{r['shot']:03d}.jpg"), tl))
    font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), '..', 'fonts', 'ResourceHanRoundedCN-Light.ttf'), 15)
    for pg in range(0, len(tl), PER):
        rows = tl[pg:pg + PER]; sheet = Image.new('RGB', (COLS * W, -(-len(rows) // COLS) * (H + 22)), 'white')
        for k, (r, p) in enumerate(zip(rows, paths[pg:pg + PER])):
            x, y = k % COLS * W, k // COLS * (H + 22)
            if os.path.exists(p): sheet.paste(Image.open(p), (x, y + 22))
            m, s = divmod(r['at'], 60)
            ImageDraw.Draw(sheet).text((x + 4, y + 2), f"#{r['shot']} {r['clip']} {int(m)}:{s:04.1f} {r['dur']:.1f}s {r.get('note', '')[:14]}", fill='black', font=font)
        sheet.save(f'{out}/page{pg // PER + 1}.jpg', quality=85)
        print(f'{out}/page{pg // PER + 1}.jpg')
    for p in paths:
        if os.path.exists(p): os.remove(p)

if __name__ == '__main__':
    main()
