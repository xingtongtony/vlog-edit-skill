# vlog-edit

一个 [Claude Code](https://claude.com/claude-code) skill：用对话的方式，把一趟旅行的原始素材（无人机、运动相机、手机）剪成 6–15 分钟的横屏中文 vlog。

A Claude Code skill that turns raw travel footage (drone, action cam, phone) into a 6–15 minute landscape vlog, by conversation.

它负责的事：

- **素材盘点**：生成代理文件、缩略图、带逐字时间戳的转写稿；分析镜头运动（抖动、水下翻滚、航拍是否折返）；找出重复素材
- **讲故事**：全片一条主线，按章节写旁白稿；旁白按段写、按片主实测的语速排时间（不写流水账，也不一句一句干等字幕）；从口播里挑出干净的句子
- **剪辑表**：用 Python 写成，剪辑工具会自动拦下常见错误，包括：
  - 同一段画面用两次
  - 时间倒退
  - 不稳的空镜、来回折返的航拍
  - 旁白压在人声上
  - 章节卡和字幕撞在一起
  - 没有声音也没有音乐的镜头
- **渲染**：1080p，按素材统一调色（含水下校正），烧录字幕、片名和章节卡，加漏光转场；照片带快门声；配乐按人声自动压低
- **自检**：画面帧数、剪辑点、口播同步、人声是否高过音乐、有没有静音段、响度和峰值
- **选歌**：只用 CC0 / CC BY 的免费音乐，先测量鼓点能量、低频占比、明亮度和调式再挑
- **导出剪映草稿**（剪映专业版 Mac 11.6 实测可用）：每个镜头提前调好色并留出前后余量，字幕和标题直接用成片里的图片（另附 SRT 方便改字），音乐先统一好响度、说话处带压低关键帧，照片带推近和快门声，漏光用“滤色”叠加。打开就和成片一样，同时保留剪辑点可调
- **读回剪映里的修改**：剪映存盘后草稿会加密，所以拿片主从剪映导出的成片和渲染对比，找出换过的镜头（定位到原素材的第几秒）、改过的字幕、黑帧和响度，再把这些修改做回剪辑表，重新导出时不会冲掉

剪辑方法和踩过的坑都写在 `docs/` 里。它们是在一支真实的旅行片上，按片主的反馈一轮一轮改出来的。

## 目录

| 路径 | 内容 |
|---|---|
| `skills/vlog-edit/SKILL.md` | skill 本体：流程、命令、规则 |
| `docs/storytelling.md` | 叙事、节奏、字幕规格、音乐、调色、转场的规则（剪片前必读） |
| `docs/toolchain.md` | 工具链和踩过的坑 |
| `docs/music-sources.md` | 免费音乐来源、授权规矩、验证过好用的歌 |
| `docs/footage-sources.md` | 免费视频素材，以及哪些转场用代码生成 |
| `docs/workflow.md` | 和片主约定的剪辑流程 |
| `scripts/` | skill 用到的脚本（入口见 SKILL.md） |
| `scripts/export_jianying.py` | 把剪辑表导出成剪映草稿。导出前先退出剪映（⌘Q）。Mac App Store 版剪映有沙盒，素材会放进 `~/Movies/JianyingPro/Claude素材/` |
| `fonts/` | 字幕和标题字体，均为 SIL OFL 开源许可，许可证在 `fonts/licenses/` |

## 安装

需要 macOS（Apple 芯片）。

```bash
brew install ffmpeg
pip3 install mlx-whisper pillow numpy scipy pyJianYingDraft
python3 -m venv ~/.venvs/tts && ~/.venvs/tts/bin/pip install librosa opencv-python-headless numpy pillow
git clone <this repo> ~/Documents/vlog-editing
ln -s ~/Documents/vlog-editing/skills/vlog-edit ~/.claude/skills/vlog-edit
```

skill 默认工作区是 `~/Documents/vlog-editing/`。每次旅行在 `projects/<旅行名>/` 下建一个项目；素材留在原来的文件夹，不复制。

## 字体

资源圆体 Light（Resource Han Rounded）、Great Vibes、Pinyon Script、马善政楷书、志莽行书、站酷小薇、Playfair Display、Montserrat。全部是 SIL Open Font License 1.1，许可证全文在 `fonts/licenses/`。
