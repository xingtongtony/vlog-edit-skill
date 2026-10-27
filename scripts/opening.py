"""Designed vlog openings (≤ 15 s): graphic design - one accent colour, strict layout, few sizes - brought to life on
the music's beats. Each type is a recipe; frames are composed in PIL over graded footage and encoded with the song.

Types (learnt from four openings the user sent, projects/_references/fancy-openings/README.md):
  behind     字长在景后面: a fixed letterbox (no slit opening or closing); three small labels across the top; big condensed place
             names grow letter by letter *behind* something in the shot (a passing truck, the user) - Vision cut-outs
             per frame; on the drop, cuts on every beat with the place name in the bottom bar; an end card
  flash      快闪 + 换字体: a burst of shots at a quarter beat each; hold on one calm shot, a tiny "Welcome to", the
             place name under it changing typeface on every beat at a fixed width; then the shots flash *behind* the
             fixed word; back to the calm shot on the last beat
  editorial  杂志排版 + 取色: small / big / small centred stack whose big word takes its colour from each shot; a thin
             big "Vlog(:" with labels overlapping it; a four-corner frame of labels over beat cuts; a left-aligned
             "Loading" block on a black-and-white shot
usage: python3 opening.py <project> behind|flash|editorial <out.mp4>
The shots, words and songs of each type come from <project>/openings.json (see projects/bali-2026/openings.json).
Needs the Vision tool compiled once: swiftc -O scripts/subject_mask.swift -o ~/.cache/vlog-tools/subject_mask
"""
import colorsys, glob, json, os, subprocess, sys, tempfile, unicodedata
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade

FF = '/opt/homebrew/bin/ffmpeg'; VENV = os.path.expanduser('~/.venvs/tts/bin/python')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); FD = os.path.join(ROOT, 'fonts')
VISION = os.path.expanduser('~/.cache/vlog-tools/subject_mask')        # swiftc -O scripts/subject_mask.swift -o this
W, H, FPS = 1920, 1080, 30

def ease(u): u = min(1.0, max(0.0, u)); return 1 - (1 - u) ** 3

def font(name, px, **axes):
    f = ImageFont.truetype(os.path.join(FD, name), max(1, round(px)))
    if axes:
        key = {'Weight': 'wght', 'Width': 'wdth', 'Optical size': 'opsz'}; ax = f.get_variation_axes()
        f.set_variation_by_axes([axes.get(key[a['name'].decode()], a['default']) for a in ax])
    return f

def track_path(part):
    lib = os.path.join(ROOT, 'music-library', 'tracks')
    hits = [p for p in glob.glob(os.path.join(lib, '*')) if part in unicodedata.normalize('NFC', os.path.basename(p))]
    assert len(hits) == 1, (part, hits); return hits[0]

def beats(path, a, b):
    """Beat times of the song between a and b (song seconds)."""
    code = ('import sys, json, librosa; y, sr = librosa.load(sys.argv[1], sr=22050, mono=True, offset=float(sys.argv[2]), '
            'duration=float(sys.argv[3]) - float(sys.argv[2])); _, b = librosa.beat.beat_track(y=y, sr=sr, units="time"); '
            'print(json.dumps([round(float(x) + float(sys.argv[2]), 3) for x in b]))')
    return json.loads(subprocess.run([VENV, '-c', code, path, str(a), str(b)], check=True, capture_output=True, text=True).stdout)

