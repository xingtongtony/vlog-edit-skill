"""Hair-level cut-out from Vision's rough mask: alpha matting plus foreground colours (pymatting, in ~/.venvs/tts).
usage: ~/.venvs/tts/bin/python refine_matte.py <frame.png> <rough_mask.png> <out_alpha.png> <out_fg.png>

Vision's mask is one smooth blob: around spiky hair it takes the sky between the spikes along, and pasted over another
picture the hair carries a pale ring of its old background (Bali cover D, the user spotted it). Here the blob only sets
a trimap - eroded = surely subject, dilated outside = surely background, the band between unknown - and closed-form
matting finds the alpha strand by strand; estimate_foreground_ml then takes the old background's colour out of the
half-transparent strands. Band widths scale with the frame: 30 px eroded / 2 px dilated at 2160 p.
The matte may only take things out of Vision's blob (the sky between the spikes), never add: a dark boat pole right
behind the hair looked like more hair and came out as a dark smudge over the new background (cover D).
"""
import sys
import numpy as np
from PIL import Image
from scipy import ndimage
from pymatting import estimate_alpha_cf, estimate_foreground_ml

src, rough, out_alpha, out_fg = sys.argv[1:5]
img = Image.open(src).convert('RGB'); mask = np.asarray(Image.open(rough).convert('L')) > 128
x0, y0, x1, y1 = Image.fromarray(mask.astype(np.uint8) * 255).getbbox()
inner, outer = max(8, round(img.height / 72)), max(1, round(img.height / 1080))
pad = 4 * inner                                      # work on the subject's box only: the rest is surely background
x0, y0, x1, y1 = max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)
I = np.asarray(img.crop((x0, y0, x1, y1)), dtype=np.float64) / 255; M = mask[y0:y1, x0:x1]
tri = np.full(M.shape, 0.5); grown = ndimage.binary_dilation(M, iterations=outer)
tri[ndimage.binary_erosion(M, iterations=inner)] = 1.0
tri[~grown] = 0.0
alpha = np.clip(estimate_alpha_cf(I, tri), 0, 1) * grown; fg = np.clip(estimate_foreground_ml(I, alpha), 0, 1)
A = np.zeros((img.height, img.width), np.uint8); A[y0:y1, x0:x1] = np.round(alpha * 255)
Image.fromarray(A).save(out_alpha)
F = np.asarray(img).copy(); F[y0:y1, x0:x1] = np.round(fg * 255)
Image.fromarray(F).save(out_fg)
print(f'matte {x1 - x0}x{y1 - y0}, band {inner}/{outer} px -> {out_alpha}')
