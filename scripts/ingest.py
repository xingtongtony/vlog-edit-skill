"""Stage 1 of the vlog workflow: inventory, proxies, contact sheets and transcripts.

usage: python3 ingest.py <project_dir> <footage_dir> [<footage_dir> ...]

Writes into <project_dir>/ingest/:
  clips.json            one record per clip (path, kind, shot time, duration, size, audio)
  proxy/<id>.mp4        360p H.264 proxy (fast to analyse and to cut previews from)
  sheets/<id>.jpg       contact sheet, one frame every 4 s
  audio/<id>.wav        16 kHz mono audio for transcription
Transcription is a separate step (transcribe.py) so it can run while proxies finish.
"""
import json, os, re, subprocess, sys, datetime
from concurrent.futures import ThreadPoolExecutor

FF = '/opt/homebrew/bin/ffmpeg'; FP = '/opt/homebrew/bin/ffprobe'
EXT = ('.mp4', '.mov')

def probe(path):
    r = json.loads(subprocess.run([FP, '-v', 'error', '-show_entries',
        'format=duration:format_tags=creation_time,com.apple.quicktime.creationdate:stream=codec_type,width,height,r_frame_rate:stream_tags=rotate',
        '-of', 'json', path], capture_output=True, text=True).stdout or '{}')
    v = [s for s in r.get('streams', []) if s.get('codec_type') == 'video']
    tags = r.get('format', {}).get('tags', {})
    return {
        'duration': float(r.get('format', {}).get('duration', 0) or 0),
        'width': v[0].get('width', 0) if v else 0, 'height': v[0].get('height', 0) if v else 0,
        'fps': v[0].get('r_frame_rate', '') if v else '',
        'audio': any(s.get('codec_type') == 'audio' for s in r.get('streams', [])),
        'creation': tags.get('com.apple.quicktime.creationdate') or tags.get('creation_time', ''),
    }

def shot_time(name, creation):
    """Best guess of local shooting time: filename stamp first (camera clock), then metadata."""
    m = re.search(r'(20\d{6})_?(\d{6})', name)
    if m: return datetime.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S').isoformat()
    if creation:
        try: return datetime.datetime.fromisoformat(creation.replace('Z', '+00:00')).isoformat()
        except ValueError: pass
    return ''

def mtime_time(path):
    """iPhone exports (IMG_*.MOV) often carry no creation date; their file mtime matches the shooting time
    (checked against neighbouring photo numbers). Shared copies from other people do NOT - don't use it for them."""
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='seconds')

def kind(name, info):
    if name.startswith('DJI_') and not info['audio']: return 'drone'
    if name.startswith('VID_'): return 'actioncam'
    if name.startswith('IMG_'): return 'phone'
    return 'other'

def run(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def make(clip, out):
    cid, src = clip['id'], clip['path']
    proxy, sheet, wav = f'{out}/proxy/{cid}.mp4', f'{out}/sheets/{cid}.jpg', f'{out}/audio/{cid}.wav'
    if not os.path.exists(proxy):
        run([FF, '-y', '-hwaccel', 'videotoolbox', '-i', src, '-vf', 'scale=-2:360', '-c:v', 'h264_videotoolbox', '-b:v', '900k',
             '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', proxy])
    if not os.path.exists(sheet):
        n = max(1, int(clip['duration'] // 4)); cols = 6; rows = max(1, -(-n // cols))
        run([FF, '-y', '-i', proxy, '-vf', f'fps=1/4,scale=240:-2,tile={cols}x{rows}:padding=2', '-frames:v', '1', sheet])
    if clip['audio'] and not os.path.exists(wav):
        run([FF, '-y', '-i', proxy, '-vn', '-ac', '1', '-ar', '16000', wav])
    return cid

def main():
    project, dirs = sys.argv[1], sys.argv[2:]
    out = os.path.join(project, 'ingest')
    for d in ('proxy', 'sheets', 'audio'): os.makedirs(f'{out}/{d}', exist_ok=True)
    clips = []
    for d in dirs:
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(EXT) or f.startswith('LRV_'): continue
            if '_剪辑_' in f or f.startswith('render_'): continue   # our own renders, not footage
            p = os.path.join(d, f); info = probe(p)
            if info['duration'] <= 0: continue
            t = shot_time(f, info['creation']) or (mtime_time(p) if f.startswith('IMG_') else '')
            clips.append({'path': p, 'name': f, **info, 'time': t, 'kind': kind(f, info)})
    clips.sort(key=lambda c: (c['time'] or '9999', c['name']))
    for i, c in enumerate(clips): c['id'] = f'c{i + 1:03d}'
    json.dump(clips, open(f'{out}/clips.json', 'w'), ensure_ascii=False, indent=1)
    print(f'{len(clips)} clips, {sum(c["duration"] for c in clips) / 60:.1f} min', flush=True)
    with ThreadPoolExecutor(4) as ex:
        for k, cid in enumerate(ex.map(lambda c: make(c, out), clips), 1):
            if k % 20 == 0 or k == len(clips): print(f'  proxies {k}/{len(clips)}', flush=True)

if __name__ == '__main__':
    main()
