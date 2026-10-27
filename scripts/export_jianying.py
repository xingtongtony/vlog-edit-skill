"""Export an EDL as a Jianying (剪映专业版, Mac) draft that looks and sounds like the rendered cut, and stays editable.

usage: python3 export_jianying.py <edl.json> "<draft name>" [--replace] [--remove "<old draft name>"] [--workers 6]
--remove: once Jianying has quit, the old version's draft goes to the Trash and leaves the draft list (each cut
replaces the previous one; its media folder is cleared by hand - see SKILL.md).
Needs pip3 install pyJianYingDraft. Quit Jianying first (⌘Q): it locks an open draft and rewrites the draft list
(root_meta_info.json) on quit.

Jianying cannot do what the renderer does, so everything Jianying has no equivalent for is made here first:
  画面          each shot pre-graded (film look + per-clip underwater correction) with 2 s handles on both sides, so the
                cut point can still be moved in Jianying; dissolves are Jianying 叠化
  照片          the plain photo fitted on black (no backdrop / border, the user's choice) with a 4.5 % push-in keyframe, 闪白 into it, shutter
                sound on the 音效 track. Screenshot cards (news, hand-drawn marks), photos seen through a moving window (mask) and
                photos laid over a video shot (layers) are drawn by the renderer's own code and placed as finished clips
  漏光          the procedural light leak, pre-multiplied on black, on its own track in 滤色 (screen) blend mode
  字幕/标题      the exact PNGs the renderer burns in (size, translucent box, round corners, fonts, positions), with the
                same fades. An SRT of all lines is written next to the media: 文本 → 导入字幕 gives editable text
  音乐          each song's used part levelled like the renderer (all songs to one loudness) and written as a WAV, so
                Jianying volumes stay <= 100 %; ducking under speech / narration as volume keyframes
Sandbox: the Mac App Store Jianying reads ~/Movies but not the Desktop / Documents ("暂无访问权限"), so all media live in
~/Movies/JianyingPro/Claude素材/<project>/.
Still different from the render: no master loudness normalisation (use Jianying's own when exporting).
"""
import json, os, shutil, subprocess, sys, time, uuid
from concurrent.futures import ThreadPoolExecutor
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pyJianYingDraft as jy
from pyJianYingDraft import TrackType, KeyframeProperty, TransitionType, MixModeType
import grade
from render_preview import (text_spans, vo_duration, subtitle_png, card_png, typewriter_png, compose_photo, make_leak, shutter,
                            render_still, render_layers, shot_beats, AUDIO_GAIN, AMBIENT_NO_MUSIC, DUCK, MUSIC_RMS, SR)

ROOT = os.path.expanduser('~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft')
MEDIA = os.path.expanduser('~/Movies/JianyingPro/Claude素材')
FF = '/opt/homebrew/bin/ffmpeg'
US = 1_000_000
HANDLE = 2.0

def us(t): return int(round(t * US))

