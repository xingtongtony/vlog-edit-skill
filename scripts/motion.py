"""Camera motion per second: shake, roll ("歪歪扭扭") and, for drone shots, whether the move keeps one direction.

usage: ~/.venvs/tts/bin/python motion.py <project_dir> [clip ids...]    (needs OpenCV; no ids = every clip > 4 s)
Writes ingest/motion.json: {clip: {"fps": 10, "sec": [[shake_px, roll_deg_s, dx, dy, zoom], ...]}}

Between frames (10 fps, 320x180 grey) features are tracked (Lucas-Kanade) and a similarity transform is fitted:
translation dx/dy (px/frame), rotation, scale. Per second:
  shake  - high-frequency part of the translation (jitter around the smoothed path), px/frame
  roll   - mean absolute rotation rate, degrees per second (an underwater camera rolling side to side)
  dx, dy - smoothed translation (direction of a pan / drone move); zoom - smoothed scale change per second (push in > 0)
`window_report()` turns these into a verdict for a shot: steady enough? and for drones: one direction, no reversal?
Thresholds were set on the Bohol footage (steady fish-school shots vs. the clips the user rejected).
"""
import json, os, subprocess, sys
import numpy as np

FF = '/opt/homebrew/bin/ffmpeg'; W, H, FPS = 320, 180, 10
# B-roll limits (mean over the shot / worst second). Set on Bohol: the user's praised fish-school shot (c154) has
# jitter 1.0 / roll 2.8 deg/s; the underwater shots they called "歪歪扭扭" had roll 8-41 deg/s.
BROLL = {'shake_mean': 2.5, 'shake_max': 4.5, 'roll_mean': 4.5, 'roll_max': 9.0}
TALK = {'shake_mean': 5.0, 'shake_max': 99, 'roll_mean': 8.0, 'roll_max': 99}    # a talking shot is handheld by nature
ROLL_MAX = BROLL['roll_max']
DRONE_MIN_MOVE = (0.3, 0.3, 0.6)      # pan x / pan y (px/frame at 320 px) and zoom (%/s) below which a component isn't moving

def frames(path):
    raw = subprocess.run([FF, '-v', 'error', '-i', path, '-vf', f'fps={FPS},scale={W}:{H},format=gray', '-f', 'rawvideo', '-'],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W)

def transforms(f):
    import cv2
    out = []
    for a, b in zip(f[:-1], f[1:]):
        p0 = cv2.goodFeaturesToTrack(a, 150, 0.01, 8)
        m = None
        if p0 is not None and len(p0) >= 8:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, winSize=(21, 21), maxLevel=3)
            ok = st.ravel() == 1
            if ok.sum() >= 8:
                m, _ = cv2.estimateAffinePartial2D(p0[ok], p1[ok], method=cv2.RANSAC, ransacReprojThreshold=2.0)
        if m is None: out.append((0.0, 0.0, 0.0, 1.0)); continue
        s = float(np.hypot(m[0, 0], m[1, 0])); r = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
        out.append((float(m[0, 2]), float(m[1, 2]), r, s))
    return np.array(out, np.float32)

def analyse(path):
    f = frames(path)
    if len(f) < 6: return None
    t = transforms(f)
    k = np.ones(7) / 7; sm = lambda x: np.convolve(x, k, mode='same')
    dx, dy, rot, sc = t[:, 0], t[:, 1], t[:, 2], t[:, 3]
    jit = np.hypot(dx - sm(dx), dy - sm(dy))
    sec = []
    for i in range(0, len(t), FPS):
        j = slice(i, i + FPS)
        sec.append([round(float(jit[j].mean()), 2), round(float(np.abs(rot[j]).mean() * FPS), 2),
                    round(float(sm(dx)[j].mean()), 2), round(float(sm(dy)[j].mean()), 2), round(float((sm(sc)[j].mean() - 1) * FPS * 100), 2)])
    return {'fps': FPS, 'sec': sec}

def window_report(m, a, b, drone=False, talk=False):
    """Verdict for source window [a, b) of one clip: (ok, reason)."""
    if not m: return True, 'no motion data'
    rows = m['sec'][int(a):max(int(a) + 1, int(np.ceil(b)))]
    if not rows: return True, 'no motion data'
    s = np.array(rows)
    if not drone:
        L = TALK if talk else BROLL
        v = {'shake_mean': s[:, 0].mean(), 'shake_max': s[:, 0].max(), 'roll_mean': s[:, 1].mean(), 'roll_max': s[:, 1].max()}
        bad = [f'{k} {v[k]:.1f} > {L[k]}' for k in L if v[k] > L[k]]
        if bad: return False, ('shaky / rolling: ' + ', '.join(bad))
        return True, f"steady (jitter {v['shake_mean']:.1f}, roll {v['roll_mean']:.1f})"
    # drone: each moving component keeps its sign for the whole window (no back-and-forth), no shake
    for col, name, lim in ((2, 'pan x', DRONE_MIN_MOVE[0]), (3, 'pan y', DRONE_MIN_MOVE[1]), (4, 'zoom', DRONE_MIN_MOVE[2])):
        v = s[:, col]; big = v[np.abs(v) > lim]
        if len(big) and (big > 0).any() and (big < 0).any(): return False, f'{name} reverses within the shot'
    if s[:, 1].max() > ROLL_MAX: return False, 'drone rotates unevenly'
    return True, 'one-direction move'

def best_windows(m, dur, drone=False, lo=0, hi=None, n=5, talk=False):
    """Start seconds of the best `dur`-second windows (verdict ok, least jitter)."""
    sec = m['sec']; hi = min(hi or len(sec), len(sec)) - dur
    cands = []
    for s in range(int(lo), int(hi) + 1):
        ok, _ = window_report(m, s, s + dur, drone, talk)
        if ok: cands.append((float(np.mean([r[0] + 0.2 * r[1] for r in sec[s:s + int(np.ceil(dur))]])), s))
    return [s for _, s in sorted(cands)[:n]]

def main():
    project = sys.argv[1]; ing = os.path.join(project, 'ingest')
    clips = {c['id']: c for c in json.load(open(f'{ing}/clips.json'))}
    ids = sys.argv[2:] or [k for k, c in clips.items() if c['duration'] > 4]
    out_p = f'{ing}/motion.json'; out = json.load(open(out_p)) if os.path.exists(out_p) else {}
    for k, cid in enumerate(ids, 1):
        if cid in out and len(sys.argv) <= 2: continue
        r = analyse(f'{ing}/proxy/{cid}.mp4')
        if r: out[cid] = r
        if k % 10 == 0: json.dump(out, open(out_p, 'w')); print(f'  {k}/{len(ids)}', flush=True)
    json.dump(out, open(out_p, 'w')); print('done', len(out))

if __name__ == '__main__':
    main()
