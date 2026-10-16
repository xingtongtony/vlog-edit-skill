# vlog-edit

一个 [Claude Code](https://claude.com/claude-code) skill：用对话的方式，把一趟旅行的原始素材（无人机、运动相机、手机）剪成 6–15 分钟的横屏中文 vlog。

A Claude Code skill that turns raw travel footage (drone, action cam, phone) into a 6–15 minute landscape vlog, by conversation.

它负责的事：

- **素材盘点**：生成代理文件、缩略图、带逐字时间戳的转写稿；分析镜头运动（抖动、水下翻滚、航拍是否折返）；找出重复素材
- **讲故事**：按章节写旁白稿（不写流水账），从口播里挑出干净的句子
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
| `fonts/` | 字幕和标题字体，均为 SIL OFL 开源许可，许可证在 `fonts/licenses/` |

## 安装

需要 macOS（Apple 芯片）。

```bash
brew install ffmpeg
pip3 install mlx-whisper pillow numpy scipy
python3 -m venv ~/.venvs/tts && ~/.venvs/tts/bin/pip install librosa opencv-python-headless numpy
git clone <this repo> ~/Documents/vlog-editing
ln -s ~/Documents/vlog-editing/skills/vlog-edit ~/.claude/skills/vlog-edit
```

skill 默认工作区是 `~/Documents/vlog-editing/`。每次旅行在 `projects/<旅行名>/` 下建一个项目；素材留在原来的文件夹，不复制。

## 字体

资源圆体 Light（Resource Han Rounded）、Great Vibes、Pinyon Script、马善政楷书、志莽行书、站酷小薇、Playfair Display、Montserrat。全部是 SIL Open Font License 1.1，许可证全文在 `fonts/licenses/`。
