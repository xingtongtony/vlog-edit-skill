"""Render a preview (or a whole film) from an edit decision list (EDL).

usage: python3 render_preview.py <edl.json> <out.mp4> [--workers N] [--shots A:B] [--reuse-video <earlier render.mp4>]
                                [--sources <local copies from cache_sources.py>]

EDL (times in seconds; source times are positions inside the clip):
{
  "project": "<project dir>", "size": [1920, 1080], "fps": 30, "look": "film" | "none",
  "shots": [{"clip": "c017", "in": 34.2, "out": 37.3, "audio": "live|ambient|mute",
             "grade": "auto|underwater|none", "fade_from_black": 1.0}],
  "subtitles": [{"shot": 0, "from": 34.32, "to": 37.28, "text": "..."}],     # live speech, source time of that shot
  "vo":        [{"shot": 5, "at": 0.3, "text": "...", "dur": 3.2}],           # narration the user will record:
               # shown as a subtitle from `at` seconds into the shot; music is ducked under it (dur defaults to text length)
  "cards":     [{"shot": 3, "at": 0.4, "dur": 2.6, "text": "...", "kind": "card|title|chapter|label", "sub": "...", "kicker": "CHAPTER 01"}],
               # card = short centred line; title = film title; chapter = poster layout bottom-left; label = small stamp
  "typewriter": [{"shot": 15, "at": 0.4, "dur": 3.0, "text": "05:31 AM", "sub": "宿务 · 菲律宾", "y": 0.10}],
  "fx": [{"type": "leak", "cut": 14, "dur": 0.9}],                           # warm edge light leak over the cut into shot 14
  "music": [{"path": "...", "start": 0.0, "from_shot": 1, "to_shot": 40, "gain": 0.55,
             "states": [{"shot": 12, "at": -0.2, "state": "none|low|full"}]}]   # one entry per song; a dict is also accepted
}
Music under live speech is ducked so it sits at least 18 dB under the measured voice (never above -13 dB of its own
level); under narration it is ducked about -7 dB (DUCK_VO), and after a talk shot it swells back into the B-roll. Shots with `ambient` sound play their natural sound louder (0.5) where
no music plays. Photos can carry `"bed": {"clip": "c105", "in": 0}` - natural sound from another clip.
Each clip gets one grade for all its shots (measured over every range of it the edit uses), so a clip never changes
colour between two of its shots. Subtitles never overlap: each one ends where the next one starts. Overlays are burned per shot (a few inputs per
ffmpeg call, shots rendered in parallel) - one big overlay graph silently dropped inputs before.
Writes the timeline (shot start times) next to the output as <out>.timeline.json, then verifies the result.
"""
import json, os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FF = '/opt/homebrew/bin/ffmpeg'; FP = '/opt/homebrew/bin/ffprobe'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, 'fonts', 'ResourceHanRoundedCN-Light.ttf')
LATIN = '/System/Library/Fonts/Avenir Next.ttc'
SR = 48000
GAIN = {'full': 1.0, 'low': 0.18, 'none': 0.0}
DUCK = 0.22          # music gain under live speech at most (the measured voice decides, see VOICE_OVER_MUSIC_DB)
DUCK_VO = 0.45       # under narration about -7 dB: the music stays "full" (reference films ~6 dB; Bali v5 at -13 dB read as "音乐都有点小")
AUDIO_GAIN = {'live': 1.0, 'half': 0.45, 'ambient': 0.12, 'mute': 0.0}
MUSIC_RMS = 10 ** (-17 / 20)  # every song is levelled to this before its gain: a loud master must not bury speech
AMBIENT_NO_MUSIC = 0.5   # natural sound is the only sound there - it has to be heard
VOICE_OVER_MUSIC_DB = 18 # live speech stays this far above the music under it
BED_RMS = 10 ** (-30 / 20)   # a natural-sound bed under a photo sits at about -30 dBFS RMS

def run(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def resolve_music(m, tl):
    """Turn shot-anchored music fields into timeline seconds: at_shot -> at, {"shot","at","state"} -> [t, state]."""
    m = dict(m)
    if 'at_shot' in m: m['at'] = tl[m['at_shot']] + m.get('at_offset', 0)
    m['states'] = [[tl[x['shot']] + x.get('at', 0), x['state']] if isinstance(x, dict) else x for x in m['states']]
    return m

def music_envelope(states, N, total):
    """Gain curve for the three music states; 1.2 s fade down, 0.4 s fade up, 1.5 s fade out at the end."""
    sr = N / total; env = np.zeros(N, np.float32); cur = 0.0; states = sorted(states); pos = 0
    for j, (t0, st) in enumerate(states):
        i0 = int(t0 * sr); i1 = int(states[j + 1][0] * sr) if j + 1 < len(states) else N
        env[pos:i0] = cur; tgt = GAIN[st]; ramp = int((0.4 if tgt > cur else 1.2) * sr)
        r = np.linspace(cur, tgt, max(1, min(ramp, i1 - i0)), dtype=np.float32)
        env[i0:i0 + len(r)] = r; env[i0 + len(r):i1] = tgt; cur = tgt; pos = i1
    tail = int(1.5 * sr); env[-tail:] *= np.linspace(1, 0, tail)
    return env

# ---------------- overlays ----------------
def spaced(draw, x, y, text, font, fill, track):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill); x += draw.textlength(ch, font=font) + track

def spaced_w(text, font, track):
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    return sum(d.textlength(c, font=font) for c in text) + track * (len(text) - 1)

def subtitle_png(text, W, H, path):
    """Spec from docs/storytelling.md: 0.036H Resource Han Rounded Light, ~0.2em tracking,
    black box at 60% opacity (measured 75-83% on the user's own videos; the user asked twice for more translucent,
    and for a slightly smaller size than the first 0.039H)."""
    s = H / 1080; f = ImageFont.truetype(FONT, round(39 * s)); tr = 7.8 * s
    tw = spaced_w(text, f, tr); asc, desc = f.getmetrics()
    padx, pady, r = 13 * s, 7 * s, 5.5 * s
    box_h = 48 * s; y1 = H - 51 * s; y0 = y1 - box_h; x0 = (W - tw) / 2 - padx; x1 = (W + tw) / 2 + padx
    im = Image.new('RGBA', (W, H)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=(0, 0, 0, 153))
    bb = f.getbbox('国'); ty = y0 + (box_h - (bb[3] - bb[1])) / 2 - bb[1]
    spaced(d, (W - tw) / 2, ty, text, f, (255, 255, 255, 255), tr)
    im.save(path)

def card_png(card, W, H, path):
    s = H / 1080; im = Image.new('RGBA', (W, H))
    def shadowed(draw_fn, blur):
        sh = Image.new('RGBA', (W, H)); draw_fn(ImageDraw.Draw(sh), (0, 0, 0, 150)); sh = sh.filter(ImageFilter.GaussianBlur(blur))
        top = Image.new('RGBA', (W, H)); draw_fn(ImageDraw.Draw(top), (255, 255, 255, 255))
        return Image.alpha_composite(sh, top)
    kind, text = card.get('kind', 'card'), card['text']
    FD = os.path.join(ROOT, 'fonts')
    def ft(name, size, wght=None):
        f = ImageFont.truetype(os.path.join(FD, name), round(size * s))
        if wght:
            try: f.set_variation_by_axes([wght])
            except Exception: pass
        return f
    cream = (255, 252, 245, 255)
    if kind == 'title':      # brush-script Latin name + marker-hand Chinese name + tracked caps line (young type: no calligraphy)
        def fn(d, fill):
            big = ft('KaushanScript-Regular.ttf', 200); w = d.textlength(card.get('latin', 'Bohol'), font=big); x = (W - w) / 2; y = H * card.get('y', 0.30)
            d.text((x, y), card.get('latin', 'Bohol'), font=big, fill=fill)
            d.text((x + w - 60 * s, y + 236 * s), text, font=ft('LXGWMarkerGothic-Regular.ttf', 92), fill=fill)
            cap = ft('Outfit[wght].ttf', 22, 400); sub = card.get('sub', '')
            spaced(d, (W - spaced_w(sub, cap, 8 * s)) / 2, y + 380 * s, sub, cap, fill, 8 * s)
        im = shadowed(fn, 10 * s)
    elif kind == 'end':      # script line + small caps
        def fn(d, fill):
            big = ft('KaushanScript-Regular.ttf', 130); w = d.textlength(text, font=big); y = H * card.get('y', 0.36)
            d.text(((W - w) / 2, y), text, font=big, fill=fill)
            cap = ft('Outfit[wght].ttf', 22, 400); sub = card.get('sub', '')
            spaced(d, (W - spaced_w(sub, cap, 8 * s)) / 2, y + 200 * s, sub, cap, fill, 8 * s)
        im = shadowed(fn, 10 * s)
    elif kind == 'chapter':  # bottom-left: brush-script "Chapter One" / marker-hand title / tracked caps line
        x, y = W * 0.07, H * 0.60
        def fn(d, fill):
            d.text((x, y), card.get('kicker', ''), font=ft('KaushanScript-Regular.ttf', 44), fill=fill)
            d.text((x - 4 * s, y + 58 * s), text, font=ft('LXGWMarkerGothic-Regular.ttf', 104), fill=fill)
            if card.get('sub'):
                cap = ft('Outfit[wght].ttf', 20, 400); spaced(d, x + 2 * s, y + 200 * s, card['sub'], cap, fill, 7 * s)
        im = shadowed(fn, 9 * s)
    elif kind == 'pop':      # the yellow 花字 label: removed at the user's request (Bali v8), the builder rejects it
        raise ValueError(f"card '{text}': yellow 花字 labels are not used")
    elif kind == 'label':  # small place/time stamp, bottom-left
        f = ImageFont.truetype(FONT, round(28 * s))
        def fn(d, fill): spaced(d, 64 * s, H - 118 * s, text, f, fill, 3 * s)
        im = shadowed(fn, 5 * s)
    else:  # centred trailer line: small, airy, no band; a faint soft shadow keeps it readable on bright shots
        f = ImageFont.truetype(FONT, round(46 * s)); y = H * card.get('y', 0.46)   # put it on a calm part of the frame
        def fn(d, fill):
            tw = spaced_w(text, f, 10 * s); spaced(d, (W - tw) / 2, y, text, f, fill, 10 * s)
        sh = Image.new('RGBA', (W, H)); fn(ImageDraw.Draw(sh), (0, 0, 0, 120)); sh = sh.filter(ImageFilter.GaussianBlur(6 * s))
        top = Image.new('RGBA', (W, H)); fn(ImageDraw.Draw(top), (255, 255, 255, 255))
        im = Image.alpha_composite(sh, top)
    im.save(path)

MONO = '/System/Library/Fonts/Menlo.ttc'

