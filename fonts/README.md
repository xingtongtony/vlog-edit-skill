# 字体库

视频字幕、片名、章节卡、小红书封面用的字体。全部是 SIL OFL 或 Apache 2.0 开源许可，可商用，许可证在 `licenses/`。样张：`specimen.png`（`python3 scripts/font_specimen.py` 重新生成，会标出每款有没有简体、繁体）。

**只用年轻、时尚的字体。** 毛笔书法、楷体、行书草书、正式花体英文看起来老气，片主明确不要（2026-10-22）。第二批（古典衬线、粗衬线和“标准感”太强的无衬线）、第三批（2010 年代流行过的手写体）同样不要。已移出字体库的：龙藏体、马善政、志莽行书、刘建毛草、站酷小薇、霞鹜文楷、Pinyon Script、Great Vibes；思源黑体、思源宋体、Montserrat、Playfair Display、Cormorant Garamond、Alfa Slab One；Yellowtail、Dancing Script、Amatic SC。新加字体前先对照这一条。

**再看“繁”这一列。** 有些中文字体只有简体，写“島 / 賓 / 爾”会变成空方框；封面脚本会检查缺字并报错。站酷庆科黄油体还缺“·”。

## 中文

| 文件 | 字体 | 风格 | 简 | 繁 | 用在哪 |
|---|---|---|---|---|---|
| `LXGWMarkerGothic-Regular.ttf` | 霞鹜漫黑 | 干净的马克笔手写 | ✓ | ✓ | 片名、章节卡的中文；封面竖排地名（箱根、镰仓那种手写） |
| `ZCOOLKuaiLe-Regular.ttf` | 站酷快乐体 | 粗圆手写，活泼 | ✓ | | 封面地名、系列名（喀什、帕米尔那种粗手写） |
| `Yozai-Medium.ttf` | 悠哉字体 | 圆润随手写 | ✓ | ✓ | 轻松的手写标题、需要繁体的手写字 |
| `Xiaolai-Regular.ttf` | 小赖字体 | 可爱手写 | ✓ | ✓ | 俏皮的小标题、贴纸感文字 |
| `SmileySans-Oblique.ttf` | 得意黑 | 窄而有力的斜黑体 | ✓ | | 现代感标题、标签；封面底部白色系列名（“搓泥…环游记”） |
| `ZCOOLQingKeHuangYou-Regular.ttf` | 站酷庆科黄油体 | 粗壮、圆润的标题字（缺“·”） | ✓ | ✓ | 需要繁体的粗标题 |
| `ResourceHanRoundedCN-Light.ttf` | 资源圆体 细 | 圆体 | ✓ | ✓ | 视频字幕（规格见 `docs/storytelling.md`）；封面上下两条的小字说明（字距拉开） |

## 英文

| 文件 | 风格 | 用在哪 |
|---|---|---|
| `Anton-Regular.ttf` | 窄而极粗的展示字 | 封面主角大字：英文地名铺满整条（PHUKET、Vietnam 那种），常放在人物后面 |
| `BigShoulders[opsz,wght].ttf` | 又高又窄，从极细到极粗（粗细可调） | 细高的“VLOG”（youyou 那种），或粗的竖向大字 |
| `BricolageGrotesque[opsz,wdth,wght].ttf` | 有性格的现代无衬线（宽度、粗细可调） | 现代感标题、编号 |
| `InstrumentSerif-Regular.ttf` / `InstrumentSerif-Italic.ttf` | 窄而精致的现代衬线（流行的新衬线，不是古典） | 签名式的英文地名（“Bohol, Philippines”）、杂志感的小标题 |
| `InstrumentSans[wdth,wght].ttf` | 细而克制的无衬线（宽度、粗细可调） | 字距拉开的小标签（“TONY VLOG”“PHILIPPINES · 2026”） |
| `Fredoka[wdth,wght].ttf` | 圆头粗体（粗细可调） | 圆润的“Vlog”（New Zealand 那种） |
| `KaushanScript-Regular.ttf` | 粗笔刷手写 | “Tony Vlog”；片名里的英文地名、“Chapter One”、“See you, Bohol” |
| `Pacifico-Regular.ttf` | 圆润手写 | “Tony Vlog”（伊豆那种） |
| `Caveat[wght].ttf` | 随手写的笔迹 | “LifeVlog”那种浅色手写 |
| `PermanentMarker-Regular.ttf` | 马克笔 | 红色“vlog”涂写（青甘那种） |
| `LilitaOne-Regular.ttf` | 圆头粗体大写 | “TONY VLOG”（喀什、独库那种） |
| `BebasNeue-Regular.ttf` | 窄粗无衬线大写 | 角标、日期 |
| `Outfit[wght].ttf` | 细而现代的几何无衬线（粗细可调） | 字距拉开的小号英文：片名卡、章节卡、片尾卡的副标题，照片说明；四角小标签 |
| `SpaceGrotesk[wght].ttf` | 带一点个性的现代无衬线 | 日期、编号、杂志感的小字 |

## 来源

Google Fonts 官方仓库（github.com/google/fonts，`ofl/` 和 `apache/` 目录）；霞鹜漫黑、悠哉字体、小赖字体来自 github.com/lxgw 的发布页，得意黑来自 github.com/atelier-anchor/smiley-sans 的发布页。下载新字体时把许可证一起放进 `licenses/`。
