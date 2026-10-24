"""Transcribe every clip that has audio, with word timestamps; drop whisper hallucinations.

usage: python3 transcribe.py <project_dir> [initial prompt with place names] [--min SECONDS]
  --min  skip clips shorter than this (Live Photo clips are 2-3 s each and cost ~7 s of model overhead apiece;
         transcribe the real clips first, the rest in a second run without --min)
Reads ingest/clips.json and ingest/audio/<id>.wav, writes ingest/transcripts/<id>.json
(segments with words) and ingest/transcripts.md (human-readable, one block per clip).
Run with system python3 (mlx-whisper lives in the user site; do not pass -I).
"""
import json, os, re, sys
# mlx_whisper is imported where it is used, so edl_builder can import hallucination() without loading the model library

MODEL = 'mlx-community/whisper-large-v3-turbo'

# Phrases whisper invents over silence, wind, bubbles or music (subtitle-credit boilerplate it was trained on)
BLACKLIST = ('请不吝点赞', '订阅', '明镜', '点点栏目', '字幕', '作词', '作曲', '李宗盛', '优优独播', 'YoYo Television',
             'ご視聴', '감사합니다', 'Субтитры', 'DimaTorzok', '谢谢大家', '谢谢观看', 'Thank you', 'Thanks for watching', '音乐')

def hallucination(seg):
    t = seg['text'].strip()
    if not t: return True
    if any(b.lower() in t.lower() for b in BLACKLIST) and len(t) < 30: return True
    if re.search(r'优优独播|YoYo Television|字幕由|请不吝点赞', t, re.I): return True     # long credit lines over pure music
    if seg.get('no_speech_prob', 0) > 0.6 and seg.get('avg_logprob', 0) < -0.8: return True
    if seg.get('compression_ratio', 0) > 2.4: return True                # "pertpertpert…", "蛮蛮蛮…"
    if re.fullmatch(r'(.{1,4}?)\1{4,}', re.sub(r'\W', '', t)): return True  # short token repeated
    if re.fullmatch(r'((sh+|s+h+|hm+|mm+|uh+|ah+|oh+|hh+)[.!?,\s]*)+', t, re.I): return True   # regulator breathing heard as "Shhh."
    return False

def fmt(s): return f'{int(s // 60)}:{s % 60:04.1f}'

def main():
    args = sys.argv[1:]; min_dur = 0.0
    if '--min' in args:
        k = args.index('--min'); min_dur = float(args[k + 1]); del args[k:k + 2]
    project = args[0]
    prompt = args[1] if len(args) > 1 else '以下是普通话的句子。'
    ing = os.path.join(project, 'ingest'); out = os.path.join(ing, 'transcripts'); os.makedirs(out, exist_ok=True)
    clips = json.load(open(f'{ing}/clips.json'))
    md = []
    for k, c in enumerate(clips, 1):
        wav = f'{ing}/audio/{c["id"]}.wav'; dst = f'{out}/{c["id"]}.json'
        if not c['audio'] or not os.path.exists(wav) or c['duration'] < min_dur: continue
        if not os.path.exists(dst):
            import mlx_whisper
            r = mlx_whisper.transcribe(wav, path_or_hf_repo=MODEL, initial_prompt=prompt, word_timestamps=True,
                                       condition_on_previous_text=False)
            segs = [{'start': s['start'], 'end': s['end'], 'text': s['text'].strip(),
                     'words': [{'w': w['word'], 's': w['start'], 'e': w['end']} for w in s.get('words', [])]}
                    for s in r['segments'] if not hallucination(s)]
            json.dump({'language': r.get('language'), 'segments': segs}, open(dst, 'w'), ensure_ascii=False)
        segs = [s for s in json.load(open(dst))['segments'] if not hallucination(s)]
        md.append(f'## {c["id"]}  {c["name"]}  {c["time"]}  {c["duration"]:.0f}s\n' +
                  ('\n'.join(f'- {fmt(s["start"])} {s["text"]}' for s in segs) or '- （无人声）'))
        if k % 20 == 0: print(f'  transcribed {k}/{len(clips)}', flush=True)
    open(f'{ing}/transcripts.md', 'w').write('\n\n'.join(md) + '\n')
    print('done', flush=True)

if __name__ == '__main__':
    main()
