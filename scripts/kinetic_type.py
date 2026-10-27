"""The film title (opening and ending, "Bali / 巴厘岛"), animated the way the user's own Bohol title moves, written as an
alpha movie (qtrle) to lay over the shot. Drawn frame by frame in PIL at 2x, so every move is sub-pixel smooth.

The user's rules (2026-10-27, after "片头和片尾的字体…还不是很高级"): the Latin and the Chinese line are exactly the same
width (ink edge to ink edge), the block is small and centred, the motion simple and smooth. The motion is learnt from
their Bohol episode (projects/_references/bohol-published, 15.1-19.4 s): the Latin glyphs come in one by one (0.1 s
apart); when they are in, the line glides up to make room and the Chinese glyphs come in under it (0.2 s apart), so
the two lines stay centred together; at the end the glyphs leave one by one. Here each glyph fades in out of a slight
blur and a few pixels' drift, and fades out the same way in a scattered order (Bohol broke them into pieces).

Pairings (fonts/README.md): bohol = Lilita One + 思源宋体 Black with the worn print of the user's Bohol title;
serif = Instrument Serif Italic + 资源圆体 Light; tall = Big Shoulders light capitals + 资源圆体 Light;
young = Bricolage Grotesque bold + 得意黑.
usage (lab): python3 kinetic_type.py <pairing> <out.mov> <seconds> "<中文>" "<Latin>"
"""
import os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FF = '/opt/homebrew/bin/ffmpeg'
FD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts')
WHITE = (252, 250, 246)
# pairing: Latin font, axes, upper-case?, Chinese font, Chinese glyph height / Latin cap height, worn print, block width
# (of the frame; a condensed face gets less, or its tall capitals make the block big)
PAIRS = {
    'bohol': ('LilitaOne-Regular.ttf', {}, False, 'NotoSerifSC-Black.otf', 1.0, True, 0.14),
    'serif': ('InstrumentSerif-Italic.ttf', {}, False, 'ResourceHanRoundedCN-Light.ttf', 0.62, False, 0.13),
    'tall': ('BigShoulders[opsz,wght].ttf', {'wght': 300, 'opsz': 72}, True, 'ResourceHanRoundedCN-Light.ttf', 0.42, False, 0.095),
    'young': ('BricolageGrotesque[opsz,wdth,wght].ttf', {'wght': 700, 'wdth': 100, 'opsz': 96}, True, 'SmileySans-Oblique.ttf', 1.05, False, 0.13),
}

def font(name, px, axes=None):
    f = ImageFont.truetype(os.path.join(FD, name), max(1, round(px)))
    if axes:
        key = {'Weight': 'wght', 'Width': 'wdth', 'Optical size': 'opsz'}; ax = f.get_variation_axes()
        f.set_variation_by_axes([axes.get(key[a['name'].decode()], a['default']) for a in ax])
    return f

def ease_out(u): u = min(1.0, max(0.0, u)); return 1 - (1 - u) ** 3
def ease_inout(u): u = min(1.0, max(0.0, u)); return 3 * u * u - 2 * u * u * u

def glyphs(text, f, track):
    """[(tile, ink box (x0, y0, x1, y1) relative to the line's ink origin)] and the line's ink size: each glyph a
    sharp RGBA tile, placed by advance + tracking, measured by its ink (so two lines can match edge to edge)."""
    d = ImageDraw.Draw(Image.new('L', (1, 1))); pad = round(f.size * 0.3); x = 0.0; out = []
    for ch in text:
        if ch.strip():
            l, t, r, b = f.getbbox(ch)
            tile = Image.new('L', (r - l + 2 * pad, b - t + 2 * pad), 0); ImageDraw.Draw(tile).text((pad - l, pad - t), ch, font=f, fill=255)
            out.append((tile, (x + l - pad, t - pad)))
        x += d.textlength(ch, font=f) + track
    x0 = min(p[0] + pad for _, p in out); x1 = max(p[0] + t.width - pad for t, p in out)
    y0 = min(p[1] + pad for _, p in out); y1 = max(p[1] + t.height - pad for t, p in out)
    return [(t, (p[0] - x0, p[1] - y0)) for t, p in out], (x1 - x0, y1 - y0)

