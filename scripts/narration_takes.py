"""List every phrase of the user's narration recordings, numbered, to pick the clean takes from.

usage: python3 narration_takes.py <phrases.json> <recording> [<recording> ...]

Each recording is transcribed once (mlx-whisper large-v3-turbo, word timestamps), cached beside it as <name>.json.
Every transcript segment is split into phrases at pauses (> 0.25 s between words) and at punctuation; phrases.json gets
all of them, {i, file (index of the recording), start, end, text}, and the list is printed per recording with the
pause before each phrase, so false starts and retakes stand out.

The take of every phrase is then picked by hand into picks.py for narration_cut.py: the last clean take wins, the
subtitle is what was said (ASR slips fixed against the script). Matching phrases to the script automatically gave short
repeated phrases ("十年前", "起了个大早") to the wrong paragraph (Bali v7), so it is not done here.
"""
import json, os, re, sys

PUNCT = re.compile(r'[\s，。！？；：、,.!?;:"“”‘’（）()…—\-]')
norm = lambda s: PUNCT.sub('', s).lower()

def transcribe(rec):
    out = os.path.splitext(rec)[0] + '.json'
    if not os.path.exists(out):
        import mlx_whisper
        r = mlx_whisper.transcribe(rec, path_or_hf_repo='mlx-community/whisper-large-v3-turbo', language='zh', word_timestamps=True)
        json.dump(r, open(out, 'w'), ensure_ascii=False)
    return out

def phrases(rec_json, file_id):
    out = []
    for seg in json.load(open(rec_json))['segments']:
        cur = []
        for w in seg.get('words', []):
            if cur and (w['start'] - cur[-1]['end'] > 0.25 or re.search(r'[，。！？,.!?…]$', cur[-1]['word'].strip())):
                out.append(cur); cur = []
            cur.append(w)
        if cur: out.append(cur)
    return [{'file': file_id, 'start': p[0]['start'], 'end': p[-1]['end'], 'words': p,
             'text': ''.join(w['word'] for w in p).strip()} for p in out if norm(''.join(w['word'] for w in p))]

def main():
    out, recs = sys.argv[1], sys.argv[2:]
    ph = [x for k, r in enumerate(recs) for x in phrases(transcribe(r), k)]
    ph = [{'i': i, 'file': x['file'], 'start': round(x['start'], 2), 'end': round(x['end'], 2), 'text': x['text']} for i, x in enumerate(ph)]
    json.dump(ph, open(out, 'w'), ensure_ascii=False, indent=1)
    for k, r in enumerate(recs):
        print(os.path.basename(r)); prev = None
        for x in (p for p in ph if p['file'] == k):
            gap = f"+{x['start'] - prev:4.1f}" if prev is not None else '     '
            print(f"  {x['i']:4d}  {x['start']:7.2f} {gap}  {x['text']}"); prev = x['end']

if __name__ == '__main__':
    main()