# ---------------- type ----------------
class Word:
    """A line of type as one RGBA tile per glyph (positions relative to the ink's top-left), measured by its ink."""
    def __init__(self, text, f, color, track=0.0):
        d = ImageDraw.Draw(Image.new('L', (1, 1))); pad = round(f.size * 0.3); x = 0.0; g = []
        for ch in text:
            if ch.strip():
                l, t, r, b = f.getbbox(ch); tile = Image.new('RGBA', (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((pad - l, pad - t), ch, font=f, fill=tuple(color) + (255,)); g.append([tile, x + l - pad, t - pad])
            x += d.textlength(ch, font=f) + track
        x0 = min(p[1] + pad for p in g); y0 = min(p[2] + pad for p in g)
        self.w = max(p[1] + p[0].width - pad for p in g) - x0; self.h = max(p[2] + p[0].height - pad for p in g) - y0
        self.glyphs = [(t, gx - x0, gy - y0) for t, gx, gy in g]

    def draw(self, fr, x, y, alpha=1.0, upto=None, blur=0.0):
        """Paste onto RGBA `fr` with the ink's top-left at (x, y); `upto` glyphs only (letter by letter)."""
        for k, (t, gx, gy) in enumerate(self.glyphs):
            if upto is not None and k >= upto: break
            a = alpha if not isinstance(alpha, (list, tuple)) else alpha[k]
            if a <= 0.004: continue
            tt = t.filter(ImageFilter.GaussianBlur(blur)) if blur > 0.3 else t
            if a < 1: tt = tt.copy(); tt.putalpha(tt.getchannel('A').point(lambda v: int(v * a)))
            fr.alpha_composite(tt, (int(round(x + gx)), int(round(y + gy))))

def fit(text, name, width=None, height=None, color=(255, 255, 255), track=0.0, **axes):
    """A Word whose ink is `width` px wide or `height` px tall."""
    size = 200.0
    for _ in range(3):
        w = Word(text, font(name, size, **axes), color, track * size)
        size *= (width / w.w) if width else (height / w.h)
    return Word(text, font(name, size, **axes), color, track * size)

def shadow(layer, r=6, a=0.35):
    """A soft shadow under an RGBA layer, so light type reads on bright footage."""
    sh = Image.new('RGBA', layer.size, (0, 0, 0, 0)); sh.putalpha(layer.getchannel('A').filter(ImageFilter.GaussianBlur(r)).point(lambda v: int(v * a)))
    return Image.alpha_composite(sh, layer)

def accent_from(img, light=0.80, sat=0.62):
    """The shot's own colour as a light accent: the most common saturated hue among its bright pixels."""
    a = np.asarray(img.convert('RGB').resize((96, 54)), np.float32) / 255
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in a.reshape(-1, 3)])
    keep = (hsv[:, 1] > 0.22) & (hsv[:, 2] > 0.35)
    hues = hsv[keep, 0] if keep.sum() > 40 else hsv[:, 0]
    hist, edges = np.histogram(hues, bins=18, range=(0, 1)); h = (edges[hist.argmax()] + edges[hist.argmax() + 1]) / 2
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, light, sat))

