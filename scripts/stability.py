"""Shake analysis: find the steadiest windows in each clip (handheld / underwater footage is often unusable).

usage: python3 stability.py <project_dir> [clip ids...]     (no ids = every clip longer than 6 s)
Writes ingest/stability.json: {clip: {"per_sec": [shake...], "best": [[start, end, score], ...]}}
Shake = frame-to-frame global translation (phase correlation on 5 fps, 160x90 grey) - its high-frequency part,
so a smooth pan scores low and a jittery hand scores high. Lower is steadier.
"""
import json, os, subprocess, sys
import numpy as np

FF = '/opt/homebrew/bin/ffmpeg'; W, H, FPS = 160, 90, 5

def frames(proxy):
    raw = subprocess.run([FF, '-v', 'error', '-i', proxy, '-vf', f'fps={FPS},scale={W}:{H},format=gray', '-f', 'rawvideo', '-'],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)

def shift(a, b):
    win = np.outer(np.hanning(H), np.hanning(W))
    A = np.fft.fft2((a - a.mean()) * win); B = np.fft.fft2((b - b.mean()) * win)
    R = A * np.conj(B); R /= np.abs(R) + 1e-6
    r = np.abs(np.fft.ifft2(R)); y, x = np.unravel_index(np.argmax(r), r.shape)
    if y > H // 2: y -= H
    if x > W // 2: x -= W
    return x, y

def analyse(proxy, win=3.0):
    f = frames(proxy)
    if len(f) < 4: return None
    d = np.array([shift(f[i], f[i + 1]) for i in range(len(f) - 1)], np.float32)
    smooth = np.stack([np.convolve(d[:, k], np.ones(5) / 5, mode='same') for k in (0, 1)], 1)
    jitter = np.linalg.norm(d - smooth, axis=1) + 0.25 * np.linalg.norm(smooth, axis=1)   # shake + a little for fast pans
    per_sec = [float(np.mean(jitter[i:i + FPS])) for i in range(0, len(jitter), FPS)]
    n = int(win)
    scores = [(i, float(np.mean(per_sec[i:i + n]))) for i in range(0, max(1, len(per_sec) - n + 1))]
    best, used = [], set()
    for i, sc in sorted(scores, key=lambda x: x[1]):
        if any(j in used for j in range(i, i + n)): continue
        best.append([i, i + n, round(sc, 2)]); used.update(range(i, i + n))
        if len(best) == 6: break
    return {'per_sec': [round(x, 2) for x in per_sec], 'best': sorted(best)}

def main():
    project = sys.argv[1]; ing = os.path.join(project, 'ingest')
    clips = {c['id']: c for c in json.load(open(f'{ing}/clips.json'))}
    ids = sys.argv[2:] or [k for k, c in clips.items() if c['duration'] > 6]
    out_p = f'{ing}/stability.json'; out = json.load(open(out_p)) if os.path.exists(out_p) else {}
    for k, cid in enumerate(ids, 1):
        if cid in out: continue
        r = analyse(f'{ing}/proxy/{cid}.mp4')
        if r: out[cid] = r
        if k % 10 == 0: json.dump(out, open(out_p, 'w')); print(f'  {k}/{len(ids)}', flush=True)
    json.dump(out, open(out_p, 'w')); print('done', len(out))

if __name__ == '__main__':
    main()
