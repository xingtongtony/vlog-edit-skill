"""Profile candidate tracks: tempo, energy curve, biggest rises (good places for a title hit).
usage: <venv python> music_profile.py <track> [<track> ...]"""
import sys, numpy as np, librosa
for path in sys.argv[1:]:
    y, sr = librosa.load(path, sr=22050, mono=True); dur = len(y) / sr
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time'); tempo = float(np.atleast_1d(tempo)[0])
    hop = sr // 2; rms = librosa.feature.rms(y=y, frame_length=sr, hop_length=hop)[0]; db = 20 * np.log10(rms + 1e-6); db -= db.max()
    curve = ''.join(' ▁▂▃▄▅▆▇█'[min(8, max(0, int((d + 30) / 30 * 8)))] for d in db[::4])   # one char per 2 s
    # rises: increase of the 4 s mean vs the previous 4 s
    m = np.convolve(db, np.ones(8) / 8, mode='same'); rise = np.r_[np.zeros(8), m[8:] - m[:-8]]
    top = sorted(range(len(rise)), key=lambda i: -rise[i]); picks = []
    for i in top:
        if all(abs(i - j) > 16 for j in picks): picks.append(i)
        if len(picks) == 3: break
    print(f'{path.split("/")[-1]}: {dur:.0f}s, ~{tempo:.0f} BPM, beat {60 / tempo:.2f}s')
    print('   energy (2s/char):', curve)
    print('   biggest rises at:', ', '.join(f'{i * 0.5:.1f}s (+{rise[i]:.0f} dB)' for i in sorted(picks)))
