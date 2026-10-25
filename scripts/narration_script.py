"""Narration script for the user to record against a rendered cut.

usage: python3 narration_script.py <edl.json> <render.mp4> <out.md> [<title>]
Reads the shot start times the renderer wrote (<render.mp4>.timeline.json). Narration (`vo`) lines are bold with a
suggested length; the user's own live lines (`subtitles`) are quotes; chapter cards become headings; music-free
stretches are noted so the user knows where their voice will sit on natural sound only; no-narration moments
(`quiet`) are listed so the user sees where the film is meant to stay silent.
"""
import json, os, sys

def tc(t): return f'{int(t // 60)}:{t % 60:04.1f}'

def main():
    edl_p, mp4, out = sys.argv[1:4]; title = sys.argv[4] if len(sys.argv) > 4 else '旁白稿'
    edl = json.load(open(edl_p)); tl = {r['shot']: r['at'] for r in json.load(open(mp4 + '.timeline.json'))}
    shots = edl['shots']; rows = []
    for s in edl['subtitles']:
        rows.append((tl[s['shot']] + s['from'] - shots[s['shot']]['in'], 1, f"> {s['text']}"))
    paras = {}                                  # paragraph id -> (start, paragraph text, end of speech)
    for v in edl['vo']:
        a = tl[v['shot']] + v['at']
        if 'para' not in v:                     # EDLs from before paragraph narration: one row per line
            rows.append((a, 1, f"**{v['text']}** （约 {v['dur']:.1f} 秒）")); continue
        s, text, _ = paras.get(v['para'], (a, v.get('para_text', v['text']), a))
        paras[v['para']] = (s, text, a + v.get('speech', v['dur']))
    for s, text, e in paras.values():
        rows.append((s, 1, f"**{text}** （一口气读完，约 {e - s:.0f} 秒）"))
    for c in edl['cards']:
        if c.get('kind') == 'chapter':
            rows.append((tl[c['shot']], 0, f"\n## {c.get('kicker', '')} · {c['text']}" + (f"（{c['sub']}）" if c.get('sub') else '') + '\n'))
    for q in edl.get('quiet', []):
        rows.append((tl[q['shot']] + q.get('at', 0), 0, f"\n*— 不配音 {q['dur']:.0f} 秒：{q['why']} —*\n"))
    for m in edl['music']:
        if m.get('out_why'):
            rows.append((tl.get(m['to_shot'], 0), 0, f"\n*— 音乐停：{m['out_why']} —*\n"))
    rows.sort(key=lambda r: (r[0], r[1]))
    with open(out, 'w') as f:
        f.write(f"# {title}\n\n对照成片 `{os.path.basename(mp4)}` 录音。**加粗**的是要你录的旁白（片子里先以字幕显示，背景音乐已经压低）；"
                "引用是片子里你自己的现场口播，不用录。\n\n录法：一段一个录音，看着这份稿子一口气读完，句子之间正常换气就行，"
                "不用等画面上的字幕；说错了停一两秒，从这一句开头重来。手机录就行。"
                "每段后面是按正常语速估的时长，读得快慢都没关系，我会按你的录音重新对齐字幕和画面。\n\n")
        for t, kind, text in rows:
            f.write(text + '\n' if kind == 0 else f"- `{tc(t)}` {text}\n")
    print(out, sum(1 for r in rows if r[2].startswith('**')), 'narration lines')

if __name__ == '__main__':
    main()
