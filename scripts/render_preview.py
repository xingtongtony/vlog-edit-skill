"""Render a preview (or a whole film) from an edit decision list (EDL).

usage: python3 render_preview.py <edl.json> <out.mp4> [--workers N] [--shots A:B] [--reuse-video <earlier render.mp4>]

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
level); under narration it is ducked -13 dB. Shots with `ambient` sound play their natural sound louder (0.5) where
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
DUCK = 0.22          # music gain under speech / narration
AUDIO_GAIN = {'live': 1.0, 'half': 0.45, 'ambient': 0.12, 'mute': 0.0}
MUSIC_RMS = 10 ** (-20 / 20)  # every song is levelled to this before its gain: a loud master must not bury speech
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
    if kind == 'title':      # script Latin name + brush Chinese name + tracked caps line (studied: Links, Char)
        def fn(d, fill):
            big = ft('PinyonScript-Regular.ttf', 230); w = d.textlength(card.get('latin', 'Bohol'), font=big); x = (W - w) / 2; y = H * card.get('y', 0.30)
            d.text((x, y), card.get('latin', 'Bohol'), font=big, fill=fill)
            d.text((x + w - 40 * s, y + 230 * s), text, font=ft('MaShanZheng-Regular.ttf', 96), fill=fill)
            cap = ft('Montserrat[wght].ttf', 22, 500); sub = card.get('sub', '')
            spaced(d, (W - spaced_w(sub, cap, 8 * s)) / 2, y + 380 * s, sub, cap, fill, 8 * s)
        im = shadowed(fn, 10 * s)
    elif kind == 'end':      # script line + small caps
        def fn(d, fill):
            big = ft('PinyonScript-Regular.ttf', 150); w = d.textlength(text, font=big); y = H * card.get('y', 0.36)
            d.text(((W - w) / 2, y), text, font=big, fill=fill)
            cap = ft('Montserrat[wght].ttf', 22, 500); sub = card.get('sub', '')
            spaced(d, (W - spaced_w(sub, cap, 8 * s)) / 2, y + 200 * s, sub, cap, fill, 8 * s)
        im = shadowed(fn, 10 * s)
    elif kind == 'chapter':  # bottom-left: italic "Chapter One" / brush title / tracked caps line
        x, y = W * 0.07, H * 0.60
        def fn(d, fill):
            d.text((x, y), card.get('kicker', ''), font=ft('PlayfairDisplay-Italic[wght].ttf', 40, 400), fill=fill)
            d.text((x - 4 * s, y + 52 * s), text, font=ft('MaShanZheng-Regular.ttf', 112), fill=fill)
            if card.get('sub'):
                cap = ft('Montserrat[wght].ttf', 20, 500); spaced(d, x + 2 * s, y + 200 * s, card['sub'], cap, fill, 7 * s)
        im = shadowed(fn, 9 * s)
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
        f = ImageFont.truetype(os.path.join(ROOT, 'fonts', 'Montserrat[wght].ttf'), round(H2 * 0.016))
        tw = spaced_w(caption, f, H2 * 0.006); x, y = (W2 - tw) / 2, H2 * 0.90
        halo = Image.new('L', (W2, H2), 0); spaced(ImageDraw.Draw(halo), x, y, caption, f, 255, H2 * 0.006)
        halo = halo.filter(ImageFilter.MaxFilter(2 * round(H2 / 300) + 1)).filter(ImageFilter.GaussianBlur(H2 / 120))
        bg.paste(Image.new('RGB', (W2, H2), (0, 0, 0)), (0, 0), halo.point(lambda v: min(255, int(v * 1.4))))
        spaced(ImageDraw.Draw(bg), x, y, caption, f, (240, 237, 230), H2 * 0.006)
    bg.save(out, quality=95)

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
        p = f'{tmp}/card{k}.png'; card_png(cd, W, H, p); add(p, a, b, 0.35 if cd.get('kind') != 'label' else 0.25)
    for k, cd in enumerate(edl.get('typewriter', [])):
        a = tl[cd['shot']] + cd.get('at', 0); step = cd.get('step', 0.07); txt = cd['text']
        for j in range(1, len(txt) + 1):
            p = f'{tmp}/tw{k}_{j}.png'; typewriter_png(txt[:j] + '_', cd.get('sub') if j == len(txt) else None, W, H, p, y=cd.get('y', 0.40))
            b = a + j * step if j < len(txt) else a + cd['dur']
            ovs.append((p, a + (j - 1) * step, b, 0.0, 0.0 if j < len(txt) else 0.3))
    return ovs

def visual_fingerprint(edl):
    """Hash of everything that decides the picture. Same hash = the video of an earlier render can be reused when only
    the sound changed (music swap: a 20-minute render becomes a few minutes)."""
    import hashlib
    keys = ('shots', 'subtitles', 'vo', 'cards', 'typewriter', 'fx', 'look', 'size', 'fps')
    blob = json.dumps({k: edl.get(k) for k in keys}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1((blob + open(os.path.abspath(__file__)).read().split('def main():')[0]).encode()).hexdigest()[:16]

def frame_count(path):
    r = subprocess.run([FP, '-v', 'error', '-count_packets', '-select_streams', 'v', '-show_entries', 'stream=nb_read_packets',
                        '-of', 'csv=p=0', path], capture_output=True, text=True).stdout.strip()
    return int(r) if r.isdigit() else -1

def slice_edl(edl, a, b):
    """Shots a..b-1 of an EDL with everything that hangs off them re-indexed (render one section to check it)."""
    e = dict(edl); e['shots'] = edl['shots'][a:b]
    for k in ('subtitles', 'vo', 'cards', 'typewriter'):
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
    shots = edl['shots']
    starts, t = [], 0
    for s in shots: starts.append(t); t += round((s['out'] - s['in']) * fps)
    total = t / fps; tl = [round(x / fps, 3) for x in starts]; ends = tl[1:] + [total]

    fp = visual_fingerprint(edl)
    if reuse:
        old = open(reuse + '.visual.sha').read().strip() if os.path.exists(reuse + '.visual.sha') else None
        if old != fp: sys.exit(f'--reuse-video: {reuse} was rendered from a different picture (fingerprint {old} != {fp}); render it fully')
    keep = os.environ.get('KEEP_TMP')
    with (tempfile.TemporaryDirectory() if not keep else _Keep(keep)) as tmp:
        from scipy.io import wavfile
        ovs = overlays_for(edl, tl, shots, fps, W, H, tmp)
        leaks = []
        for k, fx in enumerate(edl.get('fx', [])):
            if fx['type'] != 'leak': continue
            d = fx.get('dur', 0.9); a = tl[fx['cut']] - d / 2; p = f'{tmp}/leak{k}.mov'
            make_leak(p, d, fps, seed=k, warmth=fx.get('warmth', 1.0)); leaks.append((p, a, d))

        # one grade per clip, measured over every range of it the edit uses (two shots of one clip must match)
        clip_grade = {}
        for cid in sorted({s['clip'] for s in shots if not s.get('photo')}):
            uses = [s for s in shots if s['clip'] == cid]
            ts = [t for s in uses for t in np.linspace(s['in'] + 0.1, max(s['in'] + 0.1, s['out'] - 0.1), 3)]
            modes = {s.get('grade', 'auto') for s in uses}
            clip_grade[cid] = grade.clip_filter(clips[cid]['path'], ts, mode=modes.pop() if len(modes) == 1 else 'auto', look=edl.get('look', 'film'))
        music_on = [False] * len(shots)
        for m in (edl.get('music') or []):
            for k in range(m.get('from_shot', 0), m['to_shot'] if m.get('to_shot') is not None else len(shots)): music_on[k] = True

        def render_shot(i):
            s = shots[i]; n = round((s['out'] - s['in']) * fps); t0, t1 = tl[i], ends[i]
            dst = f'{tmp}/s{i:04d}.mp4'
            tail = ((f",fade=in:st=0:d={s['fade_from_black']}" if s.get('fade_from_black') else '')
                    + (',fade=in:st=0:d=0.3' if i == 0 else '') + (f',fade=out:st={max(0, n / fps - 0.8):.3f}:d=0.8' if i == len(shots) - 1 else ''))
            def source(sh, a0, dur):
                """(input args, filter chain) giving `dur` seconds of shot `sh` from source time a0, graded, at W x H."""
                if sh.get('photo'):
                    png = f"{tmp}/photo_plain_{os.path.basename(sh['photo'])}.jpg"
                    if not os.path.exists(png): compose_photo(sh['photo'], W, H, png, sh.get('caption'))
                    full = max(0.5, sh['out'] - sh['in']); k = round(full * fps)
                    z = f"zoompan=z='1+0.045*(on+{round(a0 * fps)})/{k}':d=1:s={W}x{H}:fps={fps}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    return ['-loop', '1', '-framerate', str(fps), '-t', f'{dur + 0.2:.3f}', '-i', png], f'{z},setsar=1'
                cc = clips[sh['clip']]
                gf = clip_grade[sh['clip']]
                return (['-ss', f'{a0:.3f}', '-t', f'{dur + 0.2:.3f}', '-i', cc['path']],
                        f'scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1' + (',' + gf if gf else ''))
            src, chain = source(s, s['in'], s['out'] - s['in'])
            flash = ',fade=in:st=0:d=0.18:color=white' if s.get('photo') else ''
            inputs, fc, last, idx = list(src), [f'[0:v]{chain},fps={fps},trim=end_frame={n},setpts=PTS-STARTPTS{flash}{tail}[b]'], 'b', 1
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
            for p, a, d in leaks:                              # leaks that touch this shot
                if a + d <= t0 or a >= t1: continue
                # leak already running at this shot's start: seek into it; starting later: shift its timestamps in the graph
                # (-itsoffset on the input was ignored here and the first half of every leak went missing)
                inputs += (['-ss', f'{t0 - a:.3f}'] if a < t0 else []) + ['-i', p]
                shift = f',setpts=PTS-STARTPTS+{a - t0:.3f}/TB' if a >= t0 else ',setpts=PTS-STARTPTS'
                fc.append(f'[{idx}:v]scale={W}:{H},format=rgba,gblur=sigma={round(28 * H / 1080)}{shift}[o{idx}]')
                fc.append(f'[{last}][o{idx}]overlay=0:0:eof_action=pass[v{idx}]'); last = f'v{idx}'; idx += 1
            enc = inputs + ['-filter_complex', ';'.join(fc), '-map', f'[{last}]', '-an',
                   '-frames:v', str(n), '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-maxrate', '16M', '-bufsize', '32M', '-bf', '0', '-g', str(fps),
                   '-pix_fmt', 'yuv420p', '-r', str(fps), '-video_track_timescale', str(fps * 1000), dst]
            if not reuse:
                run([FF, '-y', '-hwaccel', 'videotoolbox'] + enc)
                # under full parallel load the hardware decoder once dropped 19 frames from one shot and every later cut
                # slid out of sync with the sound - count every segment, retry in software, stop if still wrong
                if frame_count(dst) != n:
                    run([FF, '-y'] + enc)
                    if frame_count(dst) != n: raise RuntimeError(f'shot {i} {s["clip"]}: {frame_count(dst)} frames rendered, {n} planned')
            ns = round(n / fps * SR); g = AUDIO_GAIN[s.get('audio', 'ambient')]
            if s.get('audio', 'ambient') == 'ambient' and not music_on[i]: g = AMBIENT_NO_MUSIC
            seg = np.zeros((ns, 2), np.float32)
            c = clips.get(s['clip'], {'audio': False})
            if s.get('photo'):
                sfx = shutter(SR); seg[:len(sfx)] += sfx[:ns]
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
                fi = min(int((xd or 0.03) * SR), k // 2); fo = min(int(0.03 * SR), k // 2)   # fade in (longer under a dissolve), 30 ms out
                if fi: x[:fi] *= np.linspace(0, 1, fi)[:, None]
                if fo: x[k - fo:] *= np.linspace(1, 0, fo)[:, None]
                seg[:k] += x
            return dst, seg

        with ThreadPoolExecutor(workers) as ex:
            done = list(ex.map(render_shot, range(len(shots))))
        vsegs = [d for d, _ in done]; audio = [a for _, a in done]
        if reuse:      # picture unchanged (same fingerprint): take the video stream of the earlier render as it is
            run([FF, '-y', '-i', reuse, '-map', '0:v', '-c', 'copy', f'{tmp}/video.mp4'])
        else:
            with open(f'{tmp}/list.txt', 'w') as fh: fh.writelines(f"file '{p}'\n" for p in vsegs)
            run([FF, '-y', '-f', 'concat', '-safe', '0', '-i', f'{tmp}/list.txt', '-c', 'copy', f'{tmp}/video.mp4'])

        # audio: dialogue/ambience + songs, ducked under speech and narration, loudness-normalised on its own
        mix = np.concatenate(audio); N = len(mix); sr_t = N / total; t_ax = np.arange(N) / sr_t
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
        rms = lambda x: float(np.sqrt(np.mean(x ** 2)) + 1e-9)
        spans = []
        for i, s in enumerate(shots):          # live speech: music at least VOICE_OVER_MUSIC_DB under the measured voice
            if s.get('audio') != 'live': continue
            j = slice(int(tl[i] * sr_t), int(ends[i] * sr_t)); v, mu = rms(mix[j]), rms(music[j])
            spans.append((tl[i], ends[i], min(DUCK, 10 ** (-VOICE_OVER_MUSIC_DB / 20) * v / mu) if mu > 1e-6 else DUCK))
        spans += [(tl[v['shot']] + v.get('at', 0) - 0.25, tl[v['shot']] + v.get('at', 0) + v.get('dur', vo_duration(v['text'])) + 0.35, DUCK) for v in edl.get('vo', [])]
        for a, b, f in spans:
            g = np.clip(np.minimum((t_ax - (a - 0.3)) / 0.3, ((b + 0.3) - t_ax) / 0.3), 0, 1)   # 0.3 s ramps
            duck = np.minimum(duck, 1 - (1 - max(f, 0.02)) * g)
        voice_db = []
        for i, s in enumerate(shots):
            if s.get('audio') != 'live': continue
            j = slice(int(tl[i] * sr_t), int(ends[i] * sr_t)); mu = rms(music[j] * duck[j, None])
            if mu > 1e-5: voice_db.append((i, 20 * np.log10(rms(mix[j]) / mu)))
        json.dump(voice_db, open(out + '.voice.json', 'w'))
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
    sys.exit(0 if ok else 1)

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
    # d2. no dead air: no 1.5 s stretch quieter than -48 dBFS (except the first / last second)
    blk = int(0.5 * 16000); lv = [20 * np.log10(np.sqrt(np.mean(a[k:k + blk] ** 2)) / 32768 + 1e-9) for k in range(0, len(a) - blk, blk)]
    dead = [k * 0.5 for k in range(2, len(lv) - 4) if max(lv[k:k + 3]) < -48]
    ok &= not dead
    print(f"  {'✓' if not dead else '✗'} no silent stretches" + (f': silent around {", ".join(f"{int(t // 60)}:{t % 60:04.1f}" for t in dead[:10])}' if dead else ''))
    # e. loudness and true peak of the delivered file (AAC overshoots; a peak above 0 dBFS clips on playback)
    r = subprocess.run([FF, '-hide_banner', '-i', out, '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    I = float(re.findall(r'I:\s+(-?[0-9.]+) LUFS', r)[-1]); pk = float(re.findall(r'Peak:\s+(-?[0-9.]+) dBFS', r)[-1])
    good = pk <= -0.5 and -16.5 <= I <= -12.5; ok &= good
    print(f"  {'✓' if good else '✗'} loudness {I:.1f} LUFS, true peak {pk:+.1f} dBFS")
    print('  verify:', 'PASS' if ok else 'FAIL')
    return ok

if __name__ == '__main__':
    main()
