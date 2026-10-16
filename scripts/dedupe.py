"""Find clips that are the same footage under different file names (shared copies, re-exports).

usage: python3 dedupe.py <project_dir>   -> ingest/duplicates.json  {clip: canonical_clip}
Fingerprint = durations within 0.3 s and 64-bit average hashes of frames at 20/50/80 % within 10 bits.
The canonical clip is the one from the user's own camera (VID_/DJI_/IMG_) when there is one.
"""
import json, os, subprocess, sys
import numpy as np

FF = '/opt/homebrew/bin/ffmpeg'

def ahash(proxy, t):
    raw = subprocess.run([FF, '-v', 'error', '-ss', f'{t:.2f}', '-i', proxy, '-frames:v', '1', '-vf', 'scale=8:8,format=gray',
                          '-f', 'rawvideo', '-'], capture_output=True).stdout
    if len(raw) < 64: return None
    a = np.frombuffer(raw[:64], np.uint8); return a > a.mean()

def main():
    project = sys.argv[1]; ing = os.path.join(project, 'ingest')
    clips = json.load(open(f'{ing}/clips.json')); fp = {}
    for c in clips:
        hs = [ahash(f'{ing}/proxy/{c["id"]}.mp4', c['duration'] * p) for p in (0.2, 0.5, 0.8)]
        if all(h is not None for h in hs): fp[c['id']] = (c['duration'], hs)
    own = lambda c: c['name'].startswith(('VID_', 'DJI_', 'IMG_'))
    byid = {c['id']: c for c in clips}; dup = {}
    ids = list(fp)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (da, ha), (db, hb) = fp[a], fp[b]
            if abs(da - db) < 0.3 and all(int((x != y).sum()) <= 10 for x, y in zip(ha, hb)):
                keep, drop = (a, b) if own(byid[a]) or not own(byid[b]) else (b, a)
                dup[drop] = dup.get(keep, keep)
    json.dump(dup, open(f'{ing}/duplicates.json', 'w'), indent=1)
    for d, k in dup.items(): print(f'{d} {byid[d]["name"]:28s} == {k} {byid[k]["name"]}')

if __name__ == '__main__':
    main()
