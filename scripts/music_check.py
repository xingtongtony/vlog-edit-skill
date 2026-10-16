"""Can this song go under an island / underwater / talking scene? Measured, because Claude cannot listen.

usage: ~/.venvs/tts/bin/python music_check.py <track.mp3> [...]
Each limit comes from a song the user rejected (docs/storytelling.md, music section):
  drums     NOT (percussive share > 11 % and bass > 20 %)   Shaloza "Mirihi" 28 % / 72 % -> "跳脱，和海岛不搭"
            (a kit has a kick drum; a strummed ukulele has percussive attacks but no bass - Monplaisir "Hop" 17 % / 0 % is fine)
  heavy     energy below ~130 Hz <= 35 %            John Bartmann ambient 55-77 %  -> "贼沉重"
  dark      spectral centroid >= 1000 Hz            John Bartmann 300-470 Hz       -> "贼沉重"
  rock      NOT (loudness density >= 0.75 and       Monplaisir "On est bien, là"   -> "暴躁，重金属摇滚"
                 energy above 4 kHz >= 3 %)          0.88 / 4.7 %
  mode      major key (Krumhansl profiles) - minor is reported, not rejected
The cold-open montage (<= 20 s) and its bookend at the end may use a heavier song (Summer Tropical House: bass 68 %).
Songs the user accepted, for reference: Monplaisir "Relaxing Ukulele" (drums 3-10 %, bass ~0 %, centroid 1100-1500 Hz,
density 0.03-0.27), Music for Creators "Upbeat Inspiring Folk Acoustic Guitar".
"""
import os, sys
import numpy as np

LIMITS = {'drums': 0.11, 'bass': 0.35, 'centroid': 1000, 'density': 0.75, 'hi': 0.03}
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

def measure(path):
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True, offset=5, duration=120)
    _, p = librosa.effects.hpss(y)
    S = np.abs(librosa.stft(y)) ** 2; fr = librosa.fft_frequencies(sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(1)
    key = max((np.corrcoef(np.roll(t, k), chroma)[0, 1], m) for k in range(12) for t, m in ((MAJ, 'major'), (MIN, 'minor')))
    return {'drums': float(np.sum(p ** 2) / np.sum(y ** 2)), 'bass': float(S[fr < 130].sum() / S.sum()),
            'centroid': float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))),
            'density': float(np.percentile(rms, 10) / (np.percentile(rms, 90) + 1e-9)), 'hi': float(S[fr > 4000].sum() / S.sum()),
            'mode': key[1], 'duration': librosa.get_duration(path=path)}

def verdict(m):
    bad = []
    if m['drums'] > LIMITS['drums'] and m['bass'] > 0.20: bad.append(f"drum kit {m['drums']:.0%} with bass {m['bass']:.0%}")
    if m['bass'] > LIMITS['bass']: bad.append(f"heavy bass {m['bass']:.0%}")
    if m['centroid'] < LIMITS['centroid']: bad.append(f"dark {m['centroid']:.0f} Hz")
    if m['density'] >= LIMITS['density'] and m['hi'] >= LIMITS['hi']: bad.append(f"distorted / rock (density {m['density']:.2f}, >4k {m['hi']:.1%})")
    return bad

def main():
    for path in sys.argv[1:]:
        m = measure(path); bad = verdict(m)
        print(f"{'NO ' if bad else 'OK '} {os.path.basename(path)[:55]:55s} {m['duration']:4.0f}s {m['mode']:5s} "
              f"drums {m['drums']:4.0%} bass {m['bass']:4.0%} {m['centroid']:5.0f}Hz density {m['density']:.2f} >4k {m['hi']:4.1%}"
              + (f"  <- {'; '.join(bad)}" if bad else ''))

if __name__ == '__main__':
    main()
