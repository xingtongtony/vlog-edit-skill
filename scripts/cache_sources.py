"""Copy the source clips an EDL uses from an external drive to a local folder, so a render does not depend on the
drive staying mounted (Bali 2026-10-24: the USB drive dropped off three times in ten minutes and killed two renders).

usage: python3 cache_sources.py <edl.json> <cache dir>      then  render_preview.py ... --sources <cache dir>
Copies with rsync --partial (re-run to resume after the drive drops); only what the EDL uses: clips of shots, beds and
news backgrounds, and photos (render_preview looks every one of them up in the cache by file name; Bali v7 still read
photos from the drive and stopped when it was out).
"""
import json, os, subprocess, sys

def main():
    edl = json.load(open(sys.argv[1])); dst = os.path.expanduser(sys.argv[2]); os.makedirs(dst, exist_ok=True)
    clips = {c['id']: c for c in json.load(open(os.path.join(edl['project'], 'ingest', 'clips.json')))}
    used = ({s['clip'] for s in edl['shots'] if s['clip'] in clips} | {s['bed']['clip'] for s in edl['shots'] if s.get('bed')}
            | {s['news']['bg'][0] for s in edl['shots'] if (s.get('news') or {}).get('bg')})
    paths = sorted({clips[c]['path'] for c in used if c in clips} | {s['photo'] for s in edl['shots'] if s.get('photo')})
    names = [os.path.basename(p) for p in paths]
    if len(set(names)) != len(names): sys.exit('two used files share a file name; the cache keys on the name')
    todo = [p for p in paths if not (os.path.exists(os.path.join(dst, os.path.basename(p)))
                                      and os.path.getsize(os.path.join(dst, os.path.basename(p))) == os.path.getsize(p))]
    print(f'{len(paths)} files, {len(todo)} to copy, {sum(os.path.getsize(p) for p in todo) / 1e9:.1f} GB', flush=True)
    for k, p in enumerate(todo, 1):
        subprocess.run(['rsync', '-a', '--partial', p, dst + '/'], check=True)
        if k % 10 == 0 or k == len(todo): print(f'  {k}/{len(todo)}', flush=True)

if __name__ == '__main__':
    main()
