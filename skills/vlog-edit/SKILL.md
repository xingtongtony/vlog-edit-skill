---
name: vlog-edit
description: Edit the user's travel vlogs (drone + action-cam + phone footage, 6–15 min landscape, Chinese) from raw footage to a reviewed cut — ingest, story, outline, beat-synced previews with subtitles and music, self-verified renders. Use whenever the user asks to cut, edit, re-cut or preview a vlog or a part of one ("剪一下开头", "帮我剪 vlog", "改一下这个镜头"), or to pick music, write narration or subtitles for one.
---

# vlog-edit

Workspace: `~/Documents/vlog-editing/` (git). Read before any edit:
- `docs/storytelling.md` — narration, structure, pacing, subtitle spec, music states. **No 流水账.**
- `docs/toolchain.md` — installed tools and every pitfall hit so far.
- `docs/music-sources.md` — free music sources and licenses.
- `docs/footage-sources.md` — free overlay / B-roll footage, and which transitions are generated in code instead.
- `docs/workflow.md` — the stages agreed with the user.

Talk to the user in Chinese. Product text in videos stays as the user writes it.

## Project layout

`projects/<trip>/` per trip: `ingest/` (clips.json, proxy/, sheets/, audio/, transcripts/), `edl/*.json`, `music/` (+ `LICENSES.md`), `render/`. Footage stays where it is. Media files are git-ignored.

## Stages

1. **Ingest** — `python3 scripts/ingest.py <project> <footage dir>...` (360p proxies, sheets, 16 kHz audio; skips LRV and our own renders), then
   `PATH=/opt/homebrew/bin:$PATH python3 scripts/transcribe.py <project> "<prompt with place names>"` (word timestamps, hallucination blacklist). Run both in background with `nohup`; they resume where they stopped.
2. **Look** — `python3 scripts/board.py <project> c001 c034 out.jpg` (3 frames per clip, 2 clips per row). Read every board before choosing anything; for hero shots make 1 fps strips; long underwater clips: read `ingest/sheets/<id>.jpg` (1 frame / 4 s) to find the highlights (fish schools, turtles). Build the trip skeleton (where/when) from boards + transcripts.
   - Motion: `~/.venvs/tts/bin/python scripts/motion.py <project>` (OpenCV; ~2 min for 200 clips) → `ingest/motion.json`: per second jitter, **roll** (deg/s — underwater "歪歪扭扭" is rotation, which the old translation-only `stability.py` could not see) and drone direction. The builder rejects shaky / rolling B-roll and back-and-forth drone moves and suggests steady windows; `b.steady()` and `vo(broll=[(clip, None)])` pick from them. Fish schools fool the estimate — look at a 2 fps strip and `b.allow_motion(clip, a, b, why)`.
   - Footage that isn't the user's (odd file names / vertical phone video / not in their story) — ask before using it, never narrate it as their experience.
   - Duplicates: `python3 scripts/dedupe.py <project>` → `ingest/duplicates.json` (same footage under another name; the builder treats them as one clip).
   - Times: iPhone `IMG_*.MOV` without a creation date get their file mtime (= shooting time, checked against photo numbers). Shared copies from other people have transfer times — fix them with `Builder(times={...})`.
   - Photos: `ingest/photos.json` (id, file, EXIF time) for the trip's photos; they fill gaps where video is thin (tarsier, fireflies).
3. **Story & outline** — per `storytelling.md`. Live lines come from transcripts with word timestamps; subtitle text is corrected by hand (ASR misspells places: 宿务, 薄荷岛). The user reviews the outline (several rounds) before any long render.
   - Talk: `python3 scripts/clean_speech.py <project> <clip> <from> <to>` lists clean pieces (fillers, pauses, false starts removed); pick pieces by hand (repeats across a pause are not caught). English speech gets Chinese subtitles.
   - Cold open ≤ 20 s. Chapters with poster cards; simple one-line cards always centred.
   - Every thing the narration names must be on screen at that moment — find it in the footage first (sardine storm: c143, not the wall clip). Can't find it → ask or cut the line. A line that doesn't fit its picture goes.
   - Names (chapters, routes, places, species) come from research, never invented: "传统路线" was made up; the route is the Bohol Countryside Tour / 陆地一日游.
   - Photos: never shirtless or otherwise unflattering / private ones; pick the clothed shots of a set.
4. **Narration** — the user records it. Script lines go into the EDL as `vo` (shown as subtitles, music ducked under them, B-roll stretched to fit). No AI voice in a final cut. Until it exists, trailer lines go on screen as text cards. When the user gives a short trip summary, expand it into the whole film's script (per chapter), not just the opening.
   - Learn the trip background before writing any script: why this trip, who's in it, what was planned vs. unexpected, the one moment that mattered. Ask if it isn't in the footage.
