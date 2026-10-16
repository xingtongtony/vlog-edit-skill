"""Narration script for the user to record against a rendered cut.

usage: python3 narration_script.py <edl.json> <render.mp4> <out.md> [<title>]
Reads the shot start times the renderer wrote (<render.mp4>.timeline.json). Narration (`vo`) lines are bold with a
suggested length; the user's own live lines (`subtitles`) are quotes; chapter cards become headings; music-free
stretches are noted so the user knows where their voice will sit on natural sound only.
"""
import json, os, sys

def tc(t): return f'{int(t // 60)}:{t % 60:04.1f}'

def main():
    edl_p, mp4, out = sys.argv[1:4]; title = sys.argv[4] if len(sys.argv) > 4 else '旁白稿'
    edl = json.load(open(edl_p)); tl = {r['shot']: r['at'] for r in json.load(open(mp4 + '.timeline.json'))}
    shots = edl['shots']; rows = []
    for s in edl['subtitles']:
        rows.append((tl[s['shot']] + s['from'] - shots[s['shot']]['in'], 1, f"> {s['text']}"))
    for v in edl['vo']:
        rows.append((tl[v['shot']] + v['at'], 1, f"**{v['text']}** （约 {v['dur']:.1f} 秒）"))
    for c in edl['cards']:
        if c.get('kind') == 'chapter':
            rows.append((tl[c['shot']], 0, f"\n## {c.get('kicker', '')} · {c['text']}" + (f"（{c['sub']}）" if c.get('sub') else '') + '\n'))
    for m in edl['music']:
        if m.get('out_why'):
            rows.append((tl.get(m['to_shot'], 0), 0, f"\n*— 音乐停：{m['out_why']} —*\n"))
    rows.sort(key=lambda r: (r[0], r[1]))
    with open(out, 'w') as f:
        f.write(f"# {title}\n\n对照成片 `{os.path.basename(mp4)}` 录音。**加粗**的是要你录的旁白（片子里先以字幕显示，背景音乐已经压低）；"
                "引用是片子里你自己的现场口播，不用录。\n\n录法：照稿一句一句读，说错了停一两秒从这句开头重来；手机录就行。"
                "每句后面是建议时长，读得比它长没关系，我会让画面跟着你的节奏。\n\n")
        for t, kind, text in rows:
            f.write(text + '\n' if kind == 0 else f"- `{tc(t)}` {text}\n")
    print(out, sum(1 for r in rows if r[2].startswith('**')), 'narration lines')

if __name__ == '__main__':
    main()
