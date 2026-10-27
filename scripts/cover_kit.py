"""Xiaohongshu cover kit: frames graded like the cut, Apple Vision cut-outs, crops, young type, three proven layouts.

A trip's covers are a few calls in projects/<trip>/publish/make_covers.py (Bohol's is the worked example):

    from cover_kit import Kit, full_bleed_hero, triptych_hero, triptych_signature, YELLOW, SUN, NAVY
    k = Kit(project_dir)
    triptych_hero(k, 'cover_B.jpg', [('c114', 28.0, .5, .5), ('c154', 122.0, .46, .42), ('c143', 9.5, .5, .5)],
                  'BOHOL', YELLOW, tag='TONY VLOG', series='搓泥菲律宾环游记')

Design rules (docs/publishing.md, learned from covers the user rated 高级 and from Anthropic's canvas-design skill):
one hero word, everything else tiny; the subject in front of the hero word; few words, strict margins, one accent colour
taken from the picture, no drop shadows; young type only (fonts/README.md). Covers are 3:4, 1080 x 1440.
Frames come from the original footage (no burned subtitles, full resolution) at moments the final cut still uses, graded
with the cut's per-clip filter; the cut-out comes from scripts/subject_mask.swift (built into macOS, no model download).
"""
import json, os, subprocess
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import grade

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); FONTS = os.path.join(ROOT, 'fonts')
FF = '/opt/homebrew/bin/ffmpeg'; W, H = 1080, 1440; SH = H // 3; M = 64            # canvas, strip height, margin
WHITE, YELLOW, SUN = (255, 255, 255), (255, 210, 63), (255, 226, 140)
NAVY, CORAL = (18, 53, 84), (226, 85, 58)

class Kit:
    """Frames and cut-outs of one trip's footage, cached in <project>/publish/frames/."""
    def __init__(self, project):
        self.project = project; self.out = os.path.join(project, 'publish')
        self.clips = {c['id']: c for c in json.load(open(os.path.join(project, 'ingest', 'clips.json')))}
        os.makedirs(os.path.join(self.out, 'frames'), exist_ok=True)

    def path(self, name): return os.path.join(self.out, name)

    def frame(self, clip, t):
        """Full-resolution frame at t, graded like the cut."""
        dst = os.path.join(self.out, 'frames', f'{clip}_{t}.png')
        if not os.path.exists(dst):
            src = self.clips[clip]['path']
            if not os.path.exists(src):   # the external drive is out: the cut's clips are cached by cache_sources.py
                src = os.path.join(self.project, 'sources', os.path.basename(src))
            g = grade.clip_filter(src, [t - 1, t, t + 1], look='film')
            # format=yuv420p: graded in 10 bit, DJI drone HEVC comes out black (same fix as render_preview.py)
            subprocess.run([FF, '-nostdin', '-loglevel', 'error', '-y', '-ss', str(t), '-i', src, '-frames:v', '1']
                           + (['-vf', 'format=yuv420p,' + g] if g else []) + [dst], check=True)
        return Image.open(dst).convert('RGB')

    def subject(self, clip, t, mode='subject'):
        """Vision cut-out of the frame (white = subject). 'subject' takes every salient instance (a turtle, a whole
        diver); 'person' only people - underwater it finds little more than the head."""
        src = os.path.join(self.out, 'frames', f'{clip}_{t}.png'); fr = self.frame(clip, t)
        dst = src[:-4] + f'_mask_{mode}.png'
        if not os.path.exists(dst):
            # Vision works on a small copy of the frame: on a 4K frame it took the sand behind the user's hair along
            # as hair (Bali, c307). Find the subject on the whole frame, then cut it out again from a crop around it.
            vision = lambda a, b: subprocess.run(['swift', os.path.join(ROOT, 'scripts', 'subject_mask.swift'), a, b, mode],
                                                 check=True, capture_output=True)
            whole, part, part_mask = (src[:-4] + f'_{n}.png' for n in (f'mask_{mode}_whole', 'part', f'part_mask_{mode}'))
            vision(src, whole)
            x0, y0, x1, y1 = Image.open(whole).convert('L').point(lambda v: 255 if v > 128 else 0).getbbox()
            mx, my = (x1 - x0) * 0.15, (y1 - y0) * 0.15
            box = (int(max(0, x0 - mx)), int(max(0, y0 - my)), int(min(fr.width, x1 + mx)), int(min(fr.height, y1 + my)))
            fr.crop(box).save(part); vision(part, part_mask)
            m = Image.new('L', fr.size, 0); m.paste(Image.open(part_mask).convert('L'), box[:2]); m.save(dst)
            os.remove(part); os.remove(part_mask)
        m = Image.open(dst).convert('L').point(lambda v: 255 if v > 128 else 0)  # hard edge, then a hair of feather
        return m.filter(ImageFilter.GaussianBlur(1.2))

