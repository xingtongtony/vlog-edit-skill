"""Measure a folder of songs for the music catalogue (what each track can carry in a cue sheet).

usage: ~/.venvs/tts/bin/python music_catalog.py <music dir> <out.json> [--no-vocals] [--only 'name|name']
  --no-vocals   skip the Demucs split (about a minute per song); --only runs it just for names containing these

Per track: duration, tempo (BPM), key and mode, the music_check measures (drum share, bass share, spectral centroid,
density, > 4 kHz share), loudness over time (quiet intro seconds, where the biggest rise lands, how much it moves), and
the share of energy in the vocal stem (Demucs two-stem split; > ~0.25 means someone sings or raps through most of it -
it cannot sit under narration or talk). Resumes: tracks already in out.json are skipped.
"""
import json, os, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from music_check import measure, verdict

def profile(path):
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
    rms = librosa.feature.rms(y=y, hop_length=2205)[0]                     # 10 per second
    sec = np.array([rms[i:i + 10].mean() for i in range(0, len(rms), 10)]) + 1e-9
    db = 20 * np.log10(sec / sec.max())
    quiet = int(np.argmax(db > -20)) if (db > -20).any() else len(db)
    m = np.convolve(db, np.ones(4) / 4, mode='valid')                      # 4 s means; rise = vs the 4 s before
    rise = m[4:] - m[:-4] if len(m) > 4 else np.zeros(1)
    return {'bpm': round(tempo), 'quiet_intro_s': quiet, 'biggest_rise_s': int(np.argmax(rise)) + 4 if len(m) > 4 else 0,
            'dynamics_db': round(float(np.percentile(db, 95) - np.percentile(db, 10)), 1)}

def vocal_share(path):
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, '-m', 'demucs', '--two-stems=vocals', '-d', 'mps', '-o', d, path],
                       check=True, capture_output=True)
        import librosa
        stem = lambda n: next(os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs if f == f'{n}.wav')
        v, _ = librosa.load(stem('vocals'), sr=11025, mono=True); o, _ = librosa.load(stem('no_vocals'), sr=11025, mono=True)
        return round(float(np.sum(v ** 2) / (np.sum(v ** 2) + np.sum(o ** 2) + 1e-9)), 3)

def main():
    src, out = sys.argv[1], sys.argv[2]
    data = json.load(open(out)) if os.path.exists(out) else {}
    only = sys.argv[sys.argv.index('--only') + 1].split('|') if '--only' in sys.argv else None   # vocals only for these
    files = sorted(f for f in os.listdir(src) if f.lower().endswith(('.mp3', '.m4a', '.wav', '.flac')))
    for k, f in enumerate(files, 1):
        want_vocals = '--no-vocals' not in sys.argv and (not only or any(o in f for o in only))
        if f in data and ('vocals' in data[f] or not want_vocals): continue
        p = os.path.join(src, f)
        if f in data: r = data[f]
        else:
            m = measure(p); r = {k_: (round(v, 3) if isinstance(v, float) else v) for k_, v in m.items()}
            r.update(profile(p)); r['check'] = verdict(m) or ['OK']
        if want_vocals: r['vocals'] = vocal_share(p)
        data[f] = r; json.dump(data, open(out, 'w'), ensure_ascii=False, indent=1)
        print(f'{k}/{len(files)} {f[:50]}: {r["bpm"]} BPM {r["mode"]}, drums {r["drums"]:.0%}, vocals {r.get("vocals", "-")}', flush=True)

if __name__ == '__main__':
    main()