5. **Music is the soul of the film** — read the music section of `storytelling.md` first. Upbeat and young, *and* it must sound like the place: write three feeling words per section, then search (island and underwater → bright acoustic guitar / ukulele). **Never heavy, never drum-heavy** under island / underwater / talking scenes — tropical house "felt jumpy"; drumless dark ambient (Bartmann) "贼沉重". Measure before choosing: percussive share ≤ 11 % (HPSS), bass (< 130 Hz) ≤ 35 %, spectral centroid ≥ 1300 Hz, major key. Free, cross-platform licence only; record every track in `music/LICENSES.md`.
   - **Music stops completely** (`b.music_out('why')`, not `low`) for: someone singing or playing live, a performance or dance with its own music, a place whose sound is the point (waterfall, waves, a night river with only paddles), quiet places (church), the user's breathing / a heavy line. Let those run long — a whole verse, 15–45 s. Every music-free stretch has a written reason.
   - Songs never run into each other: the old one fades out (1.5 s) at the end of its section, 1–3 s of natural sound, the new one enters on the first wide shot of the next section. The opening song comes back at the end.
   - Search: `python3 scripts/fma_search.py <project>/music/fma ukulele underwater --download` (ONE word per query — multi-word queries return nothing; `--album <url>` downloads a whole album) (keeps only CC0 / CC BY, reads each track's own page, logs licences). Mixkit has no tropical / world tags. Pixabay blocks scripts — give the user titles to download. Proven per-scene picks: `docs/music-sources.md`. Pick by data: `~/.venvs/tts/bin/python scripts/music_profile.py <tracks>` (tempo, energy curve, biggest rises).
   - Never lay a near-silent stretch of a track under a montage.
   - Music-only change: `render_preview.py ... --reuse-video <previous render>` (picture fingerprint must match) — ~7 min instead of ~20.
   - Mix (automatic in the renderer): every song levelled to the same loudness first; under live speech the music sits ≥ 18 dB below the measured voice (verify fails < 15 dB); `ambient` shots play at 0.5 where no music plays; photos / drone shots in a music-free stretch need `bed=(clip, t)` natural sound — the builder rejects a shot with no sound and no music, verify rejects 1.5 s of silence.
   - Anchor the track's biggest entry (drums in, onset strength) on the most important cut: `music.start` = track time of the entry − (timeline time of that cut − music start time).
   - States `full | low | none` per `storytelling.md`, anchored to shots (`{"shot": i, "at": dt, "state": ...}`) so they follow re-timed cuts.
   - One song covers a 1.5–2 min section (tracks are only 100–140 s): check every section against its track length before rendering; change songs at story turns.
   - Snap montage cuts: `~/.venvs/tts/bin/python scripts/snap_to_beats.py <edl> <first_cut> <last_cut>` — max half a beat, speaking shots only grow. Keep a `*.before-snap.json` copy; re-snap from it, never twice on the same file.
6. **Look & transitions**
   - Grade: EDL `"look": "film"` (soft S-curve, teal shadows / warm highlights, very light vignette, fine grain). Underwater footage is auto-detected and corrected **per clip** — one set of parameters for every shot of a clip (per-shot grading made the same turtle clip jump colour between two shots) (`scripts/grade.py`: red compensation, white balance toward tropical-blue water — never neutral grey, luminance-led stretch, light denoise/sharpen). Override per shot with `"grade": "underwater" | "none"`.
   - After changing any grade parameter, make a before/after board (`python3 scripts/grade.py out.jpg <clip>:<t> ...`) with shallow water, deep blue water, a face and coral, and look at it.
   - Transitions generated in code: `"fx": [{"type": "leak", "cut": k}]` (warm edge glow over a cut; 1–2 per minute, at section changes), `"fade_from_black"` on a shot, `"typewriter"` time stamps. Outside footage only when code can't do it (`docs/footage-sources.md`); log licences in `footage/LICENSES.md`.
   - Every card / stamp has a `y`: put it on a calm area (sky, water), never over a ship, a face or signage.
7. **Build the EDL in code for anything longer than a minute** — a project `build_*.py` using `scripts/edl_builder.py` (`Builder.talk / vo / shot / photo / chapter / card / stamp / music / music_out / music_state / leak_before_next / steady`). Hand-written JSON does not scale to 150+ shots. The builder raises `EditError` on:
   - a source second used twice (duplicates count as one clip), or teaser clips reappearing in the body;
   - going back in time > 2 min in the body (`back=True` for a deliberate flashback);
   - shaky / rolling B-roll, drone moves that reverse (see Look → Motion; `motion_ok='why'` / `allow_motion` after looking);
   - on save: a title / chapter / end card on screen together with a subtitle (put chapter cards on a quiet shot — if a chapter opens with talk, on its first quiet shot); narration over audible speech in the picture (checked against transcripts, Whisper hallucinations filtered); a soundless shot where no music plays;
   - and `talk()` pads every piece (0.25 s before, 0.7 s after) and merges pieces < 1.2 s apart, so nobody is cut off mid-sentence.
   Subtitles never overlap: the renderer ends each line before the next starts. The user talking = no narration.
   Pacing: scenery shots 2.5–4 s, dissolves (`xfade=0.6`) or a leak at every place change, one breathing shot between chapters. Cut lines the user asks to remove (instructor asides, misspoken numbers) by splitting `talk` pieces around them.
   - Night photos (mean luminance < 45) are lifted automatically (denoise + autocontrast) so fireflies and tree lines read; still keep a near-black series to 2–3 photos.
   - Photos: `b.photo(pid, dur, caption)` or `('photo', pid)` inside `vo(broll=...)` — shutter sound, white flash, bordered photo on its own blurred copy, slow push-in. 2–4 in a row, never inside a live performance.
   - Titles: Pinyon Script place name + MaShanZheng Chinese + spaced Montserrat caps; chapter cards Playfair italic "Chapter One" / MaShanZheng title / caps line, bottom-left; end card "See you, <place>". Fonts in `fonts/` (all OFL).
8. **Preview** — `PATH=/opt/homebrew/bin:$PATH python3 scripts/render_preview.py <edl> <out.mp4>` (1080p from the originals). Exit code 1 = verification failed: **do not send it**. Shots render in parallel (`--workers 6`; ~1 s of render per second of film), each with its own overlays. It checks audio length = video length, every planned cut lands on its frame (frame-difference test; ffmpeg scdet misses cuts once grain is on), every live-sound shot within 40 ms of its source, and that every light leak visibly warms the frame.
   It also checks loudness (−16.5…−12.5 LUFS), true peak (≤ −0.5 dBFS), voice ≥ 15 dB over music on every talking shot, and no silent stretch.
   Then look at one frame per shot (`python3 scripts/contact_pages.py <render.mp4> <dir>` → contact pages of 30 shots), every text card / stamp mid-way, and every leak at its peak, and measure music under narration vs. music alone before sending. On the first full Bohol cut this pass caught a black clip, a vertical ceiling shot, someone else's group photo and 8 s of night-time black — none of which verify can see. A passing verify is not a look: the leak was "present" but a cheap full-frame wash, and a stamp sat on top of a ferry.
9. **Iterate in small pieces** — `render_preview.py <edl> <out> --shots 77:96` renders one section of the full EDL (music seeks to the right place); test renderer changes this way before a 17-minute full render. Changes are EDL edits.
10. **Deliver with the narration script** — `python3 scripts/narration_script.py <edl> <render.mp4> <project>/narration_script.md "<title>"` exports (timecode, line, suggested length; the user's own live lines shown as quotes) next to the cut so the user can record against it.
11. **Hand-off** — Jianying draft export is planned (`projects/_jianying-test/`, not yet confirmed working on Jianying 11.5 Mac, whose drafts are encrypted). Until confirmed, deliver MP4.

## EDL

See the docstring of `scripts/render_preview.py`. Subtitles reference a shot and *source* times; cards reference a shot and seconds into it — both survive re-timing. Audio per shot: `live` (dialogue, full), `ambient` (−18 dB), `mute`. Shot keys also: `grade`, `fade_from_black`. Top-level: `look`, `typewriter`, `fx`, `vo`, `music` (list of songs with `from_shot` / `to_shot`).

## ffmpeg rule

Keep each ffmpeg call small: audio is mixed in Python and loudness-normalised on its own; overlays are one pass; leaks are another pass; then mux with stream copy. Two silent failures came from cramming everything into one graph (24 s of audio dropped; leaks never drawn).

## Publishing

The public copy lives in its own repo (`~/Documents/vlog-edit-skill`, own history). After changing the skill, docs or scripts: `python3 scripts/publish.py --push`. It copies an allow-list only (skill, docs, scripts, fonts + licences, `publish/README.md`); `projects/` never goes out (faces in contact sheets, private transcripts, local paths), and it refuses to publish if a file contains a local path, the user name or the git e-mail.

## Asking the user

Write options from a neutral point of view ("我来挑" / "你来挑"), never "你自己挑一首给我" — it was read both ways and cost a round trip.
