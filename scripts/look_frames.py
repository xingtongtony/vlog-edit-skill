"""Single frames of photo / screenshot / laid-over shots, drawn by the renderer's own code - a look in seconds before a
section render (Bali v8: the margin note cut off at the frame edge and an arrow across a word showed up this way).

usage: python3 look_frames.py <edl.json> <out dir> <shot>@<seconds> ... [--sources <local copies>]
Plain photos, masks (beats from the song under the shot) and screenshot cards come from render_still; photos laid over
video (render_layers) are composited on an ungraded frame of the clip (blurred if the shot says so).
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_preview as R
from PIL import Image, ImageFilter

def main():
    args = sys.argv[1:]; src = None
    if '--sources' in args:
        k = args.index('--sources'); src = os.path.expanduser(args[k + 1]); del args[k:k + 2]
    edl = json.load(open(args[0])); out = args[1]; os.makedirs(out, exist_ok=True)
    W, H = edl.get('size', [1920, 1080]); fps = edl.get('fps', 30); shots = edl['shots']
    clips = {c['id']: c for c in json.load(open(os.path.join(edl['project'], 'ingest', 'clips.json')))}
    local = lambda p: os.path.join(src, os.path.basename(p)) if src and p and os.path.exists(os.path.join(src, os.path.basename(p))) else p
    for c in clips.values(): c['path'] = local(c['path'])
    tl, t = [], 0
    for s in shots: tl.append(t / fps); t += round((s['out'] - s['in']) * fps)
    for spec in args[2:]:
        i, at = spec.split('@'); i, at = int(i), float(at); s = dict(shots[i]); dst = f'{out}/s{i}_{at:.2f}.png'
        if s.get('layers'):
            s['layers'] = [dict(L, photo=local(L['photo'])) if L.get('photo') else L for L in s['layers']]
            mv = f'{out}/_layers.mov'; R.render_layers(s, W, H, fps, round((s['out'] - s['in']) * fps), mv)
            bg = R._bg_frame(clips, s['clip'], s['in'] + at, W, H).filter(ImageFilter.GaussianBlur(s.get('blur', 0))).convert('RGBA')
            raw = subprocess.run([R.FF, '-v', 'error', '-ss', f'{at:.3f}', '-i', mv, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'],
                                 capture_output=True, check=True).stdout
            bg.alpha_composite(Image.frombytes('RGBA', (W, H), raw)); bg.convert('RGB').save(dst)
        elif s.get('photo'):
            s['photo'] = local(s['photo'])
            beats = R.shot_beats(edl, tl + [t / fps], i, os.path.join(edl['project'], 'ingest', 'beats.json')) if s.get('mask') else None
            mv = f'{out}/_still.mp4'; R.render_still(s, W, H, fps, at, 2 / fps, mv, out, clips, beats)
            subprocess.run([R.FF, '-v', 'error', '-y', '-i', mv, '-frames:v', '1', dst], check=True)
        else:
            sys.exit(f'shot {i} ({s["clip"]}) is plain video: nothing drawn on it here')
        print(dst)

if __name__ == '__main__':
    main()