def grunge_mask(alpha, seed=0):
    """Worn-print texture on a text alpha: small rounded speckles inside the strokes and nibbles along the edges
    (the user's own Jianying title for Bohol, 2026-10-23)."""
    a = np.asarray(alpha, np.float32) / 255; h, w = a.shape; rng = np.random.default_rng(seed)
    n1 = np.asarray(Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.6)), np.float32)
    n2 = np.asarray(Image.fromarray((rng.random((max(1, h // 6), max(1, w // 6))) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
                    .filter(ImageFilter.GaussianBlur(3)), np.float32)
    n = 0.55 * (n1 - n1.mean()) / (n1.std() + 1e-6) + 0.45 * (n2 - n2.mean()) / (n2.std() + 1e-6)
    inner = np.asarray(alpha.filter(ImageFilter.MinFilter(7)), np.float32) / 255
    out = a * ~(n > 1.55) * ~((a > 0.4) & (inner < 0.5) & (n > 0.75))
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))

def stamp_title_pngs(card, W, H, tmp, k):
    """Title in the user's own style: small, centred, worn bold - Latin name (Lilita One) over the Chinese name (Noto
    Serif SC Black). One transparent PNG per glyph, so the glyphs can appear one by one and leave one by one.
    Returns [(png, order)] in reading order."""
    s = H / 1080; FD = os.path.join(ROOT, 'fonts')
    f1 = ImageFont.truetype(os.path.join(FD, 'LilitaOne-Regular.ttf'), round(card.get('size', 100) * s))
    f2 = ImageFont.truetype(os.path.join(FD, 'NotoSerifSC-Black.otf'), round(card.get('size', 100) * 0.78 * s))
    latin, cn = card.get('latin', ''), card['text']; y1 = H * card.get('y', 0.32); y2 = y1 + card.get('size', 100) * 1.1 * s
    al = Image.new('L', (W, H)); d = ImageDraw.Draw(al); boxes = []
    for txt, f, y, track in ((latin, f1, y1, 2 * s), (cn, f2, y2, 10 * s)):
        tw = spaced_w(txt, f, track); x = (W - tw) / 2
        for ch in txt:
            if ch.strip():
                d.text((x, y), ch, font=f, fill=255); bb = d.textbbox((x, y), ch, font=f); boxes.append(bb)
            x += d.textlength(ch, font=f) + track
    al = grunge_mask(al, seed=card.get('seed', 4))
    # soft shadow, ~15 % opacity, so the white reads on bright sky without looking outlined
    sh = al.filter(ImageFilter.GaussianBlur(8 * s)).point(lambda v: int(v * 0.35))
    out = []
    for j, bb in enumerate(boxes):
        m = Image.new('L', (W, H)); m.paste(255, (int(bb[0] - 6 * s), int(bb[1] - 6 * s), int(bb[2] + 6 * s), int(bb[3] + 6 * s)))
        im = Image.new('RGBA', (W, H)); blk = Image.new('RGBA', (W, H), (0, 0, 0, 255)); wht = Image.new('RGBA', (W, H), (252, 251, 247, 255))
        im.paste(blk, (0, 0), Image.fromarray(np.minimum(np.asarray(sh), np.asarray(m))))
        im.paste(wht, (0, 0), Image.fromarray(np.minimum(np.asarray(al), np.asarray(m))))
        p = f'{tmp}/title{k}_{j}.png'; im.save(p); out.append(p)
    return out

def place_pngs(card, W, H, tmp, k):
    """Place | time stamp in the user's own Surabaya style ("Bromo | 14:33"): thin white type, lower left, a light
    shadow; the place first, the bar and time a beat later. Returns (place png, time png)."""
    s = H / 1080; f = ImageFont.truetype(FONT, round(40 * s)); x, y = W * card.get('x', 0.094), H * card.get('y', 0.80)
    place, tm = card['text'], card.get('time', '')
    d0 = ImageDraw.Draw(Image.new('L', (1, 1))); gap = 22 * s
    pw = spaced_w(place, f, 2 * s)
    outs = []
    for part, px in ((place, x), ((f'|   {tm}' if tm else ''), x + pw + gap)):
        im = Image.new('RGBA', (W, H))
        if part:
            for fill, off in (((0, 0, 0, 90), 2 * s), ((255, 255, 255, 245), 0)):
                lay = Image.new('RGBA', (W, H)); spaced(ImageDraw.Draw(lay), px + off, y + off, part, f, fill, 2 * s)
                if off: lay = lay.filter(ImageFilter.GaussianBlur(3 * s))
                im = Image.alpha_composite(im, lay)
        p = f'{tmp}/place{k}_{len(outs)}.png'; im.save(p); outs.append(p)
    return outs

def typewriter_png(text, sub, W, H, path, y=0.40):
    """Time stamp typed out ("05:31 AM_"), with an optional small line underneath once typing ends."""
    s = H / 1080; im = Image.new('RGBA', (W, H)); f = ImageFont.truetype(MONO, round(54 * s)); x, y = W * 0.08, H * y
    for fill, off in (((0, 0, 0, 110), 3 * s), ((255, 236, 200, 255), 0)):
        d = ImageDraw.Draw(im); spaced(d, x + off, y + off, text, f, fill, 6 * s)
        if sub: spaced(d, x + off + 4 * s, y + off + 82 * s, sub, ImageFont.truetype(FONT, round(38 * s)), fill, 8 * s)
    im.save(path)

def make_leak(path, dur, fps, seed=0, warmth=1.0, w=480, h=270):
    """Procedural light leak: warm glow bleeding in from one side edge (red/orange/amber only), sweeping a little
    across and peaking over the cut. The middle of the frame stays mostly untouched - a full-frame wash looks cheap."""
    rng = np.random.default_rng(seed); n = max(2, round(dur * fps))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    side = rng.choice([-1, 1])                                   # enter from the left or the right edge
    blobs = []
    for _ in range(3):
        x0 = (0.0 if side < 0 else 1.0) * w + side * rng.uniform(0.04, 0.14) * w   # centre just outside the frame
        blobs.append((x0, rng.uniform(0.15, 0.85) * h, rng.uniform(0.09, 0.16) * w,
                      np.array([1.0, rng.uniform(0.28, 0.48), rng.uniform(0.03, 0.10)])))
    with tempfile.TemporaryDirectory() as td:
        for i in range(n):
            t = i / (n - 1); env = np.sin(np.pi * t) ** 1.3
            img = np.zeros((h, w, 3), np.float32)
            for cx, cy, r, col in blobs:
                cxt = cx - side * t * 0.12 * w                         # drift inwards a little
                img += np.exp(-(((xx - cxt) ** 2) / (2 * (1.6 * r) ** 2) + ((yy - cy) ** 2) / (2 * r * r)))[..., None] * col
            raw = img.max(2); img = np.clip(img / np.maximum(raw, 1e-3)[..., None], 0, 1)   # colour = blob colours, no clipping plateau
            a = np.clip((1 - np.exp(-1.3 * raw)) ** 1.8 * env * 0.6, 0, 1)                 # soft roll-off, no hard disc edge
            Image.fromarray(np.dstack([img * 255, a * 255]).astype(np.uint8)).save(f'{td}/f{i:03d}.png')
        run([FF, '-y', '-framerate', str(fps), '-i', f'{td}/f%03d.png', '-c:v', 'qtrle', '-pix_fmt', 'argb', path])

def compose_photo(path, W, H, out, caption=None):
    """Still photo for video: the whole photo fitted to the frame on black (portrait photos get black sides) - no blurred
    backdrop, border or shadow. The user's choice: after v7 they swapped every framed photo for the plain one in
    Jianying. Rendered at 2x so the slow push-in stays sharp."""
    from PIL import ImageOps
    W2, H2 = W * 2, H * 2
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    lum = np.asarray(im.convert('L').resize((64, 64)), np.float32).mean()
    if lum < 45:   # night photo (fireflies, stars): lift the shadows so the trees and the light points read on screen
        im = Image.eval(ImageOps.autocontrast(im.filter(ImageFilter.MedianFilter(5)), cutoff=1), lambda v: int(255 * (v / 255) ** 0.85))
    sc = min(W2 / im.width, H2 / im.height); fg = im.resize((round(im.width * sc), round(im.height * sc)), Image.LANCZOS)
    bg = Image.new('RGB', (W2, H2), (0, 0, 0)); bg.paste(fg, ((W2 - fg.width) // 2, (H2 - fg.height) // 2))
    if caption:    # on the photo near the bottom, with a soft dark halo instead of a box
        f = ImageFont.truetype(os.path.join(ROOT, 'fonts', 'Outfit[wght].ttf'), round(H2 * 0.016)); f.set_variation_by_axes([400])
        tw = spaced_w(caption, f, H2 * 0.006); x, y = (W2 - tw) / 2, H2 * 0.90
        halo = Image.new('L', (W2, H2), 0); spaced(ImageDraw.Draw(halo), x, y, caption, f, 255, H2 * 0.006)
        halo = halo.filter(ImageFilter.MaxFilter(2 * round(H2 / 300) + 1)).filter(ImageFilter.GaussianBlur(H2 / 120))
        bg.paste(Image.new('RGB', (W2, H2), (0, 0, 0)), (0, 0), halo.point(lambda v: min(255, int(v * 1.4))))
        spaced(ImageDraw.Draw(bg), x, y, caption, f, (240, 237, 230), H2 * 0.006)
    bg.save(out, quality=95)

def ease(x):
    x = min(1.0, max(0.0, x)); return 1 - (1 - x) ** 3

def _bg_frame(clips, clip, t, W, H):
    """One frame of a video clip as a PIL image (backdrop for a news card)."""
    raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.2f}', '-i', clips[clip]['path'], '-frames:v', '1', '-vf',
                          f'scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True).stdout
    return Image.frombytes('RGB', (W, H), raw)

# ---------------- hand-drawn marks (the tutorial's 方法四 涂鸦, on screenshots) ----------------
PEN = (234, 62, 46)            # red pen: reads on a white screenshot and on the dark blurred backdrop around it
HAND = {'zh': os.path.join(ROOT, 'fonts', 'Xiaolai-Regular.ttf'), 'en': os.path.join(ROOT, 'fonts', 'Caveat[wght].ttf')}
MARK_DUR = {'circle': 0.5, 'underline': 0.4, 'arrow': 0.35, 'cloud': 0.7, 'text': 0.45}

def _wobble(n, amp, seed):
    """Smooth hand tremor: n offsets from two slow sines with random phases."""
    rng = np.random.default_rng(seed); u = np.linspace(0, 1, n)
    return amp * (0.6 * np.sin(2 * np.pi * (rng.uniform(0.6, 1.4) * u + rng.uniform(0, 1)))
                  + 0.4 * np.sin(2 * np.pi * (rng.uniform(2.0, 3.5) * u + rng.uniform(0, 1))))

def mark_strokes(m, X, Y, S, seed):
    """A mark as pen strokes (lists of points, drawn in this order). X / Y turn the mark's coordinates (fractions of
    the card: < 0 or > 1 is the backdrop around it) into pixels; S is the frame height, sizes scale with it.
    circle: `box` [x0, y0, x1, y1], one loop and a bit, the second pass drifting out like a pen going round a word;
    underline: under `box`; arrow: `from` -> `to`, bent by `bend`; cloud: a rain cloud at `xy`, `size` (of S) wide."""
    k = m['kind']
    if k == 'circle':
        x0, y0, x1, y1 = m['box']; cx, cy = X((x0 + x1) / 2), Y((y0 + y1) / 2); rx, ry = (X(x1) - X(x0)) / 2, (Y(y1) - Y(y0)) / 2
        n = 120; a = np.pi * 1.15 + np.linspace(0, 2 * np.pi * 1.12, n)
        r = 1 + _wobble(n, 0.05, seed) + np.linspace(0, 0.09, n)
        return [list(zip(cx + rx * r * np.cos(a), cy + ry * r * np.sin(a)))]
    if k == 'underline':
        x0, _, x1, y = m['box']; n = 48
        return [list(zip(np.linspace(X(x0), X(x1), n), Y(y) + _wobble(n, S * 0.003, seed) - np.linspace(0, S * 0.005, n)))]
    if k == 'arrow':
        P0, P2 = np.array([X(m['from'][0]), Y(m['from'][1])]), np.array([X(m['to'][0]), Y(m['to'][1])]); d = P2 - P0
        P1 = (P0 + P2) / 2 + np.array([-d[1], d[0]]) * m.get('bend', 0.25)
        u = np.linspace(0, 1, 40)[:, None]; pts = (1 - u) ** 2 * P0 + 2 * (1 - u) * u * P1 + u ** 2 * P2
        v = pts[-1] - pts[-5]; v = v / (np.linalg.norm(v) + 1e-6) * S * 0.022
        rot = lambda a: np.array([v[0] * np.cos(a) - v[1] * np.sin(a), v[0] * np.sin(a) + v[1] * np.cos(a)])
        tip = pts[-1]
        return [[tuple(p) for p in pts], [tuple(tip - rot(0.55)), tuple(tip), tuple(tip - rot(-0.55))]]
    if k == 'cloud':          # scalloped top, flat bottom; rain dashes under it
        cx, cy = X(m['xy'][0]), Y(m['xy'][1]); w = S * m.get('size', 0.14); n = 140
        a = np.linspace(np.pi * 0.95, np.pi * 0.95 + 2 * np.pi, n); up = np.sin(a) < 0
        r = 1 + np.where(up, 0.2 * np.abs(np.sin(2.5 * (a - np.pi))), 0) + _wobble(n, 0.03, seed)
        xs, ys = cx + w / 2 * r * np.cos(a), cy + w * 0.36 * r * np.sin(a) * np.where(up, 1, 0.4)
        strokes = [list(zip(xs, ys))]
        for j in range(4):
            x = cx - w * 0.3 + j * w * 0.2; y = cy + w * 0.26 + (j % 2) * w * 0.08
            strokes.append([(x, y), (x - w * 0.05, y + w * 0.16)])
        return strokes
    raise ValueError(f'mark kind {k}')

def draw_marks(fr, marks, t, X, Y, S, pen=PEN):
    """Draw every mark that has started by time t (seconds into the shot) onto fr, written on stroke by stroke over
    its `dur` (MARK_DUR by kind) - pinned to the words in the builder (`on=`), so the pen moves as the line says them."""
    d = ImageDraw.Draw(fr); w = max(2, round(S * 0.0055))
    for j, m in enumerate(marks):
        u = (t - m['at']) / m.get('dur', MARK_DUR[m['kind']])
        if u <= 0: continue
        u = ease(u); col = tuple(m.get('color', pen))
        if m['kind'] == 'text':        # handwriting revealed left to right, slightly turned
            f = ImageFont.truetype(HAND[m.get('font', 'zh')], round(S * m.get('size', 0.07)))
            if m.get('font') == 'en': f.set_variation_by_axes([600])
            bb = f.getbbox(m['text']); im = Image.new('RGBA', (bb[2] + 8, bb[3] + 8), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((4, 4), m['text'], font=f, fill=col + (255,))
            cut = round(im.width * u); im.paste((0, 0, 0, 0), (cut, 0, im.width, im.height))
            im = im.rotate(m.get('rot', -4), resample=Image.BICUBIC, expand=True)
            fr.paste(im, (round(X(m['xy'][0])), round(Y(m['xy'][1]))), im)
            continue
        strokes = mark_strokes(m, X, Y, S, seed=j + 11)
        lens = [sum(np.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(s, s[1:])) for s in strokes]
        left = u * sum(lens); scol = [col] + [tuple(m.get('rain', col))] * (len(strokes) - 1)
        for s, L, c in zip(strokes, lens, scol):
            if left <= 0: break
            pts, acc = [s[0]], 0.0
            for p, q in zip(s, s[1:]):
                seg = np.hypot(q[0] - p[0], q[1] - p[1])
                if acc + seg >= left:
                    f_ = (left - acc) / max(seg, 1e-6); pts.append((p[0] + (q[0] - p[0]) * f_, p[1] + (q[1] - p[1]) * f_)); break
                pts.append(q); acc += seg
            left -= L
            d.line(pts, fill=c, width=w, joint='curve')
            for p in (pts[0], pts[-1]): d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=c)

def render_still(sh, W, H, fps, a0, dur, out, tmp, clips=None, beats=None):
    """A photo or a news card as a short video, drawn frame by frame in PIL (smooth push-in, no pixel jitter).
    Plain photo: the whole photo on black, push-in 4.5 % over the shot. News (`sh['news']`): the screenshot as a card
    with rounded corners and a soft shadow over a blurred, darkened frame of our own footage (`bg: [clip, t]`); the card
    rises in, the whole frame drifts in 3.5 %, and a marker highlight sweeps across `hl` (fractions of the screenshot) -
    a cut-out from the paper, not a screenshot dropped on black. `crop` (fractions) trims the screenshot first; `marks`
    are drawn on it by hand (draw_marks) at their times.
    Mask (`sh['mask']`, the tutorial's 方法三 蒙版动画): the photo seen through a window on black; on the music's beats
    (`beats`, seconds into the shot) the window jumps to another size, place and detail, and the last key shows the
    whole photo - a portrait photo is not 5 s of a still between two black bars."""
    full = max(0.5, sh['out'] - sh['in']); n = round(dur * fps); f0 = round(a0 * fps); K = max(1, round(full * fps))
    W2, H2 = W * 2, H * 2
    news, win = sh.get('news'), sh.get('mask')
    if win:
        # a key: window `box` (fractions of the frame), the photo point shown at its centre (`focus`, fractions of the
        # photo) and the photo's width as a share of the frame width (`scale`); `fit` = the whole photo fitted
        from PIL import ImageOps
        im = ImageOps.exif_transpose(Image.open(sh['photo'])).convert('RGB')
        fit = min(W2 / im.width, H2 / im.height)
        def key(k):
            if k.get('fit'):
                fw, fh = im.width * fit / W2, im.height * fit / H2
                return np.array([(1 - fw) / 2, (1 - fh) / 2, (1 + fw) / 2, (1 + fh) / 2, 0.5, 0.5, fw])
            return np.array(list(k['box']) + list(k.get('focus', (0.5, 0.5))) + [k.get('scale', 1.0)])
        bt = lambda b: 0.0 if not b else (beats[b - 1] if beats and b - 1 < len(beats) else b * 0.6)
        keys = [(bt(k.get('beat', 0)), key(k)) for k in win]
        s = min(1.0, W2 * max(k[6] for _, k in keys) / im.width)       # never decode more pixels than the largest key shows
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        def window(t):
            cur = keys[0][1]
            for tk, kv in keys[1:]:
                if t >= tk: cur = cur + (kv - cur) * ease((t - tk) / 0.2)     # a quick 0.2 s move on the beat
            return cur
    elif news:
        from PIL import ImageOps
        bg = _bg_frame(clips, news['bg'][0], news['bg'][1], W2, H2).filter(ImageFilter.GaussianBlur(H2 / 40))
        bg = Image.blend(bg, Image.new('RGB', (W2, H2), (8, 10, 14)), 0.45)
        card = ImageOps.exif_transpose(Image.open(sh['photo'])).convert('RGB')
        if news.get('crop'):
            x0, y0, x1, y1 = news['crop']; card = card.crop((round(x0 * card.width), round(y0 * card.height), round(x1 * card.width), round(y1 * card.height)))
        sc = min(W2 * news.get('w', 0.62) / card.width, H2 * news.get('h', 0.74) / card.height)
        card = card.resize((round(card.width * sc), round(card.height * sc)), Image.LANCZOS)
        r = round(H2 * 0.012); mask = Image.new('L', card.size, 0); ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width - 1, card.height - 1), r, fill=255)
        shadow = Image.new('L', (card.width + 160, card.height + 160), 0); shadow.paste(mask.point(lambda v: v * 0.55), (80, 100))
        shadow = shadow.filter(ImageFilter.GaussianBlur(36))
        cx, cy = (W2 - card.width) // 2, (H2 - card.height) // 2 + round(H2 * news.get('dy', 0.0))
        hl = news.get('hl')
    else:
        base = f"{tmp}/photo_plain_{os.path.basename(sh['photo'])}.jpg"
        if not os.path.exists(base): compose_photo(sh['photo'], W, H, base, sh.get('caption'))
        base = Image.open(base).convert('RGB')
    p = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(fps), '-i', '-',
                          '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
    for j in range(n):
        k = f0 + j; u = k / K
        if win:
            x0, y0, x1, y1, fx, fy, sw = window(k / fps); fr = Image.new('RGB', (W2, H2))
            bx0, by0, bx1, by1 = x0 * W2, y0 * H2, x1 * W2, y1 * H2; kk = sw * W2 / im.width     # frame px per photo px
            sx0 = fx * im.width + (bx0 - (bx0 + bx1) / 2) / kk; sy0 = fy * im.height + (by0 - (by0 + by1) / 2) / kk
            sx0 = min(max(0.0, sx0), im.width - (bx1 - bx0) / kk); sy0 = min(max(0.0, sy0), im.height - (by1 - by0) / kk)   # stay on the photo
            ww, wh = max(1, round(bx1 - bx0)), max(1, round(by1 - by0))
            fr.paste(im.resize((ww, wh), Image.BICUBIC, box=(sx0, sy0, sx0 + (bx1 - bx0) / kk, sy0 + (by1 - by0) / kk)), (round(bx0), round(by0)))
            z = 1 + 0.03 * u
        elif news:
            t = k / fps; fr = bg.copy()
            a = ease(t / 0.5); lift = round((1 - a) * H2 * 0.035)
            fr.paste(Image.new('RGB', shadow.size, (0, 0, 0)), (cx - 80, cy - 80 + lift), shadow.point(lambda v: int(v * a)))
            c = card.copy()
            if hl and t > 0.8:
                w = ease((t - 0.8) / 0.7); x0, y0, x1, y1 = hl
                box = (round(x0 * c.width), round(y0 * c.height), round((x0 + (x1 - x0) * w) * c.width), round(y1 * c.height))
                if box[2] > box[0]:
                    m = Image.new('RGBA', c.size, (0, 0, 0, 0)); ImageDraw.Draw(m).rectangle(box, fill=(255, 205, 40, 105))
                    c = Image.alpha_composite(c.convert('RGBA'), m).convert('RGB')
            fr.paste(c, (cx, cy + lift), mask.point(lambda v: int(v * a)))
            if news.get('marks'):
                draw_marks(fr, news['marks'], t, lambda fx: cx + fx * card.width, lambda fy: cy + lift + fy * card.height, H2, tuple(news.get('pen', PEN)))
            z = 1 + 0.035 * u
        else:
            fr = base; z = 1 + 0.045 * u
        w, h = W2 / z, H2 / z; x0, y0 = (W2 - w) / 2, (H2 - h) / 2
        frame = fr.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h))      # both are drawn at 2x
        p.stdin.write(frame.tobytes())
    p.stdin.close(); p.wait()