def run(cmd): subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def song_level(path, start, dur):
    """Same levelling as render_preview: the louder half of the used part brought to MUSIC_RMS."""
    raw = subprocess.run([FF, '-v', 'error', '-ss', f'{start:.3f}', '-t', f'{dur:.3f}', '-i', path, '-ac', '1', '-ar', '16000',
                          '-f', 's16le', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    blk = np.sqrt(np.mean(x[: len(x) // 1600 * 1600].reshape(-1, 1600) ** 2, axis=1))
    return MUSIC_RMS / (np.sqrt(np.mean(np.sort(blk)[len(blk) // 2:] ** 2)) + 1e-9)

def fade_keys(seg, d, fi, fo):
    """Opacity fades as keyframes (image overlays have no fade setting)."""
    for t, v in [(0, 0.0), (min(fi, d / 3), 1.0), (max(d - fo, d * 2 / 3), 1.0), (d, 0.0)]:
        seg.add_keyframe(KeyframeProperty.alpha, us(t), v)

def srt_time(t): h, r = divmod(t, 3600); m, s = divmod(r, 60); return f'{int(h):02d}:{int(m):02d}:{s:06.3f}'.replace('.', ',')

def main():
    edl_p, name = sys.argv[1], sys.argv[2]
    workers = int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else 6
    edl = json.load(open(edl_p)); fps = edl.get('fps', 30); W, H = edl.get('size', [1920, 1080])
    proj = edl['project']; sub = os.path.basename(os.path.normpath(proj))
    clips = {c['id']: c for c in json.load(open(os.path.join(proj, 'ingest', 'clips.json')))}
    shots = edl['shots']
    starts, t = [], 0
    for s in shots: starts.append(t); t += round((s['out'] - s['in']) * fps)
    tl = [x / fps for x in starts]; total = t / fps; ends = tl[1:] + [total]
    music_on = [False] * len(shots)
    for m in edl.get('music', []):
        for k in range(m.get('from_shot', 0), m['to_shot'] if m.get('to_shot') is not None else len(shots)): music_on[k] = True
    out = lambda *p: os.path.join(MEDIA, sub, *p)
    for d in ('graded', 'overlays', 'music', 'audio', 'photos'): os.makedirs(out(d), exist_ok=True)

    # ---- 1. graded shots with handles (same per-clip grade as the renderer)
    look = edl.get('look', 'film'); cg = {}
    for cid in sorted({s['clip'] for s in shots if not s.get('photo')}):
        uses = [s for s in shots if s['clip'] == cid]
        ts = [x for s in uses for x in np.linspace(s['in'] + 0.1, max(s['in'] + 0.1, s['out'] - 0.1), 3)]
        modes = {s.get('grade', 'auto') for s in uses}
        cg[cid] = grade.clip_filter(clips[cid]['path'], ts, mode=modes.pop() if len(modes) == 1 else 'auto', look=look)
    jobs = []
    for i, s in enumerate(shots):
        if s.get('photo'): continue
        c = clips[s['clip']]; a = max(0.0, s['in'] - HANDLE); b = min(c['duration'], s['out'] + HANDLE)
        jobs.append((i, a, b, out('graded', f"s{i:03d}_{s['clip']}_{s['in']:.2f}_{s['out']:.2f}.mp4")))
    def grade_one(j):
        i, a, b, dst = j; c = clips[shots[i]['clip']]
        if not os.path.exists(dst):
            g = cg[shots[i]['clip']]
            vf = f'scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1' + (',' + g if g else '')
            run([FF, '-y', '-hwaccel', 'videotoolbox', '-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', c['path'], '-vf', vf, '-r', str(fps),
                 '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', '-pix_fmt', 'yuv420p']
                + (['-c:a', 'aac', '-b:a', '256k'] if c['audio'] else ['-an']) + ['-f', 'mp4', dst + '.tmp'])
            os.replace(dst + '.tmp', dst)
        return i, a, dst
    print(f'grading {len(jobs)} shots ...', flush=True)
    with ThreadPoolExecutor(workers) as ex: graded = {i: (a, dst) for i, a, dst in ex.map(grade_one, jobs)}

    while subprocess.run(['pgrep', '-f', 'Contents/MacOS/VideoFusion-macOS$'], capture_output=True).stdout.strip():
        print('waiting for Jianying to quit (⌘Q) before writing the draft ...', flush=True); time.sleep(10)
    if '--remove' in sys.argv: remove_draft(sys.argv[sys.argv.index('--remove') + 1])
    folder = jy.DraftFolder(ROOT)
    script = folder.create_draft(name, W, H, fps=fps, allow_replace='--replace' in sys.argv)
    script.append_tracks([jy.TrackSpec(TrackType.video, '画面'), jy.TrackSpec(TrackType.video, '漏光'),
                          jy.TrackSpec(TrackType.video, '标题'), jy.TrackSpec(TrackType.video, '字幕'),
                          jy.TrackSpec(TrackType.audio, '音乐'), jy.TrackSpec(TrackType.audio, '环境声'),
                          jy.TrackSpec(TrackType.audio, '音效')])

    # ---- 2. picture track
    sh_wav = out('audio', 'shutter.wav')
    if not os.path.exists(sh_wav):
        from scipy.io import wavfile; wavfile.write(sh_wav, SR, (shutter(SR) * 32767).astype(np.int16))
    segs = []
    beats_db = os.path.join(proj, 'ingest', 'beats.json')
    for i, s in enumerate(shots):
        a, d = us(tl[i]), us(ends[i]) - us(tl[i])
        if s.get('photo') and (s.get('news') or s.get('mask')):     # drawn frame by frame: Jianying gets the finished clip
            mp4 = out('photos', f"s{i:03d}_{s['clip']}_{'card' if s.get('news') else 'mask'}.mp4")
            if not os.path.exists(mp4):
                render_still(s, W, H, fps, 0.0, ends[i] - tl[i], mp4, out('photos'), clips,
                             shot_beats(edl, tl + [total], i, beats_db) if s.get('mask') else None)
            seg = jy.VideoSegment(mp4, jy.Timerange(a, d), source_timerange=jy.Timerange(0, d), volume=0.0)
            if s.get('mask'): script.add_segment(jy.AudioSegment(sh_wav, jy.Timerange(a, us(0.16)), volume=1.0), '音效')
        elif s.get('layers'):                                          # photos laid over the graded shot, baked in
            mp4 = out('graded', f"s{i:03d}_{s['clip']}_{s['in']:.2f}_layers.mp4")
            if not os.path.exists(mp4):
                mov = mp4[:-4] + '.mov'; render_layers(s, W, H, fps, round((ends[i] - tl[i]) * fps), mov); ga, gp = graded[i]
                soft = f",gblur=sigma={s['blur'] * H / 1080:.1f}" if s.get('blur') else ''
                run([FF, '-y', '-ss', f"{s['in'] - ga:.3f}", '-t', f'{ends[i] - tl[i]:.3f}', '-i', gp, '-i', mov, '-filter_complex',
                     f'[0:v]setpts=PTS-STARTPTS{soft}[b];[1:v]format=rgba[o];[b][o]overlay=0:0:eof_action=pass,format=yuv420p[v]',
                     '-map', '[v]', '-map', '0:a?', '-c:a', 'aac', '-b:a', '256k', '-c:v', 'libx264', '-preset', 'medium', '-crf', '17', mp4])
                os.remove(mov)
            g = AUDIO_GAIN.get(s.get('audio', 'ambient'), 0.12) if clips[s['clip']]['audio'] else 0.0
            if s.get('audio', 'ambient') == 'ambient' and not music_on[i] and g: g = AMBIENT_NO_MUSIC
            seg = jy.VideoSegment(mp4, jy.Timerange(a, d), source_timerange=jy.Timerange(0, d), volume=g)
            for L in s['layers']:
                if L.get('photo'): script.add_segment(jy.AudioSegment(sh_wav, jy.Timerange(a + us(L['at']), us(0.16)), volume=1.0), '音效')
        elif s.get('photo'):
            png = out('photos', os.path.splitext(os.path.basename(s['photo']))[0] + ('_cap' if s.get('caption') else '') + '_plain.jpg')
            if not os.path.exists(png): compose_photo(s['photo'], W, H, png, s.get('caption'))
            seg = jy.VideoSegment(png, jy.Timerange(a, d), volume=0.0)
            seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.0); seg.add_keyframe(KeyframeProperty.uniform_scale, d, 1.045)
            script.add_segment(jy.AudioSegment(sh_wav, jy.Timerange(a, us(0.16)), volume=1.0), '音效')
        else:
            g = AUDIO_GAIN.get(s.get('audio', 'ambient'), 0.12)
            if s.get('audio', 'ambient') == 'ambient' and not music_on[i]: g = AMBIENT_NO_MUSIC
            if not clips[s['clip']]['audio']: g = 0.0
            ga, gp = graded[i]
            seg = jy.VideoSegment(gp, jy.Timerange(a, d), source_timerange=jy.Timerange(us(s['in'] - ga), d), volume=g)
        segs.append(seg)
        if s.get('bed'):
            bw = out('audio', f"bed_{s['bed']['clip']}_{s['bed']['in']:.1f}.wav")
            if not os.path.exists(bw):
                run([FF, '-y', '-ss', f"{s['bed']['in']:.3f}", '-t', f'{d / US + 0.5:.3f}', '-i', clips[s['bed']['clip']]['path'],
                     '-vn', '-ac', '2', '-ar', str(SR), '-af', 'loudnorm=I=-30:TP=-6', bw])
            bed = jy.AudioSegment(bw, jy.Timerange(a, d), source_timerange=jy.Timerange(0, d), volume=1.0)
            bed.add_fade('0.3s', '0.3s'); script.add_segment(bed, '环境声')
    for i, seg in enumerate(segs):
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        if nxt and nxt.get('photo') and not nxt.get('news') and not nxt.get('mask'): seg.add_transition(TransitionType.闪白, duration=us(0.2))
        elif nxt and nxt.get('xfade'): seg.add_transition(TransitionType.叠化, duration=us(min(nxt['xfade'], 1.0)))
        script.add_segment(seg, '画面')

    # ---- 3. light leaks: pre-multiplied on black, screen blend
    for k, fx in enumerate(f for f in edl.get('fx', []) if f['type'] == 'leak'):
        d = fx.get('dur', 0.9); a = tl[fx['cut']] - d / 2; p = out('overlays', f'leak{k}.mp4')
        if not os.path.exists(p):
            mov = p + '.mov'; make_leak(mov, d, fps, seed=k, warmth=fx.get('warmth', 1.0))
            run([FF, '-y', '-i', mov, '-filter_complex',
                 f'color=black:s=480x270:r={fps}[bg];[bg][0:v]overlay=shortest=1,scale={W}:{H},gblur=sigma={round(28 * H / 1080)},format=yuv420p',
                 '-c:v', 'libx264', '-crf', '16', p]); os.remove(mov)
        seg = jy.VideoSegment(p, jy.Timerange(us(a), us(d)), volume=0.0); seg.set_mix_mode(MixModeType.滤色)
        script.add_segment(seg, '漏光')

    # ---- 4. subtitles / narration / cards / stamps: the renderer's own PNGs
    srt = []
    for k, (a, b, text) in enumerate(text_spans(edl, tl, shots)):
        p = out('overlays', f'sub{k:03d}.png'); subtitle_png(text, W, H, p)
        seg = jy.VideoSegment(p, jy.Timerange(us(a), us(b) - us(a)), volume=0.0); fade_keys(seg, b - a, 0.08, 0.08)
        script.add_segment(seg, '字幕'); srt.append(f'{k + 1}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n')
    items = []
    for k, c in enumerate(edl.get('cards', [])):
        p = out('overlays', f'card{k:02d}.png'); card_png(c, W, H, p)
        items.append((tl[c['shot']] + c.get('at', 0), c['dur'], p, 0.35 if c.get('kind') != 'label' else 0.25))
    for k, tw in enumerate(edl.get('typewriter', [])):
        p = out('overlays', f'stamp{k:02d}.png'); typewriter_png(tw['text'], tw.get('sub'), W, H, p, y=tw.get('y', 0.40))
        items.append((tl[tw['shot']] + tw.get('at', 0), tw['dur'], p, 0.3))
    layer_end, made = [], {'标题'}
    for a, d, p, fd in sorted(items):
        li = next((j for j, e in enumerate(layer_end) if e <= a), None)
        if li is None: layer_end.append(0.0); li = len(layer_end) - 1
        layer_end[li] = a + d; tr = '标题' if li == 0 else f'标题{li + 1}'
        if tr not in made: script.append_track(jy.TrackSpec(TrackType.video, tr)); made.add(tr)
        seg = jy.VideoSegment(p, jy.Timerange(us(a), us(d)), volume=0.0); fade_keys(seg, d, fd, fd); script.add_segment(seg, tr)
    open(out('字幕.srt'), 'w').write('\n'.join(srt))

    # ---- 5. music: levelled WAVs, fades, ducking keyframes (all <= 100 %)
    spans = [(tl[i], ends[i], 10 ** (-18 / 20)) for i, s in enumerate(shots) if s.get('audio') == 'live']
    spans += [(tl[v['shot']] + v.get('at', 0) - 0.25, tl[v['shot']] + v.get('at', 0) + v.get('dur', vo_duration(v['text'])) + 0.35, DUCK)
              for v in edl.get('vo', [])]
    for k, m in enumerate(edl.get('music', [])):
        a = tl[m.get('from_shot', 0)] + m.get('at_offset', 0)
        b = tl[m['to_shot']] if m.get('to_shot') is not None and m['to_shot'] < len(tl) else total
        if b - a < 1: continue
        gain = song_level(m['path'], m.get('start', 0), b - a) * m.get('gain', 0.55)
        wav = out('music', f"{k:02d}_{os.path.splitext(os.path.basename(m['path']))[0]}.wav")
        run([FF, '-y', '-ss', f"{m.get('start', 0):.3f}", '-t', f'{b - a + 1:.3f}', '-i', m['path'], '-ac', '2', '-ar', str(SR),
             '-af', f'volume={gain:.4f},alimiter=limit=0.95:level=false', wav])
        seg = jy.AudioSegment(wav, jy.Timerange(us(a), us(b - a)), source_timerange=jy.Timerange(0, us(b - a)), volume=1.0)
        seg.add_fade(us(min(m.get('fade_in', 0.8), (b - a) / 3)), us(min(m.get('fade_out', 1.5), (b - a) / 3)))
        def level(x):
            f = 1.0
            for sa, sb, df in spans:
                f = min(f, 1 - (1 - df) * max(0.0, min(1.0, (x - (sa - 0.3)) / 0.3, ((sb + 0.3) - x) / 0.3)))
            return f
        pts = {a, b}
        for sa, sb, _ in spans:
            for x in (sa - 0.3, sa, sb, sb + 0.3):
                if a < x < b: pts.add(x)
        for x in sorted(pts): seg.add_keyframe(us(x - a), float(level(x)))
        script.add_segment(seg, '音乐')
    script.save()
    register(name)
    print('media in', out())

def remove_draft(name):
    """Old version of the cut: draft folder to the Trash (recoverable), entry out of root_meta_info.json. Only while
    Jianying is closed - it rewrites the draft list on quit."""
    p = os.path.join(ROOT, name)
    if os.path.isdir(p):
        dst = os.path.join(os.path.expanduser('~/.Trash'), f'{name} {time.strftime("%H.%M.%S")}')
        shutil.move(p, dst); print('old draft moved to the Trash:', dst)
    rp = os.path.join(ROOT, 'root_meta_info.json'); root = json.load(open(rp))
    keep = [x for x in root['all_draft_store'] if x.get('draft_name') != name]
    if len(keep) != len(root['all_draft_store']):
        shutil.copy(rp, rp + f'.bak-{int(time.time())}')
        root['all_draft_store'] = keep; root['draft_ids'] = len(keep)
        json.dump(root, open(rp, 'w'), ensure_ascii=False); print('old draft removed from the draft list:', name)

def register(name):
    """Put the draft into Jianying's draft list (root_meta_info.json), the way the app lists its own drafts."""
    p = os.path.join(ROOT, name); meta = json.load(open(os.path.join(p, 'draft_meta_info.json')))
    shutil.copy(os.path.join(p, 'draft_content.json'), os.path.join(p, 'draft_info.json'))   # the Mac app reads draft_info.json
    rp = os.path.join(ROOT, 'root_meta_info.json'); root = json.load(open(rp))
    shutil.copy(rp, rp + f'.bak-{int(time.time())}')
    root['all_draft_store'] = [x for x in root['all_draft_store'] if x.get('draft_name') != name]
    now = int(time.time() * 1e6); tpl = dict(root['all_draft_store'][0]) if root['all_draft_store'] else {}
    tpl.update({'draft_cover': '', 'draft_fold_path': p, 'draft_id': meta.get('draft_id') or str(uuid.uuid4()).upper(),
                'draft_json_file': os.path.join(p, 'draft_info.json'), 'draft_name': name, 'draft_root_path': ROOT,
                'tm_draft_create': now, 'tm_draft_modified': now, 'tm_draft_removed': 0,
                'tm_duration': json.load(open(os.path.join(p, 'draft_content.json'))).get('duration', 0),
                'draft_timeline_materials_size': 0, 'streaming_edit_draft_ready': True})
    root['all_draft_store'].insert(0, tpl); root['draft_ids'] = len(root['all_draft_store'])
    json.dump(root, open(rp, 'w'), ensure_ascii=False)
    print('draft', p, 'registered')

if __name__ == '__main__':
    main()
