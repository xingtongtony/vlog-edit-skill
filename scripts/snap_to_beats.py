"""Move cuts in a range of shots onto the music's beats.

usage: <venv python> snap_to_beats.py <edl.json> <first_cut_shot> <last_cut_shot>
Cut k is the boundary between shot k-1 and shot k. Each cut moves to the nearest beat by changing
the previous shot's `out` (its `in` stays). Shots with live speech only grow, so no word is cut off.
Writes the EDL in place and prints what moved.
"""
import json, sys
import numpy as np, librosa

edl_path, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
edl = json.load(open(edl_path)); m = edl['music']; shots = edl['shots']; fps = edl.get('fps', 30)
y, sr = librosa.load(m['path'], sr=22050, mono=True)
tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time'); period = 60 / float(np.atleast_1d(tempo)[0])
t = 0.0; starts = []
for s in shots: starts.append(t); t += round((s['out'] - s['in']) * fps) / fps
at = starts[m['at_shot']] + m.get('at_offset', 0) if 'at_shot' in m else m.get('at', 0)
beats = np.asarray(beats) - m.get('start', 0) + at                   # beat times on the timeline
shift = 0.0
for k in range(first, last + 1):
    prev = shots[k - 1]; cut = starts[k] + shift
    cand = beats[beats > starts[k - 1] + shift + 0.8]                    # keep every shot at least 0.8 s
    if prev.get('audio') == 'live': cand = cand[cand >= cut - 0.02]      # never shorten a speaking shot
    if not len(cand): continue
    b = cand[np.argmin(np.abs(cand - cut))]; d = round((b - cut) * fps) / fps
    if abs(d) > 0.55 * period:                                           # no beat close by (e.g. quiet intro): leave it
        print(f'cut {k:2d} ({prev["clip"]}→{shots[k]["clip"]}): {cut:6.2f}s  no beat within half a beat, unchanged'); continue
    prev['out'] = round(prev['out'] + d, 3); shift += d
    print(f'cut {k:2d} ({prev["clip"]}→{shots[k]["clip"]}): {cut:6.2f}s → {cut + d:6.2f}s ({d * 1000:+.0f} ms)')
json.dump(edl, open(edl_path, 'w'), ensure_ascii=False, indent=2)
