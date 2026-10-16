"""Overview board: one row per clip (3 frames at 15%/50%/85%), labelled with id, time and length.

usage: python3 board.py <project_dir> <first_id> <last_id> <out.jpg>
"""
import json, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

FF = '/opt/homebrew/bin/ffmpeg'
FONT = ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', 15)
W, H = 256, 144

def frame(proxy, t, tmp):
    out = os.path.join(tmp, 'f.jpg')
    subprocess.run([FF, '-y', '-loglevel', 'error', '-ss', f'{t:.2f}', '-i', proxy, '-frames:v', '1',
                    '-vf', f'scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2', out])
    return Image.open(out).convert('RGB') if os.path.exists(out) else Image.new('RGB', (W, H))

def main():
    project, first, last, out = sys.argv[1:5]
    ing = os.path.join(project, 'ingest')
    clips = [c for c in json.load(open(f'{ing}/clips.json')) if first <= c['id'] <= last]
    cols = 2  # two clips per row, 3 frames each
    rows = -(-len(clips) // cols)
    board = Image.new('RGB', (cols * (3 * W + 12), rows * (H + 22)), (20, 20, 20))
    d = ImageDraw.Draw(board)
    with tempfile.TemporaryDirectory() as tmp:
        for i, c in enumerate(clips):
            x0, y0 = (i % cols) * (3 * W + 12), (i // cols) * (H + 22)
            proxy = f'{ing}/proxy/{c["id"]}.mp4'
            label = f'{c["id"]}  {c["time"][5:16].replace("T", " ")}  {c["duration"]:.0f}s  {c["kind"]}'
            d.text((x0 + 4, y0 + 2), label, font=FONT, fill=(255, 210, 90))
            if not os.path.exists(proxy): continue
            for k, p in enumerate((0.15, 0.5, 0.85)):
                board.paste(frame(proxy, c['duration'] * p, tmp), (x0 + k * W, y0 + 20))
    board.save(out, quality=85)
    print(out, board.size)

if __name__ == '__main__':
    main()