# ---------------- footage ----------------
class Footage:
    def __init__(self, project):
        self.project = project; self.clips = {c['id']: c for c in json.load(open(os.path.join(project, 'ingest', 'clips.json')))}
        self.tmp = tempfile.mkdtemp(prefix='opening_')

    def path(self, clip):
        p = self.clips[clip]['path']; q = os.path.join(self.project, 'sources', os.path.basename(p))
        return q if os.path.exists(q) else p

    def frames(self, clip, t0, n, bw=False):
        """n graded frames of a clip from t0, filling the frame (centre crop)."""
        src = self.path(clip); kind = self.clips[clip].get('kind')
        t0 = max(0.0, min(t0, self.clips[clip]['duration'] - n / FPS - 0.1))     # stay inside the clip
        g = grade.clip_filter(src, [t0 + 0.1, t0 + n / FPS / 2, t0 + n / FPS - 0.1], mode='none' if kind == 'drone' else 'auto', look='film')
        vf = f'scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p' + (',' + g if g else '') + (',hue=s=0' if bw else '')
        raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t0:.3f}', '-i', src, '-vf', vf, '-frames:v', str(n), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                             capture_output=True, check=True).stdout
        fr = [Image.frombytes('RGB', (W, H), raw[i * W * H * 3:(i + 1) * W * H * 3]) for i in range(len(raw) // (W * H * 3))]
        while len(fr) < n: fr.append(fr[-1])
        return fr

    def masks(self, frames, mode='subject', roi=None, largest=False):
        """Vision cut-out of every frame (white = the subject), steady enough to put type behind.
        Vision decides afresh on every frame what the subject is: with a stranger's shoulder at the frame edge it
        jumped between that shoulder and the truck passing the MIXUE corner, missed the truck on every other frame and
        the letters flickered (Bali opening, 2026-10-27). So: only the region around the type is shown to Vision
        (`roi`, fractions of the frame), `largest` keeps the biggest blob (the user, not the tourists behind), and a
        3-frame temporal median removes one-frame drop-outs and one-frame blobs."""
        x0, y0, x1, y1 = [round(v * s) for v, s in zip(roi or (0, 0, 1, 1), (W, H, W, H))]
        raw = []
        for im in frames:
            a, b = f'{self.tmp}/m_in.png', f'{self.tmp}/m_out.png'
            im.crop((x0, y0, x1, y1)).save(a)
            r = subprocess.run([VISION, a, b, mode], capture_output=True)
            m = np.zeros((H, W), np.uint8)
            if r.returncode == 0: m[y0:y1, x0:x1] = np.asarray(Image.open(b).convert('L'))
            if largest and m.any():
                from scipy import ndimage
                lab, n = ndimage.label(m > 128)
                if n > 1: m = np.where(lab == 1 + int(np.argmax(ndimage.sum(np.ones_like(lab), lab, range(1, n + 1)))), m, 0).astype(np.uint8)
            raw.append(m)
        st = np.stack(raw); med = st.copy()
        med[1:-1] = np.median(np.stack([st[:-2], st[1:-1], st[2:]]), axis=0).astype(np.uint8)
        return [Image.fromarray(m).filter(ImageFilter.GaussianBlur(1.2)) for m in med]

class Writer:
    def __init__(self, out):
        self.video = out + '.video.mp4'; self.out = out; self.n = 0
        self.p = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                                   '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', self.video], stdin=subprocess.PIPE)
    def put(self, im): self.p.stdin.write(im.convert('RGB').tobytes()); self.n += 1
    def finish(self, song, start):
        self.p.stdin.close(); self.p.wait(); d = self.n / FPS
        subprocess.run([FF, '-v', 'error', '-y', '-i', self.video, '-ss', f'{start:.3f}', '-t', f'{d:.3f}', '-i', song, '-filter_complex',
                        f'[1:a]afade=t=out:st={max(0, d - 0.8):.3f}:d=0.8,loudnorm=I=-15:TP=-1.5[a]', '-map', '0:v', '-map', '[a]',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k', '-shortest', self.out], check=True)
        os.remove(self.video)

def frames_between(a, b): return max(1, round(b * FPS) - round(a * FPS))

def letterbox(im, open_h):
    """Black bars leaving a picture of height open_h in the middle."""
    if open_h >= H: return im
    out = Image.new('RGB', (W, H)); y = round((H - open_h) / 2)
    if open_h > 0: out.paste(im.crop((0, y, W, y + round(open_h))), (0, y))
    return out

def behind(frame, layer, mask):
    """Type layer laid *behind* the masked subject: the subject stays on top."""
    a = np.asarray(layer.getchannel('A'), np.float32) * (1 - np.asarray(mask, np.float32) / 255)
    lay = layer.copy(); lay.putalpha(Image.fromarray(a.astype(np.uint8)))
    out = frame.convert('RGBA'); out.alpha_composite(lay); return out

# ---------------- recipes ----------------
CASE = {'title': str.title, 'upper': str.upper, 'lower': str.lower}
# the place name in a new face every 3 frames (flash) - seven faces on the beat read as a slideshow, not a flash (user, Bali v1)
FLASH_FACES = [('InstrumentSerif-Italic.ttf', 'title', {}), ('Anton-Regular.ttf', 'upper', {}), ('ZCOOLKuaiLe-Regular.ttf', 'cn', {}),
               ('Pacifico-Regular.ttf', 'title', {}), ('BigShoulders[opsz,wght].ttf', 'upper', {'wght': 100, 'opsz': 72}), ('SmileySans-Oblique.ttf', 'cn', {}),
               ('LilitaOne-Regular.ttf', 'upper', {}), ('Outfit[wght].ttf', 'lower', {'wght': 100}), ('PermanentMarker-Regular.ttf', 'upper', {}),
               ('InstrumentSerif-Regular.ttf', 'upper', {}), ('LXGWMarkerGothic-Regular.ttf', 'cn', {}), ('Fredoka[wdth,wght].ttf', 'title', {'wght': 600}),
               ('BebasNeue-Regular.ttf', 'upper', {}), ('Caveat[wght].ttf', 'title', {'wght': 700}), ('SpaceGrotesk[wght].ttf', 'upper', {'wght': 700}),
               ('ZCOOLQingKeHuangYou-Regular.ttf', 'cn', {}), ('Outfit[wght].ttf', 'upper', {'wght': 900}), ('InstrumentSans[wdth,wght].ttf', 'upper', {'wght': 700, 'wdth': 75}),
               ('Yozai-Medium.ttf', 'cn', {}), ('BigShoulders[opsz,wght].ttf', 'upper', {'wght': 900, 'opsz': 72})]
FLASH_FINAL = ('BricolageGrotesque[opsz,wdth,wght].ttf', 'upper', {'wght': 800, 'wdth': 75, 'opsz': 96})

def opening_behind(F, S, out):
    """字长在景后面: a song with a quiet intro and a clear drop (S['cuts']: the slit opens until cut 0, reveal 1 until cut 1,
    reveal 2 until cut 2 = the drop, one shot per beat until cut 3, the end card for S['end']['dur'])."""
    song = track_path(S['song']); bt = beats(song, S.get('song_start', 0), S.get('song_start', 0) + 25); bt = [b - S.get('song_start', 0) for b in bt]
    AMBER = tuple(S['accent']); BAR = (H - W / 2.35) / 2; PIC = H - 2 * BAR
    cut = lambda t: min(bt, key=lambda b: abs(b - t))
    t1, t2, t3, tend = (cut(t) for t in S['cuts'])
    mcuts = [b for b in bt if t3 - 0.01 <= b < tend - 0.05][:len(S['montage'])] + [tend]
    tfin = tend + S['end'].get('dur', 2.0)
    L, C, R = S['labels']
    lab = {'l': fit(L, 'BigShoulders[opsz,wght].ttf', height=BAR * 0.34, color=AMBER, track=0.04, wght=600, opsz=72),
           'c': fit(C, 'BigShoulders[opsz,wght].ttf', height=BAR * 0.34, color=AMBER, track=0.04, wght=600, opsz=72),
           'r': fit(R, 'InstrumentSerif-Italic.ttf', height=BAR * 0.34, color=AMBER)}
    def header(fr, a):
        y = BAR + 26
        lab['l'].draw(fr, 64, y, a); lab['c'].draw(fr, (W - lab['c'].w) / 2, y, a); lab['r'].draw(fr, W - 64 - lab['r'].w, y, a)
    def caption(fr, text, a=1.0):        # the place in the bottom bar, like the lyric line of the reference
        w = fit(text, 'InstrumentSerif-Italic.ttf', height=BAR * 0.26, color=(245, 240, 230))
        w.draw(fr, (W - w.w) / 2, H - BAR + (BAR - w.h) / 2, a)
    wr = Writer(out)
    # 1 · the wide shot in the letterbox, the labels come in; no slit opening (the user: 开屏、闭屏的过场有点割裂)
    n = frames_between(0, t1)
    for k, im in enumerate(F.frames(*S['open'], n)):
        t = k / FPS; fr = letterbox(im, PIC).convert('RGBA'); header(fr, ease((t - 0.4) / 0.5)); wr.put(fr)
    # 2, 3 · big condensed words grow behind something in the shot: typed in fast (first), one letter per half beat (second)
    for i, (rv, a, b) in enumerate(((S['reveal'][0], t1, t2), (S['reveal'][1], t2, t3))):
        n = frames_between(a, b); fs = F.frames(rv['clip'], rv['at'], n)
        ms = F.masks(fs, rv.get('mode', 'subject'), roi=rv.get('roi'), largest=rv.get('largest', False))
        word = fit(rv['word'], 'BigShoulders[opsz,wght].ttf', height=PIC * rv.get('h', 0.30), color=AMBER, track=0.04, wght=800, opsz=72)
        half = sorted([x - a for x in bt if a <= x < b] + [x - a + (bt[1] - bt[0]) / 2 for x in bt if a <= x < b])
        x = rv['cx'] * W - word.w / 2 if 'cx' in rv else rv.get('x', 0.0625) * W
        for k, (im, m) in enumerate(zip(fs, ms)):
            t = k / FPS; lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            upto = min(len(word.glyphs), 1 + int(t / 0.09)) if i == 0 else sum(1 for h in half if h <= t + 0.02)
            word.draw(lay, x, BAR + PIC * rv.get('y', 0.46), upto=upto)
            fr = behind(letterbox(im, PIC), shadow(lay, 8, 0.25), m); header(fr, 1); caption(fr, rv['place']); wr.put(fr)
    # 4 · the drop: one shot per beat, the place in the bottom bar
    for (clip, at, place), a, b in zip(S['montage'], mcuts, mcuts[1:]):
        for im in F.frames(clip, at, frames_between(a, b)):
            fr = letterbox(im, PIC).convert('RGBA'); header(fr, 1); caption(fr, place); wr.put(fr)
    # 5 · the end card: the name letter by letter, a small line under it; it holds and the next shot cuts in (no closing bars)
    E = S['end']; n = frames_between(tend, tfin)
    word = fit(E['word'], 'BigShoulders[opsz,wght].ttf', height=PIC * 0.22, color=AMBER, track=0.10, wght=800, opsz=72)
    sub = fit(E['sub'], 'BigShoulders[opsz,wght].ttf', width=word.w, color=(250, 246, 238), track=0.10, wght=500, opsz=72)
    for k, im in enumerate(F.frames(E['clip'], E['at'], n)):
        t = k / FPS
        fr = im.convert('RGBA'); lay = Image.new('RGBA', (W, H), (0, 0, 0, 0)); x = (W - word.w) / 2; y = BAR + PIC * E.get('y', 0.40)
        word.draw(lay, x, y, upto=1 + int(t / 0.11)); sub.draw(lay, x, y + word.h + 26, ease((t - 0.55) / 0.4))
        fr.alpha_composite(shadow(lay, 8, 0.3)); fr = letterbox(fr.convert('RGB'), PIC).convert('RGBA')
        header(fr, 1); caption(fr, E['caption']); wr.put(fr)
    wr.finish(song, S.get('song_start', 0))

def opening_flash(F, S, out):
    """快闪 + 换字体: a song with the band in from the first beat. Burst (quarter beats) until beat 3, hold until beat 11
    (the name flashes typefaces from beat 6), shots flash behind the fixed name until beat 14, back to the calm shot for the
    last beat. No squeeze to a slit, no black tail (the user: 开屏、闭屏的过场有点割裂)."""
    song = track_path(S['song']); s0 = S.get('song_start', 0); bt = beats(song, s0, s0 + 17)
    start = bt[0]; bt = [b - start for b in bt]; q = (bt[1] - bt[0]) / 4      # frame 0 is the first beat, never black
    YELLOW = tuple(S['accent'])
    t0 = bt[0]; t_hold = bt[3]; t_word = bt[6]; t_fix = bt[11]; t_sq = bt[14]; t_end = bt[15]
    wr = Writer(out)
    edges = [t0 + q * k for k in range(len(S['burst']))] + [t_hold]
    for (clip, at), a, b in zip(S['burst'], edges, edges[1:]):
        for im in F.frames(clip, at, frames_between(a, b)): wr.put(im)
    welcome = fit(S.get('welcome', 'Welcome to'), 'InstrumentSerif-Regular.ttf', height=H * 0.034, color=(250, 248, 242))
    name = lambda case: S['name_cn'] if case == 'cn' else CASE[case](S['name'])
    WW = W * 0.25
    settle = fit(name(FLASH_FINAL[1]), FLASH_FINAL[0], width=WW, color=YELLOW, **FLASH_FINAL[2])
    words = [fit(name(c), f, width=WW, color=YELLOW, **ax) for f, c, ax in FLASH_FACES]
    words = [w if w.h <= settle.h * 1.15 else fit(name(c), f, height=settle.h * 1.15, color=YELLOW, **ax)   # tall thin faces must not reach "Welcome to"
             for w, (f, c, ax) in zip(words, FLASH_FACES)]
    swaps = [t_word + 3 * k / FPS for k in range(int((t_fix - t_word) * FPS / 3))]
    def title(fr, t, alpha=1.0):
        a = ease((t - (t_hold + 0.35)) / 0.5) * alpha
        if a <= 0: return
        lay = Image.new('RGBA', (W, H), (0, 0, 0, 0)); wcx = W * S.get('x', 0.5); base = H * S.get('y', 0.53)   # on calm water, off white sand
        welcome.draw(lay, wcx - welcome.w / 2, base - welcome.h - H * 0.022, a)
        if t >= t_word:
            k = sum(1 for s in swaps if s <= t + 0.005) - 1
            w = settle if t >= t_fix - 0.005 else words[k % len(words)]
            w.draw(lay, wcx - w.w / 2, base + settle.h / 2 - w.h / 2, alpha)        # every face centred on the same line
        fr.alpha_composite(shadow(lay, 7, 0.35))
    for k, im in enumerate(F.frames(*S['hold'], frames_between(t_hold, t_fix))):
        t = t_hold + k / FPS; fr = im.convert('RGBA'); title(fr, t); wr.put(fr)
    edges = [t_fix + q * k for k in range(len(S['behind'])) if t_fix + q * k < t_sq] + [t_sq]
    for (clip, at), a, b in zip(S['behind'], edges, edges[1:]):
        for im in F.frames(clip, at, frames_between(a, b)):
            fr = im.convert('RGBA'); title(fr, b); wr.put(fr)
    for im in F.frames(*S.get('last', S['hold']), frames_between(t_sq, t_end)):
        fr = im.convert('RGBA'); title(fr, t_end); wr.put(fr)
    wr.finish(song, s0 + start)

def opening_editorial(F, S, out):
    """杂志排版 + 取色: a cut on every ~0.65 s beat from S['song_start'] (the song's drums in). Four shots of the centred stack,
    four of the thin "Vlog(:", up to eight of the four-corner frame, then the "Loading" block in black and white."""
    song = track_path(S['song']); start = S.get('song_start', 0); bt = [b - start for b in beats(song, start, start + 20)]
    step = max(1, round(0.65 / float(np.median(np.diff(bt))))); cuts = bt[::step]       # one cut per ~0.65 s beat
    s1, s2, s3 = S['stack'], S['vlog'], S['corners']['shots']     # stack: shots darker than a white wall (the type is light)
    wr = Writer(out); WHITE = (250, 248, 242)
    LAV = None
    small = lambda s, c=WHITE, h=H * 0.027: fit(s, 'ResourceHanRoundedCN-Light.ttf' if any(ord(ch) > 0x2e80 for ch in s) else 'Outfit[wght].ttf',
                                                height=h, color=c, **({} if any(ord(ch) > 0x2e80 for ch in s) else {'wght': 450}))
    top, quote = small(S['top']), fit(S['quote'], 'ResourceHanRoundedCN-Light.ttf', height=H * 0.030, color=WHITE)
    def put(fr, *items):
        lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        for w, x, y, a in items: w.draw(lay, x, y, a)
        box = lay.getchannel('A').getbbox() or (0, 0, W, H)
        lum = float(np.asarray(fr.convert('L').crop(box), np.float32).mean()) / 255     # brighter behind the type -> a deeper scrim
        k = 0.30 + 0.35 * max(0.0, lum - 0.45) / 0.55
        sc = lay.getchannel('A').filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(60)).point(lambda v: int(min(255, v * 2) * k))
        dark = Image.new('RGBA', (W, H), (0, 0, 0, 0)); dark.putalpha(sc)        # a soft scrim: light type reads on bright footage
        fr.alpha_composite(dark); fr.alpha_composite(shadow(lay, 6, 0.35)); wr.put(fr)
    # 1 · the centred stack; the big word wears each shot's own colour
    for k, (clip, at) in enumerate(s1):
        fs = F.frames(clip, at, frames_between(cuts[k], cuts[k + 1])); col = accent_from(fs[len(fs) // 2]); LAV = LAV or col
        big = fit(S['name'], 'Outfit[wght].ttf', width=W * 0.20, color=col, wght=500)
        y0 = H / 2 - (top.h + 40 + big.h + 44 + quote.h) / 2
        for j, im in enumerate(fs):
            a = ease((cuts[k] + j / FPS) / 0.4)
            put(im.convert('RGBA'), (top, (W - top.w) / 2, y0, a), (big, (W - big.w) / 2, y0 + top.h + 40, a),
                (quote, (W - quote.w) / 2, y0 + top.h + 40 + big.h + 44, a))
    # 2 · a thin big "Vlog(:" with two labels laid over its edges
    k0 = len(s1); vlog = fit('Vlog(:', 'Outfit[wght].ttf', width=W * 0.40, color=LAV, wght=200)
    l1, l2 = small(S['vlog_labels'][0], LAV), small(S['vlog_labels'][1], LAV)
    vx, vy = (W - vlog.w) / 2, (H - vlog.h) / 2
    for k, (clip, at) in enumerate(s2, k0):
        for j, im in enumerate(F.frames(clip, at, frames_between(cuts[k], cuts[k + 1]))):
            t = cuts[k] + j / FPS - cuts[k0]
            put(im.convert('RGBA'), (vlog, vx, vy, ease(t / 0.5)), (l1, vx + vlog.w * 0.42, vy + vlog.h * 0.12, ease((t - 0.3) / 0.4)),
                (l2, vx + vlog.w * 0.04, vy + vlog.h * 0.80, ease((t - 0.5) / 0.4)))
    # 3 · the four-corner frame of labels (real facts only: month, year, place, the account)
    k0 = len(s1) + len(s2); Cn = S['corners']
    M = 72; title = fit(S['name'], 'Outfit[wght].ttf', height=H * 0.06, color=LAV, wght=500)
    bl = small(Cn['left'], LAV, H * 0.028); bc = [small(x, LAV, H * 0.028) for x in Cn['centre']]; br = [small(x, LAV, H * 0.028) for x in Cn['right']]
    lh = H * 0.028 + 14
    for k, (clip, at) in enumerate(s3, k0):
        if k + 1 >= len(cuts): break
        for j, im in enumerate(F.frames(clip, at, frames_between(cuts[k], cuts[k + 1]))):
            t = cuts[k] + j / FPS - cuts[k0]; a = ease(t / 0.4); yb = H - M - 2 * lh
            put(im.convert('RGBA'), (title, (W - title.w) / 2, M, a), (bl, M, yb + lh, a), (bc[0], (W - bc[0].w) / 2, yb, a),
                (bc[1], (W - bc[1].w) / 2, yb + lh, a), (br[0], W - M - br[0].w, yb, a), (br[1], W - M - br[1].w, yb + lh, a))
    # 4 · "Loading", left-aligned, on one shot in black and white
    Ld = S['loading']; n = round(Ld.get('dur', 2.8) * FPS); rows = Ld['rows']
    f4 = font('Outfit[wght].ttf', fit(rows[0], 'Outfit[wght].ttf', height=H * 0.042, wght=500).h / 0.72, wght=500)   # one size for every row
    big = [Word(r, f4, WHITE) for r in rows]; dots = [Word(d, f4, WHITE) for d in ('Loading.', 'Loading..', 'Loading...')]
    x = W * 0.60; y = H * 0.30; step = H * 0.075
    for j, im in enumerate(F.frames(Ld['clip'], Ld['at'], n, bw=True)):
        t = j / FPS; items = []
        for r, w in enumerate(big):
            a = ease((t - r * 0.12) / 0.35)
            if rows[r] == 'Loading': w = dots[int(t / 0.33) % 3]
            items.append((w, x, y + r * step + (big[0].h - w.h) / 2 * (rows[r] == '–'), a))
        put(im.convert('RGBA'), *items)
    wr.finish(song, start)

if __name__ == '__main__':
    project, kind, out = sys.argv[1:4]
    F = Footage(os.path.abspath(project)); spec = json.load(open(os.path.join(project, 'openings.json')))
    {'behind': opening_behind, 'flash': opening_flash, 'editorial': opening_editorial}[kind](F, spec[kind], out)
    print(out)
