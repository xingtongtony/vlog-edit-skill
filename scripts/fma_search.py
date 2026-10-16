"""Search Free Music Archive and keep only tracks that are free for monetised, cross-platform use (CC0 or CC BY).

usage: python3 fma_search.py <out_dir> <query> [<query> ...] [--download]
       python3 fma_search.py <out_dir> --album <album page url> [--download]
Each query must be ONE word (FMA quicksearch returns nothing for "calm ukulele"; it matches titles / artists / tags).
Prints title | artist | licence | duration; with --download saves the mp3s and appends a row per track to
<out_dir>/LICENSES.md (CC BY tracks need the credit line in the video description).
Licence is read from each track's own page, never assumed from the album or artist.
"""
import html, json, os, re, subprocess, sys

UA = ['-A', 'Mozilla/5.0']
OK = {'publicdomain/zero': 'CC0', 'licenses/by': 'CC BY'}

def get(url): return subprocess.run(['curl', '-s', '-L'] + UA + [url], capture_output=True, text=True).stdout

def search(q):
    t = get(f'https://freemusicarchive.org/search?quicksearch={q.replace(" ", "+")}')
    return [json.loads(html.unescape(b)) for b in re.findall(r"data-track-info='([^']+)'", t)]

def licence(page):
    found = sorted(set('/'.join(x) for x in re.findall(r'creativecommons\.org/(licenses|publicdomain)/([a-z\-]+)/', page)))
    for f in found:
        if f in OK: return OK[f], found
    return None, found

def album(url):
    """Tracks listed on an album page (pass --album <album url> instead of queries)."""
    return [json.loads(html.unescape(b)) for b in re.findall(r"data-track-info='([^']+)'", get(url))]

def main():
    out = sys.argv[1]; dl = '--download' in sys.argv
    if '--album' in sys.argv:
        k = sys.argv.index('--album'); qs = [('album', sys.argv[k + 1])]
    else:
        qs = [('q', a) for a in sys.argv[2:] if not a.startswith('--')]
    os.makedirs(out, exist_ok=True); seen = set()
    for kind, q in qs:
        print(f'== {q}')
        for d in (album(q) if kind == 'album' else search(q)):
            u = d.get('url') or ''
            if not u or u in seen: continue
            seen.add(u); page = get(u); lic, all_ = licence(page)
            if not lic: continue
            dur = (re.findall(r'"duration":\s*"?([0-9:]+)', page) or ['?'])[0]
            print(f"  {lic:5s} | {d.get('title', '')[:38]:38s} | {d.get('artistName', '')[:22]:22s} | {dur} | {u}")
            own = [json.loads(html.unescape(b)) for b in re.findall(r"data-track-info='([^']+)'", page)]
            file_url = d.get('fileUrl') or next((x.get('fileUrl') for x in own if x.get('url') == u and x.get('fileUrl')), None)
            if dl and file_url:
                fn = re.sub(r'[^A-Za-z0-9_.-]+', '_', f"fma-{d.get('artistName', '')}-{d.get('title', '')}")[:90] + '.mp3'
                p = os.path.join(out, fn)
                if not os.path.exists(p):
                    subprocess.run(['curl', '-s', '-L'] + UA + ['-o', p, html.unescape(file_url)])
                    with open(os.path.join(out, 'LICENSES.md'), 'a') as fh:
                        fh.write(f"| {fn} | {d.get('title')} / {d.get('artistName')} | {u} | {lic}"
                                 f"{' (credit required)' if lic == 'CC BY' else ''} | {__import__('datetime').date.today()} |\n")

if __name__ == '__main__':
    main()