def window(im, size, cx=0.5, cy=0.5, zoom=1.0):
    """Crop box of the target aspect centred on (cx, cy) (fractions of the frame); zoom > 1 is tighter."""
    ar = size[0] / size[1]; w = im.width / zoom; h = w / ar
    if h > im.height / zoom: h = im.height / zoom; w = h * ar
    x = min(max(cx * im.width - w / 2, 0), im.width - w); y = min(max(cy * im.height - h / 2, 0), im.height - h)
    return (round(x), round(y), round(x + w), round(y + h))

def crop(im, size, cx=0.5, cy=0.5, zoom=1.0):
    return im.crop(window(im, size, cx, cy, zoom)).resize(size, Image.LANCZOS)

def font(name, size, **axes):
    """Font with variable axes by tag, e.g. font('Fredoka[wdth,wght].ttf', 150, wght=600)."""
    f = ImageFont.truetype(os.path.join(FONTS, name), size)
    if axes:
        tags = {'Weight': 'wght', 'Width': 'wdth', 'Optical size': 'opsz'}; vals = []
        for a in f.get_variation_axes():
            n = a['name'].decode() if isinstance(a['name'], bytes) else a['name']
            vals.append(axes.get(tags.get(n, n), a['default']))
        f.set_variation_by_axes(vals)
    return f

