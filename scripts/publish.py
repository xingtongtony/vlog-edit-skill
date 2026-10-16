"""Copy the reusable part of this workspace into the public repo and commit it.

usage: python3 scripts/publish.py [<public repo dir>] [--push]      (default dir: ~/Documents/vlog-edit-skill)

Only the ALLOW list goes out. projects/ never does: it holds contact sheets of people's faces, transcripts of private
conversations and local paths. The public repo has its own history, so nothing removed here can leak from old commits.
Before copying, every text file is scanned for local paths and personal identifiers; a hit stops the publish.
"""
import glob, os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = ['skills/vlog-edit/SKILL.md', 'docs/*.md', 'scripts/*.py', 'scripts/analysis/*.py',
         'fonts/*.ttf', 'fonts/licenses/*.txt']
DENY = ['scripts/render_preview_v1.py']                      # superseded; scripts/bohol-v1/ is not in ALLOW either
RENAME = {'publish/README.md': 'README.md', 'publish/gitignore': '.gitignore'}
def private_pattern():
    """Local paths, this machine's user name, the git e-mail, share links - read at run time, so this public file
    names nobody."""
    home = os.path.expanduser('~'); mail = subprocess.run(['git', 'config', 'user.email'], capture_output=True, text=True).stdout.strip()
    words = [home, os.path.basename(home), '/Users/', 'xhslink'] + ([mail, mail.split('@')[0]] if mail else [])
    return re.compile('|'.join(re.escape(w) for w in words if w), re.I)

def files():
    out = {}
    for pat in ALLOW:
        for p in glob.glob(os.path.join(ROOT, pat)):
            rel = os.path.relpath(p, ROOT)
            if rel not in DENY: out[rel] = rel
    out.update({k: v for k, v in RENAME.items() if os.path.exists(os.path.join(ROOT, k))})
    return out

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dst = os.path.expanduser(args[0] if args else '~/Documents/vlog-edit-skill')
    fs = files(); hits = []; PRIVATE = private_pattern()
    for src in fs:
        if src.endswith('.ttf') or src == 'scripts/publish.py': continue      # holds only the generic patterns
        for n, line in enumerate(open(os.path.join(ROOT, src), encoding='utf-8', errors='ignore'), 1):
            if PRIVATE.search(line): hits.append(f'{src}:{n}: {line.strip()[:90]}')
    if hits: sys.exit('private details found, nothing published:\n  ' + '\n  '.join(hits))
    os.makedirs(dst, exist_ok=True)
    if not os.path.isdir(os.path.join(dst, '.git')): subprocess.run(['git', 'init', '-q', '-b', 'main', dst], check=True)
    for name in os.listdir(dst):                             # mirror: remove what is no longer published
        if name != '.git': (shutil.rmtree if os.path.isdir(os.path.join(dst, name)) else os.remove)(os.path.join(dst, name))
    for src, rel in fs.items():
        os.makedirs(os.path.dirname(os.path.join(dst, rel)) or dst, exist_ok=True)
        shutil.copy2(os.path.join(ROOT, src), os.path.join(dst, rel))
    g = lambda *a: subprocess.run(['git', '-C', dst, *a], capture_output=True, text=True)
    g('add', '-A')
    if not g('status', '--porcelain').stdout.strip(): print('nothing changed'); return
    src_head = subprocess.run(['git', '-C', ROOT, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    msg = f'Sync from workspace {src_head}' + '\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
    print(g('commit', '-q', '-m', msg).stderr or f'committed {len(fs)} files to {dst}')
    if '--push' in sys.argv:
        r = g('push', '-u', 'origin', 'main'); print(r.stdout + r.stderr)

if __name__ == '__main__':
    main()