def torn_note(text, S, font='zh', size=0.06, seed=0):
    """A handwritten line on a strip of torn paper (RGBA, with its shadow): the tutorial's 撕纸素材上面写字."""
    f = ImageFont.truetype(HAND[font], round(S * size))
    if font == 'en': f.set_variation_by_axes([600])
    bb = f.getbbox(text); pw, ph = bb[2] - bb[0] + round(S * size * 1.1), bb[3] - bb[1] + round(S * size * 0.9)
    pad = round(S * 0.03); im = Image.new('RGBA', (pw + 2 * pad, ph + 2 * pad), (0, 0, 0, 0))
    rng = np.random.default_rng(seed); n = 24
    top = [(pad + pw * i / n, pad + rng.uniform(-1, 1) * S * 0.004) for i in range(n + 1)]
    bot = [(pad + pw * i / n, pad + ph + rng.uniform(-1, 1) * S * 0.004) for i in range(n, -1, -1)]
    left = [(pad + rng.uniform(-1, 1) * S * 0.005, pad + ph * i / 6) for i in range(5, 0, -1)]   # torn edges: ragged
    right = [(pad + pw + rng.uniform(-1, 1) * S * 0.005, pad + ph * i / 6) for i in range(1, 6)]
    poly = top + right + bot + left
    sh = Image.new('L', im.size, 0); ImageDraw.Draw(sh).polygon([(x + S * 0.004, y + S * 0.006) for x, y in poly], fill=110)
    im.putalpha(sh.filter(ImageFilter.GaussianBlur(S * 0.006)))
    paper = Image.new('L', im.size, 0); ImageDraw.Draw(paper).polygon(poly, fill=255)
    im.paste(Image.new('RGBA', im.size, (244, 238, 224, 255)), (0, 0), paper)
    ImageDraw.Draw(im).text((pad + round(S * size * 0.55) - bb[0], pad + round(S * size * 0.45) - bb[1]), text, font=f, fill=(48, 44, 40, 255))
    return im

