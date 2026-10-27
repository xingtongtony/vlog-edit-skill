"""Specimen sheet of every font in fonts/: name, sample line, and whether it has simplified / traditional Chinese.

usage: python3 font_specimen.py [out.png]          (default: fonts/specimen.png)
Most handwritten Chinese fonts are simplified-only: 島 / 賓 / 爾 / 獨 come out as an empty box. A glyph counts as missing
when it renders exactly like the font's .notdef box (Pillow only, no fontTools).
"""
import glob, os, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); FONTS = os.path.join(ROOT, 'fonts')
SIMPLIFIED, TRADITIONAL = '岛宾环游记', '島賓爾獨當'
CJK_SAMPLE, LATIN_SAMPLE = '薄荷岛 · 搓泥菲律宾环游记', 'Tony Vlog · Bohol 2026'

def _render(f, c):
    im = Image.new('L', (int(f.size * 2.5),) * 2); ImageDraw.Draw(im).text((f.size // 4, 0), c, font=f, fill=255); return im.tobytes()

def missing(f, text):
    tofu = _render(f, '\U0010FFFD')
    return [c for c in text if not c.isspace() and _render(f, c) == tofu]

def regular(f):
    try: axes = f.get_variation_axes()
    except Exception: return f
    f.set_variation_by_axes([max(a['minimum'], min(400, a['maximum'])) if a.get('name') in (b'Weight', 'Weight') else a['default'] for a in axes]); return f   # regular, the weight the pipeline mostly uses

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(FONTS, 'specimen.png')
    rows = []
    for p in sorted(glob.glob(os.path.join(FONTS, '*.ttf')) + glob.glob(os.path.join(FONTS, '*.otf'))):   # .otf too: 思源宋体 Black was missing
        try: f = ImageFont.truetype(p, 56)
        except OSError: rows.append((os.path.basename(p), None, 'unreadable (still downloading?)')); continue
        simp = not missing(f, SIMPLIFIED); trad = not missing(f, TRADITIONAL)
        rows.append((os.path.basename(p), f, ('简' if simp else '') + ('繁' if trad else '') or 'Latin only'))
    W, RH = 1800, 110; img = Image.new('RGB', (W, RH * len(rows) + 30), (250, 248, 244)); d = ImageDraw.Draw(img)
    label = ImageFont.truetype(os.path.join(FONTS, 'ResourceHanRoundedCN-Light.ttf'), 22)
    for k, (name, f, cov) in enumerate(rows):
        y = 15 + k * RH
        d.text((24, y + 8), name, font=label, fill=(90, 90, 90)); d.text((24, y + 44), cov, font=label, fill=(180, 80, 40))
        if f is not None:
            sample = CJK_SAMPLE if '简' in cov else LATIN_SAMPLE
            if '简' in cov and missing(f, sample): sample = ''.join(c for c in sample if c not in missing(f, sample))
            d.text((470, y + 14), sample, font=regular(f) if 'wght' in name else f, fill=(25, 25, 25))
        d.line((24, y + RH - 4, W - 24, y + RH - 4), fill=(225, 220, 212))
    img.save(out); print(out, len(rows), 'fonts')
    for name, f, cov in rows: print(f'  {name:36s} {cov}')

if __name__ == '__main__':
    main()
