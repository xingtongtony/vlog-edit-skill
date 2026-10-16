"""Analyse how music is used in a finished vlog: music/voice timeline, track changes,
tempo per section, ducking depth, and whether cuts land on beats.
usage: music_analysis.py <sep_dir with no_vocals.wav, vocals.wav> <cuts.txt>"""
import sys
import numpy as np
import librosa

sep, cuts_file = sys.argv[1], sys.argv[2]
SR = 22050
mus, _ = librosa.load(f'{sep}/no_vocals.wav', sr=SR, mono=True)
voc, _ = librosa.load(f'{sep}/vocals.wav', sr=SR, mono=True)
dur = len(mus) / SR

def db(x): return 20 * np.log10(np.maximum(x, 1e-6))
hop = SR // 2  # 0.5 s frames
m_rms = db(librosa.feature.rms(y=mus, frame_length=SR, hop_length=hop)[0])
v_rms = db(librosa.feature.rms(y=voc, frame_length=SR, hop_length=hop)[0])
n = min(len(m_rms), len(v_rms)); m_rms, v_rms = m_rms[:n], v_rms[:n]
t = np.arange(n) * 0.5
voice_on = v_rms > -32
music_on = m_rms > -40

# --- timeline, one char per 5 s: V voice only, M music only, B both, . neither
print('timeline (each char = 5 s; M=music only, V=voice only, B=both, .=quiet)')
line = ''
for i in range(0, n, 10):
    v, m = voice_on[i:i + 10].mean() > 0.4, music_on[i:i + 10].mean() > 0.5
    line += 'B' if v and m else 'V' if v else 'M' if m else '.'
for k in range(0, len(line), 12):
    print(f'  {int(k * 5 // 60):2d}:{int(k * 5 % 60):02d}  {line[k:k + 12]}')

# --- ducking: music level with vs without voice
both = music_on & voice_on; solo = music_on & ~voice_on
print(f'\nmusic level: alone {np.median(m_rms[solo]):.1f} dB, under voice {np.median(m_rms[both]):.1f} dB '
      f'-> ducked by ~{np.median(m_rms[solo]) - np.median(m_rms[both]):.1f} dB')
print(f'voice-free stretches >= 15 s with music (breathers):')
run = 0
for i in range(n + 1):
    if i < n and solo[i]: run += 1
    else:
        if run * 0.5 >= 15: s = (i - run) * 0.5; print(f'  {int(s // 60)}:{int(s % 60):02d}  {run * 0.5:.0f}s')
        run = 0

# --- track changes: novelty on chroma+mfcc of the music stem
hop2 = 2048
chroma = librosa.feature.chroma_cqt(y=mus, sr=SR, hop_length=hop2)
mfcc = librosa.feature.mfcc(y=mus, sr=SR, n_mfcc=13, hop_length=hop2)
feat = np.vstack([librosa.util.normalize(chroma, axis=0), librosa.util.normalize(mfcc, axis=1)])
# average into 2 s blocks, then cosine distance between the 10 s before and after each point
blk = int(2 * SR / hop2)
F = np.array([feat[:, i:i + blk].mean(1) for i in range(0, feat.shape[1] - blk, blk)])
F = F / (np.linalg.norm(F, axis=1, keepdims=True) + 1e-9)
w = 5
nov = np.zeros(len(F))
for i in range(w, len(F) - w):
    a, b = F[i - w:i].mean(0), F[i:i + w].mean(0)
    nov[i] = 1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
thr = np.percentile(nov, 90)
peaks = [i for i in range(1, len(nov) - 1) if nov[i] > thr and nov[i] >= nov[i - 1] and nov[i] >= nov[i + 1]]
# merge peaks closer than 20 s
bounds = []
for p in peaks:
    if not bounds or (p - bounds[-1]) * 2 > 20: bounds.append(p)
print('\nlikely music changes (strongest novelty peaks):')
for p in bounds: print(f'  {int(p * 2 // 60)}:{int(p * 2 % 60):02d}  novelty {nov[p]:.3f}')

# --- tempo per section between changes
edges = [0] + [p * 2 for p in bounds] + [dur]
print('\nsections: tempo and level')
for a, b in zip(edges, edges[1:]):
    if b - a < 12: continue
    seg = mus[int(a * SR):int(b * SR)]
    tempo, beats = librosa.beat.beat_track(y=seg, sr=SR)
    tempo = float(np.atleast_1d(tempo)[0])
    onset = librosa.onset.onset_strength(y=seg, sr=SR).mean()
    lvl = db(np.sqrt(np.mean(seg ** 2)))
    print(f'  {int(a // 60)}:{int(a % 60):02d}-{int(b // 60)}:{int(b % 60):02d}  ~{tempo:5.1f} BPM  level {lvl:5.1f} dB  pulse {onset:.2f}')

# --- are cuts on beats? compare with random cut positions
_, beat_frames = librosa.beat.beat_track(y=mus, sr=SR, units='time')
beats = np.asarray(beat_frames)
cuts = np.array([float(x) for x in open(cuts_file) if x.strip()])
def near(xs, tol=0.08): return np.mean([np.min(np.abs(beats - x)) <= tol for x in xs])
rng = np.random.default_rng(0)
rand = rng.uniform(0, dur, 2000)
music_cuts = [c for c in cuts if music_on[min(int(c * 2), n - 1)] and not voice_on[min(int(c * 2), n - 1)]]
print(f'\ncuts within ±80 ms of a beat: all cuts {near(cuts):.0%} ({len(cuts)}), '
      f'cuts in music-only parts {near(music_cuts):.0%} ({len(music_cuts)}), random baseline {near(rand):.0%}')