def render_layers(sh, W, H, fps, n, out):
    """Photos (and a note on torn paper) laid over a video shot one after another - the tutorial's 方法二 画中画文艺风:
    the moving picture stays underneath, each photo is a cropped detail at its own size, the words come last.
    `layers`: [{photo, crop: [x0, y0, x1, y1] of the photo, xy: centre (fractions of the frame), h: height (of the
    frame), tilt: degrees, at: seconds into the shot} | {note: text, font: zh|en, size, xy, tilt, at}]; `dim` darkens the
    video under them. A photo lands with a short pop (0.3 s) and the shutter sound; nothing moves after that but the
    video. Written as an alpha movie that render_shot lays over the shot. `blur` (sigma at 1080p) takes the video out of
    focus: people walking behind the prints read as cut-off legs under them (Bali v8, the run club)."""
    from PIL import ImageOps
    S = H; items = []
    for j, L in enumerate(sh['layers']):
        if L.get('note'):
            im = torn_note(L['note'], S, L.get('font', 'zh'), L.get('size', 0.06), seed=j)
        else:
            ph = ImageOps.exif_transpose(Image.open(L['photo'])).convert('RGB')
            if L.get('crop'):
                x0, y0, x1, y1 = L['crop']; ph = ph.crop((round(x0 * ph.width), round(y0 * ph.height), round(x1 * ph.width), round(y1 * ph.height)))
            h = round(S * L.get('h', 0.5)); ph = ph.resize((round(ph.width * h / ph.height), h), Image.LANCZOS)
            pad = round(S * 0.04); im = Image.new('RGBA', (ph.width + 2 * pad, ph.height + 2 * pad), (0, 0, 0, 0))
            shadow = Image.new('L', im.size, 0); ImageDraw.Draw(shadow).rectangle((pad + 6, pad + 10, pad + ph.width + 6, pad + ph.height + 10), fill=120)
            im.putalpha(shadow.filter(ImageFilter.GaussianBlur(S * 0.012))); im.paste(ph, (pad, pad))
        if L.get('tilt'): im = im.rotate(L['tilt'], resample=Image.BICUBIC, expand=True)
        items.append((L['at'], im, L['xy']))
    a0 = min(at for at, _, _ in items); dim = sh.get('dim', 0.0)
    p = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{W}x{H}', '-r', str(fps), '-i', '-',
                          '-c:v', 'qtrle', '-pix_fmt', 'argb', out], stdin=subprocess.PIPE)
    for k in range(n):
        t = k / fps; fr = Image.new('RGBA', (W, H), (0, 0, 0, round(255 * dim * ease((t - a0) / 0.4)) if t > a0 else 0))
        for at, im, (cx, cy) in items:
            if t < at: continue
            u = ease((t - at) / 0.3); z = 1.06 - 0.06 * u
            lay = im if z == 1.0 else im.resize((round(im.width * z), round(im.height * z)), Image.BICUBIC)
            if u < 1: lay = lay.copy(); lay.putalpha(lay.getchannel('A').point(lambda v: int(v * min(1.0, u * 1.6))))
            x, y = round(cx * W - lay.width / 2), round(cy * H - lay.height / 2)
            if x < 0 or y < 0: lay = lay.crop((max(0, -x), max(0, -y), lay.width, lay.height)); x, y = max(0, x), max(0, y)
            fr.alpha_composite(lay.crop((0, 0, min(lay.width, W - x), min(lay.height, H - y))), (x, y))
        p.stdin.write(fr.tobytes())
    p.stdin.close(); p.wait()

def _map_proj(box, W, H, margin=0.06):
    """lon/lat -> pixel for an equirectangular map of `box` (lon0, lat0, lon1, lat1) fitted into W x H."""
    import math
    lon0, lat0, lon1, lat1 = box; k = math.cos(math.radians((lat0 + lat1) / 2))
    sx = W * (1 - 2 * margin) / ((lon1 - lon0) * k); sy = H * (1 - 2 * margin) / (lat1 - lat0); sc = min(sx, sy)
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    return lambda lon, lat: (W / 2 + (lon - cx) * k * sc, H / 2 - (lat - cy) * sc)

def _land_layer(m, W2, H2):
    """Land/sea image for the map box: an OSM coastline raster (`m['mask']`, .npy + .json beside it, see
    projects/*/design) or Natural Earth polygons (`m['land']`)."""
    import math
    lon0, lat0, lon1, lat1 = m['box']; k = math.cos(math.radians((lat0 + lat1) / 2)); margin = 0.0
    sc = min(W2 / ((lon1 - lon0) * k), H2 / (lat1 - lat0)); cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    if m.get('mask'):
        meta = json.load(open(m['mask'][:-4] + '.json')); Hm, Wm = meta['shape']; bx = meta['box']; res = meta['res']
        mask = np.unpackbits(np.load(m['mask']))[:Hm * Wm].reshape(Hm, Wm).astype(bool)
        xs = cx + (np.arange(W2) - W2 / 2) / (k * sc); ys = cy - (np.arange(H2) - H2 / 2) / sc
        ix = np.clip(((xs - bx[0]) / res).astype(int), 0, Wm - 1); iy = np.clip(((bx[3] - ys) / res).astype(int), 0, Hm - 1)
        landm = mask[iy[:, None], ix[None, :]]
        inside = ((xs >= bx[0]) & (xs <= bx[2]))[None, :] & ((ys >= bx[1]) & (ys <= bx[3]))[:, None]
        landm &= inside
        return Image.fromarray((landm * 255).astype(np.uint8)), (lambda lon, lat: (W2 / 2 + (lon - cx) * k * sc, H2 / 2 - (lat - cy) * sc))
    P = lambda lon, lat: (W2 / 2 + (lon - cx) * k * sc, H2 / 2 - (lat - cy) * sc)
    im = Image.new('L', (W2, H2)); d = ImageDraw.Draw(im)
    for poly in json.load(open(m['land'])):
        if len(poly) > 2: d.polygon([P(x, y) for x, y in poly], fill=255)
    return im, P

def render_map(sh, W, H, fps, a0, dur, out):
    """Route map shot: land in sand on a deep sea, the trip so far as a white line with small labels, this leg drawn in
    from the last stop to the next (eased), the new stop's dot, name and how we got there appear when the line
    arrives; the map drifts in 3 % toward the new stop. Coastline: OpenStreetMap (credited on the frame)."""
    m = sh['map']; stops = m['stops']; leg = m['leg']                     # stops [[lon, lat, name, how]]
    W2, H2 = W * 2, H * 2
    landm, P = _land_layer(m, W2, H2)
    SEA, LAND, ROUTE, DONE = (22, 36, 44), (231, 223, 203), (242, 165, 65), (250, 248, 240)
    soft = landm.filter(ImageFilter.GaussianBlur(1.5))
    base = Image.composite(Image.new('RGB', (W2, H2), LAND), Image.new('RGB', (W2, H2), SEA), soft)
    shade = landm.filter(ImageFilter.GaussianBlur(H2 / 90)).point(lambda v: int(v * 0.35))           # soft glow off the coast
    base = Image.composite(Image.new('RGB', (W2, H2), (44, 66, 76)), base, Image.fromarray(np.minimum(np.asarray(shade), 255 - np.asarray(landm))))
    yy, xx = np.mgrid[0:H2, 0:W2]; vig = 1 - 0.28 * (((xx - W2 / 2) / (W2 / 2)) ** 2 + ((yy - H2 / 2) / (H2 / 2)) ** 2)
    base = Image.fromarray((np.asarray(base, np.float32) * np.clip(vig, 0.6, 1)[..., None]).astype(np.uint8))
    fN = ImageFont.truetype(FONT, round(H2 * 0.036)); fn = ImageFont.truetype(FONT, round(H2 * 0.024))
    fS = ImageFont.truetype(os.path.join(ROOT, 'fonts', 'Outfit[wght].ttf'), round(H2 * 0.017))
    try: fS.set_variation_by_axes([500])
    except Exception: pass
    full = max(0.5, sh['out'] - sh['in']); n = round(dur * fps); f0 = round(a0 * fps); K = max(1, round(full * fps))
    path = [P(x, y) for x, y, *_ in stops]; dxs = m.get('label_dx') or [0.018] * len(stops)
    t_draw0, t_draw1 = 0.35, max(0.9, full * 0.62)
    def lerp(p, q, u): return (p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u)
    def label(fr, xy, text, font, fill, a):
        lay = Image.new('RGBA', (W2, H2)); ld = ImageDraw.Draw(lay)
        ld.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=(0, 0, 0, int(110 * a)))
        lay = lay.filter(ImageFilter.GaussianBlur(4)); ImageDraw.Draw(lay).text(xy, text, font=font, fill=fill[:3] + (int(fill[3] * a),))
        fr.paste(lay, (0, 0), lay)
    pr = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(fps), '-i', '-',
                           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', '-pix_fmt', 'yuv420p', out], stdin=subprocess.PIPE)
    lw = round(H2 * 0.005)
    for j in range(n):
        k = f0 + j; t = k / fps; u = ease((t - t_draw0) / (t_draw1 - t_draw0))
        fr = base.copy(); d = ImageDraw.Draw(fr)
        for q in range(1, leg): d.line([path[q - 1], path[q]], fill=DONE, width=lw)
        if leg >= 1 and u > 0: d.line([path[leg - 1], lerp(path[leg - 1], path[leg], u)], fill=ROUTE, width=lw + 3)
        for q in range(leg + 1):
            if q == leg and u < 1: continue
            cx, cy = path[q]; r = H2 * (0.012 if q == leg else 0.0075)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ROUTE if q == leg else DONE)
        for q in range(leg + 1):
            name, how = stops[q][2], (stops[q][3] if len(stops[q]) > 3 else '')
            cx, cy = path[q]; dx = dxs[q] * W2
            if q < leg: label(fr, (cx + dx, cy - H2 * 0.03), name, fn, (255, 255, 255, 200), 1.0)
            elif u >= 1:
                a = ease((t - t_draw1) / 0.4)
                label(fr, (cx + dx, cy - H2 * 0.05), name, fN, (255, 255, 255, 255), a)
                if how: label(fr, (cx + dx + 3, cy + H2 * 0.0), how, fS, (255, 230, 190, 235), a)
        ImageDraw.Draw(fr).text((W2 - H2 * 0.30, H2 - H2 * 0.04), '© OpenStreetMap contributors' if m.get('mask') else 'Natural Earth',
                                font=ImageFont.truetype(FONT, round(H2 * 0.013)), fill=(200, 200, 200))
        z = 1 + 0.03 * k / K; w, h = W2 / z, H2 / z; fx, fy = path[leg]
        x0 = min(max(0, W2 / 2 + (fx - W2 / 2) * 0.3 - w / 2), W2 - w); y0 = min(max(0, H2 / 2 + (fy - H2 / 2) * 0.3 - h / 2), H2 - h)
        pr.stdin.write(fr.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + w, y0 + h)).tobytes())
    pr.stdin.close(); pr.wait()