def _render(f, c):
    im = Image.new('L', (int(f.size * 2.5),) * 2); ImageDraw.Draw(im).text((f.size // 4, 0), c, font=f, fill=255); return im.tobytes()

def check_glyphs(f, s):
    """Many handwritten Chinese fonts are simplified-only: 島 / 賓 come out as an empty box. Fail instead."""
    tofu = _render(f, '\U0010FFFD')
    miss = sorted({c for c in s if not c.isspace() and _render(f, c) == tofu})
    if miss: raise SystemExit(f"{os.path.basename(f.path)} has no glyph for {''.join(miss)}")

def width(s, f, track=0):
    return sum(f.getlength(c) for c in s) + track * (len(s) - 1)

def text(img, xy, s, f, fill=WHITE, track=0, anchor='l', alpha=255):
    """Tracked text, top-left (anchor 'l'), centred ('m') or right-aligned ('r') at xy; no shadow."""
    check_glyphs(f, s); x, y = xy; tw = width(s, f, track)
    if anchor == 'm': x -= tw / 2
    elif anchor == 'r': x -= tw
    lay = Image.new('L', img.size, 0); d = ImageDraw.Draw(lay)
    for c in s: d.text((x, y), c, font=f, fill=alpha); x += f.getlength(c) + track
    img.paste(fill, (0, 0), lay)

def hero(img, s, name, box, fill, track=0, max_h=None, **axes):
    """The one big word: sized so its ink fills the box width (x0, y0, x1, _), top edge of the ink at y0.
    A short word (BALI) at full width is taller than a strip: with max_h it keeps that height and the letters
    spread out to the width instead."""
    x0, y0, x1, _ = box; size = 400
    for _ in range(3):
        f = font(name, size, **axes); l, t, r, b = f.getbbox(s); ink = r - l + track * (len(s) - 1)
        size = int(size * (x1 - x0) / ink)
    f = font(name, size, **axes); l, t, r, b = f.getbbox(s)
    if max_h and b - t > max_h:
        f = font(name, int(size * max_h / (b - t)), **axes); l, t, r, b = f.getbbox(s)
        ink = width(s[:-1], f) + f.getbbox(s[-1])[2] - l; track = ((x1 - x0) - ink) / max(len(s) - 1, 1)
    text(img, (x0 - l, y0 - t), s, f, fill=fill, track=track)
    return y0 + (b - t)

def series_label(img, s):
    """The account's series name, small and centred at the bottom (搓泥…环游记)."""
    text(img, (W / 2, H - 76), s, font('SmileySans-Oblique.ttf', 40), track=4, anchor='m')

def full_bleed_hero(kit, out, shot, word, color, hero_top=470, cx=0.5, cy=0.5, labels=('', ''), label_color=WHITE,
                    title='', subtitle='', hero_font='Anton-Regular.ttf', cut='subject', title_corner='bl'):
    """One picture, a huge word behind the subject (a turtle in open blue), tiny labels top left / right,
    a title (lines split by \\n) and a quiet subtitle at the bottom left - or, when the subject fills the bottom
    (the user on the stairs at Kelingking), right-aligned in the open top right ('tr')."""
    clip, t = shot; box = window(kit.frame(clip, t), (W, H), cx=cx, cy=cy)
    pic = kit.frame(clip, t).crop(box).resize((W, H), Image.LANCZOS); img = pic.copy()
    hero(img, word, hero_font, (M - 8, hero_top, W - M + 8, 0), color)
    if cut: img.paste(pic, (0, 0), kit.subject(clip, t, cut).crop(box).resize((W, H), Image.LANCZOS))
    small = font('InstrumentSans[wdth,wght].ttf', 24, wght=600)
    if labels[0]: text(img, (M, 70), labels[0], small, fill=label_color, track=7)
    if labels[1]: text(img, (W - M, 70), labels[1], small, fill=label_color, track=7, anchor='r')
    lines = title.split('\n') if title else []
    x, anchor = (M, 'l') if title_corner == 'bl' else (W - M, 'r')
    y = H - 122 - 88 * len(lines) if title_corner == 'bl' else 150
    for s in lines: text(img, (x, y), s, font('LXGWMarkerGothic-Regular.ttf', 60), anchor=anchor); y += 88
    if subtitle: text(img, (x, y), subtitle, font('ResourceHanRoundedCN-Light.ttf', 28), track=4, alpha=230, anchor=anchor)
    img.save(kit.path(out), quality=95)

def triptych_hero(kit, out, strips, word, color, hero_strip=1, inset=70, tag='', series='', hero_font='Anton-Regular.ttf', cut='subject'):
    """Three full-width strips [(clip, t, cx, cy[, zoom])]; in one of them a word spanning the strip sits behind the
    subject (the 'PHUKET' cover). Tiny tag above the word, series name at the bottom. The word never runs past its strip."""
    img = Image.new('RGB', (W, H))
    for k, (clip, t, cx, cy, *z) in enumerate(strips):
        box = window(kit.frame(clip, t), (W, SH), cx=cx, cy=cy, zoom=z[0] if z else 1.0)
        pic = kit.frame(clip, t).crop(box).resize((W, SH), Image.LANCZOS); img.paste(pic, (0, k * SH))
        if k != hero_strip: continue
        hero(img, word, hero_font, (24, k * SH + inset, W - 24, 0), color, max_h=SH - inset - 20)
        if cut: img.paste(pic, (0, k * SH), kit.subject(clip, t, cut).crop(box).resize((W, SH), Image.LANCZOS))
    if tag: text(img, (M, hero_strip * SH + 24), tag, font('InstrumentSans[wdth,wght].ttf', 22, wght=600), track=6)
    if series: series_label(img, series)
    img.save(kit.path(out), quality=95)

def triptych_signature(kit, out, strips, big, name, name_color=NAVY, strip=1, series=''):
    """Three strips; on a calm area of one strip a rounded 'Vlog' with the place name signing off on its baseline
    in a serif italic (the 'Vlog New Zealand' cover)."""
    img = Image.new('RGB', (W, H))
    for k, (clip, t, cx, cy, *z) in enumerate(strips): img.paste(crop(kit.frame(clip, t), (W, SH), cx, cy, *z), (0, k * SH))
    vf, nf = font('Fredoka[wdth,wght].ttf', 168, wght=600), font('InstrumentSerif-Italic.ttf', 66)
    l, tp, r, b = vf.getbbox(big); y = strip * SH + 34 - tp; text(img, (M - l, y), big, vf)
    base = y + vf.getmetrics()[0]
    text(img, (M - l + r + 30, base - nf.getmetrics()[0]), name, nf, fill=name_color)
    if series: series_label(img, series)
    img.save(kit.path(out), quality=95)

def triptych_breakout(kit, out, strips, word, place='', hook='', word_h=250, place_color=WHITE, hook_color=YELLOW,
                      hook_y=44, tag='', series='', cut='subject'):
    """Three strips; the person in the middle one breaks out of it - head over the top strip, in front of the place
    name standing on the top strip's lower edge (the '越南 Vietnam 15天2k' cover the user liked). A Chinese place name
    top right, a handwritten hook in the middle strip. Give the middle strip a zoom (2-2.5) and a cy that puts its top
    edge across the face: whatever of the subject is above that edge breaks out. The word is left-aligned, so put the
    person on the right (cx)."""
    img = Image.new('RGB', (W, H)); boxes = []
    for k, (clip, t, cx, cy, *z) in enumerate(strips):
        box = window(kit.frame(clip, t), (W, SH), cx, cy, z[0] if z else 1.0); boxes.append(box)
        img.paste(kit.frame(clip, t).crop(box).resize((W, SH), Image.LANCZOS), (0, k * SH))
    name, axes = 'BricolageGrotesque[opsz,wdth,wght].ttf', dict(opsz=96, wght=800)
    f = font(name, 400, **axes); l, tp, r, b = f.getbbox(word)
    f = font(name, int(400 * word_h / (b - tp)), **axes); l, tp, r, b = f.getbbox(word)
    text(img, (M - l, SH - 30 - b), word, f)
    clip, t = strips[1][:2]; x0, y0, x1, y1 = boxes[1]; s = W / (x1 - x0)
    ext = (x0, round(y0 - min(y0, SH / s)), x1, y1); size = (W, round((y1 - ext[1]) * s))
    pic = kit.frame(clip, t).crop(ext).resize(size, Image.LANCZOS)
    img.paste(pic, (0, 2 * SH - size[1]), kit.subject(clip, t, cut).crop(ext).resize(size, Image.LANCZOS))
    if place: text(img, (W - M, 52), place, font('SmileySans-Oblique.ttf', 112), fill=place_color, anchor='r')
    if hook: text(img, (M, SH + hook_y), hook, font('LXGWMarkerGothic-Regular.ttf', 88), fill=hook_color)
    if tag: text(img, (M, 40), tag, font('InstrumentSans[wdth,wght].ttf', 22, wght=600), track=6)
    if series: series_label(img, series)
    img.save(kit.path(out), quality=95)