def worn(alpha, seed):
    """The worn print of the user's Bohol title (render_preview.grunge_mask) on one glyph."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from render_preview import grunge_mask
    return grunge_mask(alpha, seed=seed)

class Title:
    """Both lines laid out at 2x: the Latin line set to `width` (fraction of the frame width) by its ink, the Chinese
    line at its pairing's height, tracked to the very same ink width."""
    def __init__(self, pair, cn, latin, W2, H2, width=None, y=0.5, gap=0.42):
        lf, axes, upper, cf, ratio, rough, w0 = PAIRS[pair]; latin = latin.upper() if upper else latin; width = width or w0
        size = 100.0
        for _ in range(3):              # the Latin size whose ink is `width` of the frame
            g, (w, h) = glyphs(latin, font(lf, size, axes), size * 0.02); size *= W2 * width / w
        f1 = font(lf, size, axes); self.l1, (w1, h1) = glyphs(latin, f1, size * 0.02)
        cap = f1.getbbox('H')[3] - f1.getbbox('H')[1]
        csize = cap * ratio / 0.88       # a CJK glyph's ink is ~0.88 of its size
        f2 = font(cf, csize); g2, (w2, _) = glyphs(cn, f2, 0)
        track = (w1 - w2) / max(1, len(cn) - 1)
        if track < 0:                    # wider than the Latin even untracked: shrink it until it fits
            csize *= w1 / w2; f2 = font(cf, csize); g2, (w2, _) = glyphs(cn, f2, 0); track = (w1 - w2) / max(1, len(cn) - 1)
        self.l2, (w2, h2) = glyphs(cn, f2, track)
        if rough:
            self.l1 = [(worn(t, 4 + k), p) for k, (t, p) in enumerate(self.l1)]
            self.l2 = [(worn(t, 9 + k), p) for k, (t, p) in enumerate(self.l2)]
        self.g = gap * cap; self.h1, self.h2, self.w = h1, h2, w1
        self.cx, self.cy = W2 / 2, H2 * y

def frame(T, t, dur, W2, H2):
    """Latin in one by one (0.1 s apart, 0.5 s each) centred alone; then the line glides up as the Chinese comes in
    under it (0.2 s apart); hold; out one by one in a scattered order over the last 0.9 s."""
    S = H2 / 1080; fr = Image.new('RGBA', (W2, H2), (0, 0, 0, 0))
    n1, n2 = len(T.l1), len(T.l2); t2 = 0.1 * (n1 - 1) + 0.45          # the Chinese starts as the last Latin glyph lands
    lift = ease_inout((t - t2) / 0.6) * (T.g + T.h2) / 2               # the block re-centres as the second line arrives
    top1 = T.cy - T.h1 / 2 - lift; top2 = top1 + T.h1 + T.g; x0 = T.cx - T.w / 2
    order = np.random.default_rng(7).permutation(n1 + n2); out0 = dur - 0.9
    for k, (tile, (gx, gy)) in enumerate(T.l1 + T.l2):
        first = k < n1; t0 = 0.1 * k if first else t2 + 0.2 * (k - n1)
        e = ease_out((t - t0) / 0.5); o = ease_out((t - out0 - order[k] * 0.06) / 0.4)
        a = e * (1 - o)
        if a <= 0.003: continue
        blur = (1 - e) * 5 * S + o * 4 * S; dy = (1 - e) * 6 * S - o * 4 * S
        tl = tile.filter(ImageFilter.GaussianBlur(blur)) if blur > 0.3 else tile
        al = tl.point(lambda v: int(v * a)); x = x0 + gx; y = (top1 if first else top2) + gy + dy
        fr.paste(Image.new('RGBA', tl.size, WHITE + (255,)), (int(round(x)), int(round(y))), al)
    return fr

def render(pair, out, dur, cn, latin, W=1920, H=1080, fps=30, width=None, y=0.5):
    W2, H2 = W * 2, H * 2; T = Title(pair, cn, latin, W2, H2, width, y)
    p = subprocess.Popen([FF, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{W}x{H}', '-r', str(fps), '-i', '-',
                          '-c:v', 'qtrle', '-pix_fmt', 'argb', out], stdin=subprocess.PIPE)
    for k in range(round(dur * fps)):
        fr = frame(T, k / fps, dur, W2, H2).resize((W, H), Image.LANCZOS)
        sh = Image.new('RGBA', (W, H), (0, 0, 0, 0))        # a faint soft shadow so white reads on bright water
        sh.putalpha(fr.getchannel('A').filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.35)))
        p.stdin.write(Image.alpha_composite(sh, fr).tobytes())
    p.stdin.close(); p.wait()

if __name__ == '__main__':
    pair, out, dur, cn, latin = sys.argv[1:6]
    render(pair, out, float(dur), cn, latin)