def whoosh(sr, dur=0.9):
    """Soft air whoosh (band-passed noise swelling and falling) for a route-map line (no licence needed)."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(7); n = int(dur * sr); x = rng.standard_normal(n).astype(np.float32)
    x = sosfilt(butter(2, [300, 2400], 'band', fs=sr, output='sos'), x).astype(np.float32)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return np.stack([x * env, x * env], 1) / (np.abs(x).max() + 1e-6) * 0.18

def shutter(sr):
    """Synthetic camera shutter: two short filtered clicks (no licence needed)."""
    from scipy.signal import butter, sosfilt
    rng = np.random.default_rng(3); n = int(0.16 * sr); x = np.zeros(n, np.float32)
    for t0, amp, dec in ((0.0, 1.0, 0.006), (0.055, 0.7, 0.012)):
        i = int(t0 * sr); m = n - i; tt = np.arange(m) / sr
        x[i:] += rng.standard_normal(m).astype(np.float32) * np.exp(-tt / dec) * amp
    x = sosfilt(butter(2, [900, 7000], 'band', fs=sr, output='sos'), x).astype(np.float32)
    return np.stack([x, x], 1) / (np.abs(x).max() + 1e-6) * 0.35

class _Keep:
    def __init__(self, d): self.d = d; os.makedirs(d, exist_ok=True)
    def __enter__(self): return self.d
    def __exit__(self, *a): return False

def vo_duration(text): return round(len(text) / 4.3 + 0.6, 2)

VENV = os.path.expanduser('~/.venvs/tts/bin/python')

def track_beats(path, cache):
    """Beat times of a song (librosa, which lives in the tts venv), cached in `cache` (json, keyed by path)."""
    db = json.load(open(cache)) if os.path.exists(cache) else {}
    if path not in db:
        code = ('import sys, json, librosa; y, sr = librosa.load(sys.argv[1], sr=22050, mono=True); '
                '_, b = librosa.beat.beat_track(y=y, sr=sr, units="time"); print(json.dumps([round(float(x), 3) for x in b]))')
        db[path] = json.loads(subprocess.run([VENV, '-c', code, path], check=True, capture_output=True, text=True).stdout)
        json.dump(db, open(cache, 'w'))
    return db[path]

def shot_beats(edl, tl, i, cache):
    """Beats of the song under shot i, in seconds from the shot's start (a photo's mask moves on them)."""
    for m in edl.get('music') or []:
        f, t = m.get('from_shot', 0), m.get('to_shot') if m.get('to_shot') is not None else len(tl) - 1
        if f <= i < t:
            a = tl[f] + m.get('at_offset', 0) - m.get('start', 0)
            return [round(b + a - tl[i], 3) for b in track_beats(m['path'], cache) if tl[i] < b + a < tl[i + 1]]
    return []

def music_track(m, tl, total, N):
    """Envelope for one song between from_shot and to_shot. It comes in over `fade_in` (0.8 s) and is OUT by the
    boundary: the fade (`fade_out`, 1.5 s) ends at to_shot, so songs never run into each other and the cut into the
    next section lands in natural sound. Shot-anchored states switch it between full / low / none inside."""
    a = tl[m.get('from_shot', 0)] + m.get('at_offset', 0)
    b = tl[m['to_shot']] if m.get('to_shot') is not None and m['to_shot'] < len(tl) else total
    states = [[a, 'full']] + [[tl[x['shot']] + x.get('at', 0), x['state']] for x in m.get('states', [])]
    env = music_envelope(states, N, total)
    sr = N / total; t = np.arange(N) / sr
    fi, fo = m.get('fade_in', 0.8), m.get('fade_out', 1.5)
    env *= np.clip((t - a) / fi, 0, 1) * np.clip((b - t) / fo, 0, 1)
    return a, b, env

def text_spans(edl, tl, shots):
    """Subtitle + narration lines as (a, b, text) on the timeline, sorted; each ends before the next one starts."""
    sp = []
    for sub in edl.get('subtitles', []):
        s = shots[sub['shot']]; sp.append([tl[sub['shot']] + max(0, sub['from'] - s['in']), tl[sub['shot']] + min(s['out'], sub['to']) - s['in'] + 0.15, sub['text']])
    for v in edl.get('vo', []):
        a = tl[v['shot']] + v.get('at', 0); sp.append([a, a + v.get('dur', vo_duration(v['text'])), v['text']])
    sp.sort()
    for x, y in zip(sp, sp[1:]): x[1] = min(x[1], y[0] - 0.04)
    return [tuple(x) for x in sp if x[1] - x[0] > 0.2]

def overlays_for(edl, tl, shots, fps, W, H, tmp):
    """All overlays as (png, a, b, fade_in, fade_out) on the timeline."""
    ovs = []
    def add(p, a, b, fd): ovs.append((p, a, b, fd, fd))
    for k, (a, b, text) in enumerate(text_spans(edl, tl, shots)):
        p = f'{tmp}/sub{k}.png'; subtitle_png(text, W, H, p); add(p, a, b, 0.08)
    for k, cd in enumerate(edl.get('cards', [])):
        a = tl[cd['shot']] + cd.get('at', 0); b = a + cd['dur']
        if cd.get('kind') == 'title' and cd.get('style') == 'kinetic': continue     # an alpha movie (kinetic_titles), not a PNG
        if cd.get('kind') == 'title' and cd.get('style') == 'stamp':      # glyph by glyph in, glyph by glyph out
            ps = stamp_title_pngs(cd, W, H, tmp, k); order = np.random.default_rng(k).permutation(len(ps))
            for j, p in enumerate(ps): ovs.append((p, a + j * 0.09, b - order[j] * 0.07, 0.35, 0.3))
            continue
        if cd.get('kind') == 'place':
            p1, p2 = place_pngs(cd, W, H, tmp, k); ovs.append((p1, a, b, 0.4, 0.4)); ovs.append((p2, a + 0.35, b, 0.4, 0.4))
            continue
        p = f'{tmp}/card{k}.png'; card_png(cd, W, H, p); add(p, a, b, {'label': 0.25}.get(cd.get('kind'), 0.35))
    for k, cd in enumerate(edl.get('typewriter', [])):
        a = tl[cd['shot']] + cd.get('at', 0); step = cd.get('step', 0.07); txt = cd['text']
        for j in range(1, len(txt) + 1):
            p = f'{tmp}/tw{k}_{j}.png'; typewriter_png(txt[:j] + '_', cd.get('sub') if j == len(txt) else None, W, H, p, y=cd.get('y', 0.40))
            b = a + j * step if j < len(txt) else a + cd['dur']
            ovs.append((p, a + (j - 1) * step, b, 0.0, 0.0 if j < len(txt) else 0.3))
    return ovs

def kinetic_titles(edl, tl, W, H, fps, tmp):
    """Film titles in the style the user chose (2026-10-27): `style='kinetic'`, `pair` from kinetic_type.PAIRS (default
    'tall': Big Shoulders light capitals over 资源圆体 Light, the same ink width), animated like the user's Bohol title.
    Returns [(alpha movie, start, dur, card index)]."""
    import kinetic_type
    out = []
    for k, cd in enumerate(edl.get('cards', [])):
        if cd.get('kind') == 'title' and cd.get('style') == 'kinetic':
            p = f'{tmp}/ktitle{k}.mov'
            kinetic_type.render(cd.get('pair', 'tall'), p, cd['dur'], cd['text'], cd.get('latin', ''), W, H, fps, y=cd.get('y', 0.5))
            out.append((p, tl[cd['shot']] + cd.get('at', 0), cd['dur'], k))
    return out

def visual_fingerprint(edl):
    """Hash of everything that decides the picture. Same hash = the video of an earlier render can be reused when only
    the sound changed (music swap: a 20-minute render becomes a few minutes)."""
    import hashlib
    keys = ('shots', 'subtitles', 'vo', 'cards', 'typewriter', 'fx', 'look', 'size', 'fps')
    blob = json.dumps({k: edl.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1((blob + picture_code()).encode()).hexdigest()[:16]

def picture_code():
    """The code that draws pictures: everything before main(), main() up to the audio mix (the shot renderer), grade.py."""
    src = open(os.path.abspath(__file__)).read()
    return (src.split('def main():')[0] + src.split('def main():')[1].split('# audio: dialogue/ambience')[0]
            + open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'grade.py')).read())

def frame_count(path):
    r = subprocess.run([FP, '-v', 'error', '-count_packets', '-select_streams', 'v', '-show_entries', 'stream=nb_read_packets',
                        '-of', 'csv=p=0', path], capture_output=True, text=True).stdout.strip()
    return int(r) if r.isdigit() else -1

def slice_edl(edl, a, b):
    """Shots a..b-1 of an EDL with everything that hangs off them re-indexed (render one section to check it)."""
    e = dict(edl); e['shots'] = edl['shots'][a:b]
    for k in ('subtitles', 'vo', 'cards', 'typewriter', 'voices', 'quiet'):    # voices: Bali v7's section render crashed without
        e[k] = [dict(x, shot=x['shot'] - a) for x in edl.get(k, []) if a <= x['shot'] < b]
    e['fx'] = [dict(x, cut=x['cut'] - a) for x in edl.get('fx', []) if a < x['cut'] < b]
    e['music'] = []
    for m in edl.get('music', []):
        f, t = m.get('from_shot', 0), m.get('to_shot') if m.get('to_shot') is not None else len(edl['shots'])
        if t <= a or f >= b: continue
        skip = sum(x['out'] - x['in'] for x in edl['shots'][f:a]) if f < a else 0      # song already running: seek into it
        e['music'].append(dict(m, from_shot=max(f, a) - a, to_shot=(t - a) if t < b else None, start=m.get('start', 0) + skip,
                               at_offset=m.get('at_offset', 0) if f >= a else 0,
                               states=[dict(x, shot=x['shot'] - a) for x in m.get('states', []) if a <= x['shot'] < b]))
    return e

def main():
    edl = json.load(open(sys.argv[1])); out = sys.argv[2]
    reuse = sys.argv[sys.argv.index('--reuse-video') + 1] if '--reuse-video' in sys.argv else None
    if '--shots' in sys.argv:                       # --shots 85:100  renders only that section
        a, b = map(int, sys.argv[sys.argv.index('--shots') + 1].split(':')); edl = slice_edl(edl, a, b)
    workers = int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else 4
    W, H = edl.get('size', [1920, 1080]); fps = edl.get('fps', 30)
    clips = {c['id']: c for c in json.load(open(os.path.join(edl['project'], 'ingest', 'clips.json')))}
    for cid, r in (edl.get('renders') or {}).items():            # sequences rendered elsewhere (edl_builder.rendered)
        clips[cid] = {'id': cid, 'path': r['path'], 'duration': r['duration'], 'audio': True, 'kind': 'render'}
    if '--sources' in sys.argv:                     # local copies made by cache_sources.py (the external drive may drop off)
        src_dir = os.path.expanduser(sys.argv[sys.argv.index('--sources') + 1])
        for c in clips.values():
            local = os.path.join(src_dir, os.path.basename(c['path']))
            if os.path.exists(local): c['path'] = local
        for s in edl['shots']:                      # photos too: Bali v7 stopped on a photo while the drive was out
            local = os.path.join(src_dir, os.path.basename(s.get('photo') or ''))
            if s.get('photo') and os.path.exists(local): s['photo'] = local
            for L in s.get('layers', []):
                local = os.path.join(src_dir, os.path.basename(L.get('photo') or ''))
                if L.get('photo') and os.path.exists(local): L['photo'] = local
    shots = edl['shots']
    starts, t = [], 0
    for s in shots: starts.append(t); t += round((s['out'] - s['in']) * fps)
    total = t / fps; tl = [round(x / fps, 3) for x in starts]; ends = tl[1:] + [total]

    fp = visual_fingerprint(edl)
    if reuse:
        old = open(reuse + '.visual.sha').read().strip() if os.path.exists(reuse + '.visual.sha') else None
        if old != fp: sys.exit(f'--reuse-video: {reuse} was rendered from a different picture (fingerprint {old} != {fp}); render it fully')
    keep = os.environ.get('KEEP_TMP')
    # rendered shot segments live next to the output, keyed by the picture fingerprint: a run that stops half-way
    # (a decode error under load, a crash, a kill) resumes from the shots already done instead of starting over
    # rendered shots are cached next to the output under a hash of everything that draws them (the shot, the one before
    # it for a dissolve, its grade, the overlay images and times on it, the leaks over it, the picture code): a change to
    # three shots re-renders three shots, not the whole film; a run that stops half-way resumes. Absolute path: the
    # concat list is resolved from tmp.
    parts = os.path.abspath(f'{out}.parts'); os.makedirs(parts, exist_ok=True)
    with (tempfile.TemporaryDirectory() if not keep else _Keep(keep)) as tmp:
        from scipy.io import wavfile
        ovs = overlays_for(edl, tl, shots, fps, W, H, tmp)
        leaks = []
        for k, fx in enumerate(edl.get('fx', [])):
            if fx['type'] != 'leak': continue
            d = fx.get('dur', 0.9); a = tl[fx['cut']] - d / 2; p = f'{tmp}/leak{k}.mov'
            make_leak(p, d, fps, seed=k, warmth=fx.get('warmth', 1.0)); leaks.append((p, a, d, k, fx.get('warmth', 1.0)))
        ktitles = kinetic_titles(edl, tl, W, H, fps, tmp)

        # one grade per clip, measured over every range of it the edit uses (two shots of one clip must match)
        clip_grade = {}
        for cid in sorted({s['clip'] for s in shots if not s.get('photo')}):
            uses = [s for s in shots if s['clip'] == cid]
            ts = [t for s in uses for t in np.linspace(s['in'] + 0.1, max(s['in'] + 0.1, s['out'] - 0.1), 3)]
            modes = {s.get('grade', 'auto') for s in uses}
            mode = modes.pop() if len(modes) == 1 else 'auto'
            if mode == 'auto' and clips[cid].get('kind') == 'drone': mode = 'none'   # sea seen from the air reads as "underwater"
            if clips[cid].get('kind') == 'render': clip_grade[cid] = ''; continue    # graded when it was made
            clip_grade[cid] = grade.clip_filter(clips[cid]['path'], ts, mode=mode, look=edl.get('look', 'film'))
        music_on = [False] * len(shots)
        for m in (edl.get('music') or []):
            for k in range(m.get('from_shot', 0), m['to_shot'] if m.get('to_shot') is not None else len(shots)): music_on[k] = True

        import hashlib
        png_md5 = {}; code_fp = hashlib.sha1(picture_code().encode()).hexdigest()
        beats = {i: shot_beats(edl, tl + [total], i, os.path.join(edl['project'], 'ingest', 'beats.json'))
                 for i, s in enumerate(shots) if s.get('mask')}
        def shot_key(i):
            s = shots[i]; t0, t1 = tl[i], ends[i]; h = hashlib.sha1(code_fp.encode())   # not the whole-EDL fingerprint: an edit elsewhere must not re-render this shot
            h.update(json.dumps([s, beats.get(i), shots[i - 1] if i and s.get('xfade') else None, clip_grade.get(s['clip']),
                                 clip_grade.get(shots[i - 1]['clip']) if i and s.get('xfade') else None,
                                 round((s['out'] - s['in']) * fps), i == 0, i == len(shots) - 1], sort_keys=True, default=str).encode())
            for p, a, b, fi, fo in ovs:
                if b <= t0 or a >= t1: continue
                if p not in png_md5: png_md5[p] = hashlib.md5(open(p, 'rb').read()).hexdigest()
                h.update(f'{png_md5[p]} {a - t0:.3f} {b - t0:.3f} {fi} {fo}'.encode())
            for p, a, d, k, warm in leaks:
                if a + d <= t0 or a >= t1: continue
                h.update(f'leak {k} {warm} {d:.3f} {a - t0:.3f}'.encode())
            for p, a, d, k in ktitles:
                if a + d <= t0 or a >= t1: continue
                h.update(f'ktitle {json.dumps(edl["cards"][k], sort_keys=True, ensure_ascii=False)} {a - t0:.3f}'.encode())
            return h.hexdigest()[:20]
        def render_shot(i):
            s = shots[i]; n = round((s['out'] - s['in']) * fps); t0, t1 = tl[i], ends[i]
            dst = f'{parts}/s_{shot_key(i)}.mp4'
            tail = ((f",fade=in:st=0:d={s['fade_from_black']}" if s.get('fade_from_black') else '')
                    + (',fade=in:st=0:d=0.3' if i == 0 else '') + (f',fade=out:st={max(0, n / fps - 0.8):.3f}:d=0.8' if i == len(shots) - 1 else ''))
            def source(sh, a0, dur):
                """(input args, filter chain) giving `dur` seconds of shot `sh` from source time a0, graded, at W x H."""
                if sh.get('map'):
                    mv = f"{tmp}/map_{i}_{round(a0 * fps)}_{round(dur * fps)}.mp4"
                    if not os.path.exists(mv): render_map(sh, W, H, fps, a0, dur + 0.2, mv)
                    return ['-i', mv], 'setsar=1'
                if sh.get('photo'):
                    # frames drawn in PIL with sub-pixel crop boxes: ffmpeg zoompan rounds the crop to whole pixels
                    # every frame and the photo visibly shivers (Bali v1: "所有的照片还有新闻的截图，画面都是颤抖的")
                    mv = f"{tmp}/still_{sh['clip']}_{round(a0 * fps)}_{round(dur * fps)}.mp4"
                    if not os.path.exists(mv): render_still(sh, W, H, fps, a0, dur + 0.2, mv, tmp, clips, beats.get(shots.index(sh)))
                    return ['-i', mv], 'setsar=1'
                cc = clips[sh['clip']]
                gf = clip_grade[sh['clip']]
                return (['-ss', f'{a0:.3f}', '-t', f'{dur + 0.2:.3f}', '-i', cc['path']],
                        f'scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p' + (',' + gf if gf else ''))
                # format=yuv420p: the underwater chain turns 10-bit HEVC (DJI drone) to black - grade in 8 bit
            src, chain = source(s, s['in'], s['out'] - s['in'])
            flash = ',fade=in:st=0:d=0.18:color=white' if s.get('photo') and not s.get('news') and not s.get('map') and not s.get('mask') else ''
            soft = f",gblur=sigma={s['blur'] * H / 1080:.1f}" if s.get('blur') else ''   # video under laid-over photos: out of focus
            inputs, fc, last, idx = list(src), [f'[0:v]{chain}{soft},fps={fps},trim=end_frame={n},setpts=PTS-STARTPTS{flash}{tail}[b]'], 'b', 1
            if s.get('layers'):                               # photos laid over this video shot (render_layers)
                mv = f'{tmp}/layers_{i}.mov'; render_layers(s, W, H, fps, n, mv)
                inputs += ['-i', mv]; fc.append(f'[{idx}:v]format=rgba,setpts=PTS-STARTPTS[o{idx}]')
                fc.append(f'[{last}][o{idx}]overlay=0:0:eof_action=pass[v{idx}]'); last = f'v{idx}'; idx += 1
            xd = s.get('xfade', 0) if i > 0 else 0
            if xd:                                            # dissolve: previous shot keeps running under this one, fading out
                prev = shots[i - 1]; pa, pch = source(prev, prev['out'], xd)
                inputs += pa
                fc.append(f'[{idx}:v]{pch},fps={fps},trim=end_frame={round(xd * fps)},setpts=PTS-STARTPTS,format=rgba,fade=out:st=0:d={xd}:alpha=1[x{idx}]')
                fc.append(f'[{last}][x{idx}]overlay=0:0:eof_action=pass[v{idx}]'); last = f'v{idx}'; idx += 1
            for p, a, b, fi, fo in ovs:                       # overlays local to this shot
                if b <= t0 or a >= t1: continue
                la, lb = a - t0, b - t0
                if min(lb, t1 - t0) - max(0, la) < 0.05: continue          # a tail of a few frames: skip
                chain = 'format=rgba'
                if fi and la >= 0: chain += f',fade=in:st={la:.3f}:d={fi}:alpha=1'
                if fo and lb <= t1 - t0 + 1e-3: chain += f',fade=out:st={max(0.0, la, lb - fo):.3f}:d={fo}:alpha=1'
                inputs += ['-loop', '1', '-framerate', str(fps), '-t', f'{t1 - t0 + 0.1:.3f}', '-i', p]
                fc.append(f'[{idx}:v]{chain}[o{idx}]')
                fc.append(f"[{last}][o{idx}]overlay=0:0:enable='between(t,{max(0, la):.3f},{lb:.3f})'[v{idx}]"); last = f'v{idx}'; idx += 1
            for p, a, d, _, _ in leaks:                        # leaks that touch this shot
                if a + d <= t0 or a >= t1: continue
                # leak already running at this shot's start: seek into it; starting later: shift its timestamps in the graph
                # (-itsoffset on the input was ignored here and the first half of every leak went missing)
                inputs += (['-ss', f'{t0 - a:.3f}'] if a < t0 else []) + ['-i', p]
                shift = f',setpts=PTS-STARTPTS+{a - t0:.3f}/TB' if a >= t0 else ',setpts=PTS-STARTPTS'
                fc.append(f'[{idx}:v]scale={W}:{H},format=rgba,gblur=sigma={round(28 * H / 1080)}{shift}[o{idx}]')
                fc.append(f'[{last}][o{idx}]overlay=0:0:eof_action=pass[v{idx}]'); last = f'v{idx}'; idx += 1
            for p, a, d, _ in ktitles:                         # the film title, an alpha movie that may span a cut
                if a + d <= t0 or a >= t1: continue
                inputs += (['-ss', f'{t0 - a:.3f}'] if a < t0 else []) + ['-i', p]
                shift = f',setpts=PTS-STARTPTS+{a - t0:.3f}/TB' if a >= t0 else ',setpts=PTS-STARTPTS'
                fc.append(f'[{idx}:v]format=rgba{shift}[o{idx}]')
                fc.append(f'[{last}][o{idx}]overlay=0:0:eof_action=pass[v{idx}]'); last = f'v{idx}'; idx += 1
            enc = inputs + ['-filter_complex', ';'.join(fc), '-map', f'[{last}]', '-an',
                   '-frames:v', str(n), '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-maxrate', '16M', '-bufsize', '32M', '-bf', '0', '-g', str(fps),
                   '-pix_fmt', 'yuv420p', '-r', str(fps), '-video_track_timescale', str(fps * 1000), dst]
            if not reuse and not (os.path.exists(dst) and frame_count(dst) == n):
                # under full parallel load the hardware decoder once dropped 19 frames from one shot, and once failed a
                # shot outright (exit 183, Bali v1) that renders fine on its own - retry in software, stop if still wrong
                try: run([FF, '-y', '-hwaccel', 'videotoolbox'] + enc); ok = frame_count(dst) == n
                except subprocess.CalledProcessError: ok = False
                src_path = clips.get(s['clip'], {}).get('path')
                if not ok and src_path and not os.path.exists(src_path):     # the external drive dropped off: wait for it
                    import time
                    for _ in range(60):
                        if os.path.exists(src_path): break
                        time.sleep(5)
                if not ok:
                    run([FF, '-y'] + enc)
                    if frame_count(dst) != n: raise RuntimeError(f'shot {i} {s["clip"]}: {frame_count(dst)} frames rendered, {n} planned')
            ns = round(n / fps * SR); g = AUDIO_GAIN[s.get('audio', 'ambient')]
            if s.get('audio', 'ambient') == 'ambient' and not music_on[i]: g = AMBIENT_NO_MUSIC
            seg = np.zeros((ns, 2), np.float32)
            c = clips.get(s['clip'], {'audio': False})
            if s.get('map'):
                sfx = whoosh(SR); at = int(0.35 * SR); m_ = min(len(sfx), ns - at)
                if m_ > 0: seg[at:at + m_] += sfx[:m_]
            elif s.get('photo') and not s.get('news'):
                sfx = shutter(SR); seg[:len(sfx)] += sfx[:ns]
            for L in s.get('layers', []):                      # each photo laid over the video clicks in
                if L.get('photo') and int(L['at'] * SR) < ns:
                    sfx = shutter(SR); at = int(L['at'] * SR); seg[at:at + len(sfx)] += sfx[:ns - at]
            if s.get('bed'):
                bw = f'{tmp}/bed{i:04d}.wav'
                run([FF, '-y', '-ss', f"{s['bed']['in']:.3f}", '-t', f'{n / fps:.3f}', '-i', clips[s['bed']['clip']]['path'], '-vn', '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_s16le', bw])
                _, bx = wavfile.read(bw); bx = bx.astype(np.float32) / 32768; m = min(ns, len(bx))
                bx *= min(8.0, BED_RMS / (np.sqrt(np.mean(bx ** 2)) + 1e-9))     # quiet night sound is levelled up, not lost
                bx = bx[:m] * np.clip(np.minimum(np.arange(m), m - np.arange(m)) / (0.3 * SR), 0, 1)[:, None]; seg[:m] += bx
            if xd and clips.get(shots[i - 1]['clip'], {}).get('audio') and AUDIO_GAIN[shots[i - 1].get('audio', 'ambient')] > 0:
                prev = shots[i - 1]; pw = f'{tmp}/x{i:04d}.wav'
                run([FF, '-y', '-ss', f"{prev['out']:.3f}", '-t', f'{xd:.3f}', '-i', clips[prev['clip']]['path'], '-vn', '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_s16le', pw])
                _, px = wavfile.read(pw); px = px.astype(np.float32) / 32768 * AUDIO_GAIN[prev.get('audio', 'ambient')]
                m = min(len(px), ns); seg[:m] += px[:m] * np.linspace(1, 0, m)[:, None]
            if c['audio'] and g > 0 and not s.get('photo'):
                aw = f'{tmp}/a{i:04d}.wav'
                run([FF, '-y'] + src + ['-vn', '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_s16le', aw])
                _, x = wavfile.read(aw); x = x.astype(np.float32) / 32768 * g
                k = min(ns, len(x)); x = x[:k].copy()
                # fade in (longer under a dissolve, but short on live speech so the first word is not swallowed), 30 ms out
                fi = min(int(((min(xd, 0.12) if s.get('audio') == 'live' else xd) or 0.03) * SR), k // 2); fo = min(int(0.03 * SR), k // 2)
                if fi: x[:fi] *= np.linspace(0, 1, fi)[:, None]
                if fo: x[k - fo:] *= np.linspace(1, 0, fo)[:, None]
                seg[:k] += x
            return dst, seg

        ex = ThreadPoolExecutor(workers); futs = [ex.submit(render_shot, i) for i in range(len(shots))]
        try: done = [f.result() for f in futs]
        except BaseException:          # stop at the first failed shot instead of rendering every other shot first
            ex.shutdown(wait=False, cancel_futures=True); raise
        ex.shutdown()
        vsegs = [d for d, _ in done]; audio = [a for _, a in done]
        if reuse:      # picture unchanged (same fingerprint): take the video stream of the earlier render as it is
            run([FF, '-y', '-i', reuse, '-map', '0:v', '-c', 'copy', f'{tmp}/video.mp4'])
        else:
            with open(f'{tmp}/list.txt', 'w') as fh: fh.writelines(f"file '{p}'\n" for p in vsegs)
            run([FF, '-y', '-f', 'concat', '-safe', '0', '-i', f'{tmp}/list.txt', '-c', 'copy', f'{tmp}/video.mp4'])

        # audio: dialogue/ambience + songs, ducked under speech and narration, loudness-normalised on its own
        mix = np.concatenate(audio); N = len(mix); sr_t = N / total; t_ax = np.arange(N) / sr_t
        # J-cut (`jcut`: the shot's own sound starts that many seconds before its picture, under the previous shot) and
        # L-cut (`lcut`: its sound runs on under the next shot). The picture cuts hard, the sound carries the viewer
        # across - "the cut is abrupt" in Bali v1 came from picture and sound changing on the same frame.
        def own_gain(i):
            sh = shots[i]; g = AUDIO_GAIN[sh.get('audio', 'ambient')]
            return AMBIENT_NO_MUSIC if sh.get('audio', 'ambient') == 'ambient' and not music_on[i] else g
        for i, sh in enumerate(shots):
            c = clips.get(sh['clip'], {})
            if sh.get('photo') or not c.get('audio'): continue
            for kind in ('jcut', 'lcut'):
                d = sh.get(kind, 0)
                if not d or (kind == 'jcut' and i == 0) or (kind == 'lcut' and i == len(shots) - 1): continue
                src0 = sh['in'] - d if kind == 'jcut' else sh['out']
                if src0 < 0: d += src0; src0 = 0.0
                if d <= 0.05: continue
                w = f'{tmp}/{kind}{i:04d}.wav'
                run([FF, '-y', '-ss', f'{src0:.3f}', '-t', f'{d:.3f}', '-i', c['path'], '-vn', '-ac', '2', '-ar', str(SR), '-c:a', 'pcm_s16le', w])
                _, x = wavfile.read(w); x = x.astype(np.float32) / 32768 * own_gain(i)
                at = int((tl[i] - d) * sr_t) if kind == 'jcut' else int(ends[i] * sr_t); m = min(len(x), N - at)
                if m <= 0: continue
                ramp = np.linspace(0, 1, m) if kind == 'jcut' else np.linspace(1, 0, m)
                mix[at:at + m] = mix[at:at + m] * (1 - 0.6 * ramp)[:, None] + x[:m] * ramp[:, None]
        # the user's recorded narration (`voices`, edl_builder.use_narration): one cleaned take per paragraph, placed
        # where its subtitles start; everything under it - the shots' own sound and the music - is measured against the
        # voice and held NARRATION_OVER_MUSIC_DB below it
        NARRATION_OVER_MUSIC_DB = 12            # here, not at the top: the top of the file is part of the picture fingerprint
        rms = lambda x: float(np.sqrt(np.mean(x ** 2)) + 1e-9)
        voice = np.zeros_like(mix); vspans = []
        for vc in edl.get('voices', []):
            _, x = wavfile.read(vc['path']); x = x.astype(np.float32) / 32768
            if x.ndim == 1: x = np.repeat(x[:, None], 2, axis=1)
            at = int((tl[vc['shot']] + vc['at']) * sr_t); m = min(len(x), N - at)
            if m > 0: voice[at:at + m] += x[:m]; vspans.append((at, at + m))
        # the shots' own sound under the narration: a beach club's music on the B-roll sat 3 dB under the voice (Bali
        # v7, Finns) - only the music used to be ducked. 0.3 s ramps, like the music
        own = np.ones(N, np.float32)
        for a0, b0 in vspans:
            f = min(1.0, 10 ** (-NARRATION_OVER_MUSIC_DB / 20) * rms(voice[a0:b0]) / rms(mix[a0:b0]))
            if f < 1:
                g = np.clip(np.minimum((t_ax - (a0 / sr_t - 0.3)) / 0.3, ((b0 / sr_t + 0.3) - t_ax) / 0.3), 0, 1)
                own = np.minimum(own, 1 - (1 - f) * g)
        mix = mix * own[:, None]; bed = mix.copy()
        mix = mix + voice
        tracks = edl.get('music') or []
        if isinstance(tracks, dict): tracks = [dict(tracks, from_shot=tracks.get('at_shot', 0))]
        music = np.zeros_like(mix)
        for k, m in enumerate(tracks):
            a, b, env = music_track(m, tl, total, N)
            mw = f'{tmp}/music{k}.wav'
            run([FF, '-y', '-ss', str(m.get('start', 0)), '-t', f'{b - a + 2:.3f}', '-i', m['path'], '-ac', '2', '-ar', str(SR), mw])
            _, x = wavfile.read(mw); x = x.astype(np.float32) / 32768
            blk = np.sqrt(np.mean(x[: len(x) // 4800 * 4800].reshape(-1, 4800, 2) ** 2, axis=(1, 2)))   # 0.1 s blocks
            x *= MUSIC_RMS / (np.sqrt(np.mean(np.sort(blk)[len(blk) // 2:] ** 2)) + 1e-9)          # louder half -> same level for every song
            lead = int(a * sr_t); x = np.pad(x, ((lead, max(0, N - len(x) - lead)), (0, 0)))[:N]
            music += x * env[:, None] * m.get('gain', 0.55)
        duck = np.ones(N, np.float32)
        spans = []
        for i, s in enumerate(shots):          # live speech: music at least VOICE_OVER_MUSIC_DB under the measured voice
            if s.get('audio') != 'live': continue
            j = slice(int(tl[i] * sr_t), int(ends[i] * sr_t)); v, mu = rms(mix[j]), rms(music[j])
            spans.append((tl[i], ends[i], min(DUCK, 10 ** (-VOICE_OVER_MUSIC_DB / 20) * v / mu) if mu > 1e-6 else DUCK, True))
        spans += [(tl[v['shot']] + v.get('at', 0) - 0.25, tl[v['shot']] + v.get('at', 0) + v.get('dur', vo_duration(v['text'])) + 0.35, DUCK_VO, False)
                  for v in edl.get('vo', [])]
        for a0, b0 in vspans:                   # recorded narration: music NARRATION_OVER_MUSIC_DB under the measured voice
            v, mu = rms(voice[a0:b0]), rms(music[a0:b0])
            spans.append((a0 / sr_t, b0 / sr_t, min(DUCK_VO, 10 ** (-NARRATION_OVER_MUSIC_DB / 20) * v / mu) if mu > 1e-6 else DUCK_VO, False))
        for a, b, f, live in spans:
            # 0.3 s down before speech; after a talk shot the music swells back over the first second of the next shot,
            # so the B-roll comes out on the music. Starting the swell inside the talk shot (Bali v6, 0.6 s early) let a
            # full-level song cover quiet voices: five talk shots fell to 5-14 dB over the music
            up = ((b + 1.0) - t_ax) / 1.0 if live else ((b + 0.3) - t_ax) / 0.3
            g = np.clip(np.minimum((t_ax - (a - 0.3)) / 0.3, up), 0, 1)
            duck = np.minimum(duck, 1 - (1 - max(f, 0.02)) * g)
        # no-narration moments (the edit's `quiet`): the music lifts 3 dB over 1 s ramps - the moment to just watch and
        # listen (Bali v6: "衬托氛围的情况，音乐声可以大一些")
        QUIET_LIFT = 10 ** (3 / 20)            # here, not at the top: the top of the file is part of the picture fingerprint
        for q in edl.get('quiet', []):
            a = tl[q['shot']] + q.get('at', 0); b = a + q['dur']
            g = np.clip(np.minimum((t_ax - (a - 1.0)) / 1.0, ((b + 1.0) - t_ax) / 1.0), 0, 1)
            duck = duck * (1 + (QUIET_LIFT - 1) * g)
        voice_db = []
        for i, s in enumerate(shots):
            if s.get('audio') != 'live': continue
            j = slice(int(tl[i] * sr_t), int(ends[i] * sr_t)); mu = rms(music[j] * duck[j, None])
            if mu > 1e-5: voice_db.append((i, 20 * np.log10(rms(mix[j]) / mu)))
        json.dump(voice_db, open(out + '.voice.json', 'w'))
        narr_db = [(k, 20 * np.log10(rms(voice[a0:b0]) / rms(bed[a0:b0] + music[a0:b0] * duck[a0:b0, None]))) for k, (a0, b0) in enumerate(vspans)
                   if rms(bed[a0:b0] + music[a0:b0] * duck[a0:b0, None]) > 1e-5]
        json.dump(narr_db, open(out + '.narration.json', 'w'))
        mix = mix + music * duck[:, None]
        wavfile.write(f'{tmp}/mix.wav', SR, (np.clip(mix, -1, 1) * 32767).astype(np.int16))
        # two-pass loudness: measure, then one linear gain (no pumping) + limiter. The old one-pass dynamic loudnorm
        # followed by the limiter landed anywhere from -16.6 to -16.2 LUFS and once fell outside the check window.
        r = subprocess.run([FF, '-hide_banner', '-i', f'{tmp}/mix.wav', '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json',
                            '-f', 'null', '-'], capture_output=True, text=True).stderr
        ms = json.loads(r[r.rindex('{'):r.rindex('}') + 1])
        ln = (f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={ms['input_i']}:measured_TP={ms['input_tp']}:measured_LRA={ms['input_lra']}"
              f":measured_thresh={ms['input_thresh']}:offset={ms['target_offset']}:linear=true")
        run([FF, '-y', '-i', f'{tmp}/mix.wav', '-af', f'{ln},aresample={SR},alimiter=limit=0.79:level=false', '-c:a', 'pcm_f32le', f'{tmp}/final.wav'])
        run([FF, '-y', '-i', f'{tmp}/video.mp4', '-i', f'{tmp}/final.wav', '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
             '-c:a', 'aac_at', '-b:a', '256k', '-t', f'{total:.3f}', '-movflags', '+faststart', out])   # 192k AAC overshot the peak by 2.5 dB
    json.dump([{'shot': i, 'clip': s['clip'], 'at': tl[i], 'dur': round((s['out'] - s['in']), 3), 'note': s.get('note', '')} for i, s in enumerate(shots)],
              open(out + '.timeline.json', 'w'), ensure_ascii=False, indent=0)
    open(out + '.visual.sha', 'w').write(fp)
    print(f'{out}: {total:.2f}s, {len(shots)} shots, {len(ovs)} overlays')
    ok = verify(out, edl, clips, tl, total)
    if ok:                             # segments are only kept to resume an interrupted or failed run
        keep = {os.path.basename(p) for p in vsegs}       # drop cached shots this cut no longer uses
        for f in os.listdir(parts):
            if f not in keep: os.remove(os.path.join(parts, f))
    sys.exit(0 if ok else 1)

def frame_luma(path, t):
    """Mean luma (0-255) of one frame at t seconds (t=None: a still image), on a 64x36 thumbnail."""
    cmd = [FF, '-v', 'error'] + (['-ss', f'{t:.3f}'] if t is not None else []) + ['-i', path, '-frames:v', '1', '-vf', 'scale=64:36,format=gray', '-f', 'rawvideo', '-']
    raw = subprocess.run(cmd, capture_output=True).stdout
    return float(np.frombuffer(raw, np.uint8).mean()) if raw else None

def verify(out, edl, clips, tl, total):
    """Self-check before anyone watches it: cuts where planned, audio as long as video, live sound in sync."""
    from scipy.signal import correlate
    ok = True; shots = edl['shots']
    # a. audio length
    raw = subprocess.run([FF, '-v', 'error', '-i', out, '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32); alen = len(a) / 16000
    vf = frame_count(out); good = abs(alen - total) < 0.06 and vf == round(total * edl.get('fps', 30)); ok &= good
    print(f"  {'✓' if good else '✗'} audio length {alen:.2f}s, video {vf} frames, planned {total:.2f}s = {round(total * edl.get('fps', 30))} frames")
    # b. cuts: frame difference at each planned cut must stand out from its neighbours.
    #    (ffmpeg scdet missed real cuts once film grain was added - it scores the change in difference, not the difference)
    fps = edl.get('fps', 30)
    raw = subprocess.run([FF, '-v', 'error', '-i', out, '-vf', 'scale=64:36,format=gray', '-f', 'rawvideo', '-'], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 36, 64).astype(np.float32)
    d = np.r_[0, np.abs(np.diff(fr, axis=0)).mean((1, 2))]
    missed = []
    for i in range(1, len(shots)):
        k = round(tl[i] * fps)
        if k >= len(d) - 2: continue
        if shots[i]['clip'] == shots[i - 1]['clip'] and abs(shots[i]['in'] - shots[i - 1]['out']) < 0.1: continue   # continuous take
        if shots[i].get('xfade'): continue                                                                       # dissolve, no hard cut
        if fr[k].mean() < 15 and fr[k - 1].mean() < 15: continue                                                 # black to black
        loc = np.median(np.r_[d[max(1, k - 10):k - 1], d[k + 2:k + 11]])
        if not (d[k] > 3 and d[k] > 2.5 * (loc + 0.5)): missed.append(i)
    ok &= not missed
    print(f"  {'✓' if not missed else '✗'} cuts: {len(shots) - 1 - len(missed)}/{len(shots) - 1} land on their planned frame" + (f', not found at shots {missed}' if missed else ''))
    # c. live sound in sync with its source
    for i, s in enumerate(shots):
        if s.get('audio') != 'live' or not clips.get(s['clip'], {}).get('audio'): continue
        src = np.frombuffer(subprocess.run([FF, '-v', 'error', '-ss', f'{max(0, s["in"] - 0.5):.3f}', '-t', f'{s["out"] - s["in"] + 1:.3f}',
              '-i', clips[s['clip']]['path'], '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True).stdout, np.int16).astype(np.float32)
        seg = a[int((tl[i] + 0.1) * 16000):int((tl[i] + s['out'] - s['in'] - 0.1) * 16000)]
        if len(seg) < 4000 or len(src) <= len(seg): continue
        c = correlate(src, seg, mode='valid', method='fft'); k = int(np.argmax(np.abs(c)))
        found = max(0, s['in'] - 0.5) + k / 16000 - 0.1; off = (found - s['in']) * 1000
        good = abs(off) < 40; ok &= good
        print(f"  {'✓' if good else '✗'} shot {i:2d} {s['clip']} live sound offset {off:+.0f} ms")
    for k, fx in enumerate(f for f in edl.get('fx', []) if f['type'] == 'leak'):
        t, dd = tl[fx['cut']], fx.get('dur', 0.9)
        def warm(a, b):
            raw = subprocess.run([FF, '-v', 'error', '-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', out, '-vf', 'scale=64:36',
                                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
            px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(float); return (px[:, 0] - px[:, 2]).mean() if len(px) else 0
        # compare each shot with itself (the two shots around a cut have different colours)
        before = warm(t - 0.2, t - 0.03) - warm(max(0, t - dd / 2 - 0.3), max(0.02, t - dd / 2 - 0.02))
        after = warm(t + 0.03, t + 0.2) - warm(t + dd / 2 + 0.05, t + dd / 2 + 0.3)
        good = max(before, after) > 8; ok &= good
        print(f"  {'✓' if good else '✗'} light leak at cut {fx['cut']} ({t:.2f}s): warmer by +{before:.0f} before / +{after:.0f} after the cut")
    # d. speech over music: every live shot's voice at least 15 dB over the music under it
    vp = out + '.voice.json'
    if os.path.exists(vp):
        low = [(i, db) for i, db in json.load(open(vp)) if db < 15]
        ok &= not low
        print(f"  {'✓' if not low else '✗'} live speech over music: " + (f'all {len(json.load(open(vp)))} shots >= 15 dB' if not low else ', '.join(f'shot {i} {db:.0f} dB' for i, db in low)))
    # d1. recorded narration over everything under it (the shots' own sound and the music): at least 10 dB
    npth = out + '.narration.json'
    if os.path.exists(npth) and edl.get('voices'):
        nd = json.load(open(npth)); low = [(k, db) for k, db in nd if db < 10]
        ok &= not low
        print(f"  {'✓' if not low else '✗'} narration over the sound under it: " + (f'all {len(nd)} paragraphs >= 10 dB (lowest {min(db for _, db in nd):.0f} dB)' if not low else ', '.join(f'paragraph {k + 1} {db:.0f} dB' for k, db in low)))
    # d2. no dead air: no 1.5 s stretch quieter than -48 dBFS (except the first second and the last 4 s, where the
    # closing song is meant to ring out on its own last notes - end_on_last_bar)
    blk = int(0.5 * 16000); lv = [20 * np.log10(np.sqrt(np.mean(a[k:k + blk] ** 2)) / 32768 + 1e-9) for k in range(0, len(a) - blk, blk)]
    dead = [k * 0.5 for k in range(2, len(lv) - 8) if max(lv[k:k + 3]) < -48]
    ok &= not dead
    print(f"  {'✓' if not dead else '✗'} no silent stretches" + (f': silent around {", ".join(f"{int(t // 60)}:{t % 60:04.1f}" for t in dead[:10])}' if dead else ''))
    # e. loudness and true peak of the delivered file (AAC overshoots; a peak above 0 dBFS clips on playback)
    r = subprocess.run([FF, '-hide_banner', '-i', out, '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    I = float(re.findall(r'I:\s+(-?[0-9.]+) LUFS', r)[-1]); pk = float(re.findall(r'Peak:\s+(-?[0-9.]+) dBFS', r)[-1])
    good = pk <= -0.5 and -16.5 <= I <= -12.5; ok &= good
    print(f"  {'✓' if good else '✗'} loudness {I:.1f} LUFS, true peak {pk:+.1f} dBFS")
    # f. no shot goes black where its source is not dark: a grade that breaks on one pixel format (10-bit drone footage
    # through the underwater chain, Bali v1) rendered seven shots pure black and every other check passed
    black = []
    for i, s in enumerate(shots):
        mid = tl[i] + (s['out'] - s['in']) / 2
        r = frame_luma(out, mid)
        src = frame_luma(s['photo'], None) if s.get('photo') else frame_luma(clips[s['clip']]['path'], s['in'] + (s['out'] - s['in']) / 2)
        if r is not None and src is not None and r < 12 and src > 35: black.append(f"{i} {s['clip']} ({r:.0f} vs source {src:.0f})")
    ok &= not black
    print(f"  {'✓' if not black else '✗'} no shot black where its source is not: {', '.join(black) if black else 'all shots'}")
    print('  verify:', 'PASS' if ok else 'FAIL')
    return ok

if __name__ == '__main__':
    main()
