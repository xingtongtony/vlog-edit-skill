"""Colour grading: per-clip underwater correction + one global "look" (filmic tone, split toning, vignette, grain).

Underwater correction follows the usual recipe (water absorbs red first):
  1. red compensation  - add green into red in proportion to the red deficit (Ancuti-style, linearised as a channel mix)
  2. white balance towards a tropical-water target at 70 % strength - never neutral grey (deep blue water turns magenta)
  3. contrast stretch  - per-channel 0.5 / 99.5 percentile levels (a light dehaze)
  4. clarity + saturation + light denoise (lifting red lifts noise)
Parameters are measured once per clip from a few frames and used for every shot of that clip (no flicker, and two shots
of one clip never differ in colour).

usage (preview a before/after board):
  python3 grade.py <out.jpg> <clip>:<t> [<clip>:<t> ...]
"""
import json, os, subprocess, sys
import numpy as np

FF = '/opt/homebrew/bin/ffmpeg'

LOOKS = {
    # subtle filmic look for travel vlogs: soft S-curve, teal shadows / warm highlights, gentle vignette, fine grain
    'film': {
        'curve': "curves=all='0/0.02 0.25/0.22 0.5/0.5 0.75/0.79 1/0.97'",
        'balance': 'colorbalance=rs=-0.03:gs=0.0:bs=0.04:rh=0.04:gh=0.01:bh=-0.04',
        'eq': 'eq=saturation=1.06',
        'vignette': 'vignette=angle=PI/8:mode=forward',
        'grain': 'noise=alls=3:allf=t',
    },
    'none': {},
}
# same look without the moving grain: random per-frame noise is what the encoder cannot compress (a 14 min cut was
# 1.7 GB at 16 Mbps), and the platforms' own re-encode turns it into blocks anyway
LOOKS['film_clean'] = {k: v for k, v in LOOKS['film'].items() if k != 'grain'}

def frames(path, times, w=480):
    """Decode a few frames as float RGB arrays (0..1)."""
    out = []
    for t in times:
        raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.3f}', '-i', path, '-frames:v', '1', '-vf', f'scale={w}:-2',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
        if not raw: continue
        h = len(raw) // (w * 3)
        out.append(np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3).astype(np.float32) / 255)
    return out

def is_underwater(imgs):
    """Blue-green cast with suppressed red."""
    m = np.mean([im.reshape(-1, 3).mean(0) for im in imgs], 0)
    return bool(m[0] < 0.78 * m[1] and m[2] > m[0] * 1.05 and (m[1] + m[2]) / 2 > 0.18)

# Water should stay a little blue-green after correction; pulling it to neutral grey turns deep blue water magenta.
WATER_TARGET = np.array([0.86, 1.0, 1.13])

def underwater_params(imgs, strength=1.0):
    px = np.concatenate([im.reshape(-1, 3) for im in imgs])
    r, g, b = px.mean(0)
    # 1. red compensation as a channel mix: R' = R + k*G  (moderate: noisy cameras, no raw)
    k = float(np.clip(strength * 0.6 * (g - r) / max(g, 1e-3), 0, 0.5))
    px2 = px.copy(); px2[:, 0] = np.clip(px[:, 0] + k * px[:, 1], 0, 1)
    # 2. white balance towards a tropical-water target, at 70 % strength
    m = px2.mean(0); target = m.mean() * WATER_TARGET / WATER_TARGET.mean()
    gains = np.clip((target / np.maximum(m, 1e-3)) ** 0.6, 0.7, 1.8)
    px3 = np.clip(px2 * gains, 0, 1)
    # 3. stretch mostly on luminance, a little per channel (per-channel only = colour shifts)
    lo_c = np.percentile(px3, 0.5, axis=0); hi_c = np.percentile(px3, 99.5, axis=0)
    lum = px3.mean(1); lo_l, hi_l = np.percentile(lum, 0.5), np.percentile(lum, 99.5)
    lo = 0.7 * lo_l + 0.3 * lo_c; hi = 0.7 * hi_l + 0.3 * hi_c; hi = np.maximum(hi, lo + 0.25)
    return {'k': round(k, 3), 'gains': [round(float(x), 3) for x in gains],
            'lo': [round(float(x), 3) for x in lo], 'hi': [round(float(x), 3) for x in hi]}

def underwater_filter(p):
    gr, gg, gb = p['gains']; k = p['k']
    mix = f"colorchannelmixer=rr={gr:.3f}:rg={k * gr:.3f}:gg={gg:.3f}:bb={gb:.3f}"
    lv = ('colorlevels=' + ':'.join(f'{c}imin={lo:.3f}:{c}imax={hi:.3f}' for c, lo, hi in zip('rgb', p['lo'], p['hi'])))
    return ','.join([mix, lv, 'hqdn3d=1.5:1.5:3:3', 'unsharp=7:7:0.6:7:7:0', 'eq=saturation=1.22:contrast=1.03'])

def clip_filter(path, times, mode='auto', look='film'):
    """One filter chain for a whole clip, measured on frames at `times` (all ranges of it the edit uses)."""
    chain = []
    if mode in ('auto', 'underwater'):
        imgs = frames(path, sorted(times)[:: max(1, len(times) // 12)])
        if imgs and (mode == 'underwater' or is_underwater(imgs)):
            chain.append(underwater_filter(underwater_params(imgs)))
    L = LOOKS.get(look, {})
    chain += [L[k] for k in ('curve', 'balance', 'eq', 'vignette', 'grain') if k in L]
    return ','.join(chain)

def shot_filter(path, t_in, t_out, mode='auto', look='film'):
    """Filter chain for one shot: [underwater correction] + look. mode: auto | underwater | none."""
    chain = []
    if mode in ('auto', 'underwater'):
        imgs = frames(path, np.linspace(t_in + 0.1, max(t_in + 0.1, t_out - 0.1), 4))
        if imgs and (mode == 'underwater' or is_underwater(imgs)):
            chain.append(underwater_filter(underwater_params(imgs)))
    L = LOOKS.get(look, {})
    chain += [L[k] for k in ('curve', 'balance', 'eq', 'vignette', 'grain') if k in L]
    return ','.join(chain)

if __name__ == '__main__':
    out, specs = sys.argv[1], sys.argv[2:]
    from PIL import Image, ImageDraw, ImageFont
    F = ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', 22)
    rows = []
    for spec in specs:
        path, t = spec.rsplit(':', 1); t = float(t)
        f = shot_filter(path, t, t + 2)
        pair = []
        for vf in ('scale=800:-2', f'scale=800:-2,{f}'):
            raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.3f}', '-i', path, '-frames:v', '1', '-vf', vf, '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True).stdout
            from io import BytesIO; pair.append(Image.open(BytesIO(raw)).convert('RGB'))
        row = Image.new('RGB', (1600, pair[0].height)); row.paste(pair[0], (0, 0)); row.paste(pair[1], (800, 0))
        d = ImageDraw.Draw(row); d.text((10, 8), '调色前', font=F, fill=(255, 220, 80)); d.text((810, 8), '调色后', font=F, fill=(255, 220, 80))
        d.text((10, row.height - 32), os.path.basename(path)[:40], font=F, fill=(255, 255, 255))
        rows.append(row)
    board = Image.new('RGB', (1600, sum(r.height for r in rows)))
    y = 0
    for r in rows: board.paste(r, (0, y)); y += r.height
    board.save(out, quality=88); print(out, board.size)
