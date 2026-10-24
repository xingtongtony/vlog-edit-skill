# 免费音乐来源

只收免费的。用户的片子发在 B 站、小红书、YouTube 三个平台，而且可能有收益，所以每首歌都要满足：**允许商用**、**不限平台**。

调研时间：2026-10-15。"核实"一栏写的是信息来源：**官方**表示读过官方条款或官方页面，**二手**表示只看到第三方文章。条款会变，用之前再看一眼官方页面。

## 推荐优先用

| 来源 | 风格和体量 | 商用 | 署名 | 多平台 | 风险 | 核实 |
|---|---|---|---|---|---|---|
| **Pixabay Music**（pixabay.com/music；有真人验证，需用户自己下载） | 各类型都有，上万首；电影感、氛围类较多 | ✅ | 不需要 | ✅ | 少数歌的作者在 YouTube 登记了 Content ID，可能被自动认领；凭下载记录和授权证书可以申诉 | 官方：不用署名、不能单独转售。商用和 Content ID 部分是二手 |
| **ACE-Step 1.5**（本地 AI 生成） | 按描述生成，可控制时长（10 秒–10 分钟）、BPM、调性、乐器 | ✅ 模型页明确写可商用 | 不需要；建议注明 AI 生成 | ✅ | 质量还没在这台 Mac 上试过；模型方要求披露 AI 参与 | 官方：代码和模型 MIT；训练数据是授权音乐、公有领域和合成音频；支持 Apple 芯片（MLX） |

**ACE-Step 的特别价值**：曲库里的歌长度和结构都是固定的，只能剪。本地生成可以直接按大纲要（"开场 45 秒，空灵，70 BPM，第 30 秒推起来"），每段音乐长度正好、在想要的位置起落。免费曲库最缺的就是电影感的歌，正好用它补。

## 可以用，但要守规矩

| 来源 | 风格 | 规矩 | 核实 |
|---|---|---|---|
| **Incompetech**（Kevin MacLeod） | 电影、氛围、古典感，数量多 | CC BY 4.0，**必须署名**。用的人太多，辨识度高 | 二手 |
| **YouTube 音频库**（YouTube Studio 里） | 各类型 | **只能用标了 CC BY 的歌**，并照它给的文字署名。普通授权的歌只限 YouTube，不能发 B 站、小红书 | 二手；各文章说法有冲突，用前看每首歌的授权说明 |
| **Free Music Archive**（freemusicarchive.org） | 独立音乐人，风格杂；**按关键词能找到地域感的歌**（ukulele、tropical、island、hawaiian） | **只挑 CC0 和 CC BY**。授权按每首歌自己的页面判断，同一张专辑里可能混着 NC。`scripts/fma_search.py <music目录> ukulele tropical --download` 只保留 CC0/CC BY，并自动写进 LICENSES.md | **官方**（逐首读歌曲页的 CC 链接，2026-10-15） |
| **Jamendo / ccMixter** | 独立音乐人，风格杂 | 只挑 CC0 和 CC BY | 二手 |
| **StreamBeats**（Harris Heller） | 1,700+ 首，lofi、氛围、合成器浪潮等 | 官方写着免费、可盈利、不用署名；具体的同步授权条款在另一页，还没读 | 官方首页 |
| **Mixkit**（Envato 旗下） | 电影、电子、民谣；电影感分类 36 首。**没有热带、尤克里里、世界音乐分类**，配海岛和异国风情会显得通用 | Stock Music Free License：可商用，可发任何网页和社交平台（含 YouTube），不用署名；不能混音、转售、登记版权管理。被认领时邮件 team@mixkit.co | **官方**（2026-10-15 读授权页原文） |
| **Uppbeat 免费版** | 质量较高 | 每月下载数有限，需要署名，可把频道加白名单避免 Content ID | 二手；官网当时拒绝访问 |

## 国内站点

| 来源 | 说明 | 核实 |
|---|---|---|
| 淘声网 | 聚合搜索国内外声音素材，每条标了授权，要逐条看 | 二手 |
| 爱给网 | 配乐库有"免费商用（CC 协议）"筛选 | 二手 |
| FREESOUND 飞声 | 写着免费可商用，免费下载每天有额度 | 二手 |

国内站点的质量和授权都参差不齐，适合找音效，主力配乐不建议用。

## 不要用

- **任何带 NC（非商业）的 CC 音乐**：有收益的视频就算商用
- **"无版权音乐"类 YouTube、B 站频道的转载**：来源不清，最容易被认领
- **别人视频里扒下来的音乐**
- **Freeplay Music 免费版**：只限个人和课堂使用
- **MusicGen**：模型权重禁止商用
- **Stable Audio Open**：许可证说法不一，没弄清前不用

## 每首歌都要留记录

在项目文件夹的 `music/LICENSES.md` 里，每用一首歌记一行：歌名、作者、来源链接、授权类型、下载日期，并保存授权页截图或授权证书。被认领时，申诉就靠这些。需要署名的歌，发布时把署名写进视频简介。

## 已经验证好用的歌（按场景）

| 场景 | 歌 | 来源 / 授权 |
|---|---|---|
| 海岛开场、结尾呼应 | Summer Tropical House（Music for Creators） | FMA，CC BY，要署名 |
| 清晨出发、在路上 | Monplaisir: On the road / Red Hair Blue Sky | FMA，CC0 |
| 学潜水、训练、轻松的日常 | Monplaisir: Sincere Love / Hop | FMA，CC0 |
| 夜里、安静、萤火虫 | Monplaisir: Stay Quiet | FMA，CC0 |
| 海里训练、潜水 | Music for Creators: Peaceful Background Acoustic / Upbeat Inspiring Folk Acoustic Guitar（明亮原声吉他） | FMA，CC BY，要署名 |
| 浮潜、珊瑚 | Monplaisir: Je voudrais être un pigeon / On est bien, là（专辑 *GOOD*） | FMA，CC0 |
| ~~水下~~ | ~~John Bartmann: Underwater Wonders~~ 没有鼓，但低频占 55–77%、很暗，用户说“贼沉重” | FMA，CC0 |
| ~~海边、水下~~ | ~~Shaloza: Waves / Mirihi / Back To The Sea / Maalhos~~ 热带浩室，鼓点太重（打击乐占比 10–31%），用户说 5:20 很跳脱、和海岛不搭。只能用在开场快剪 | FMA，CC0 |
| 午后、慢下来 | Monplaisir: Time Gap | FMA，CC0 |
| 高处、无人机大景 | Monplaisir: First | FMA，CC0 |

Monplaisir（尤克里里）和 John Bartmann（水下氛围）的这几张专辑整张都是 CC0，海岛题材先在里面找。

**FMA 搜索只认单个词**：搜 “calm ukulele” 一首都找不到，要分别搜 “ukulele”、“underwater”、“reef”。找到合适的专辑后，用 `fma_search.py <目录> --album <专辑页链接> --download` 整张下载，授权仍然逐首核对。

**选歌前先测量**（`music_profile.py` 看能量曲线；打击乐占比、调式用 librosa 的 HPSS 和 Krumhansl 调性模板测），记在这里：

| 歌 | 打击乐占比 | 低频占比 / 频谱重心 | 调式 | 用在 |
|---|---|---|---|---|
| Bartmann: Coral Flyby / Living Shipwreck / Untamed Reef / Water Forest | 1–2% | 55–77% / 300–470 Hz | 大调 | 不用：太沉 |
| MfC: Peaceful Background Acoustic | 4.6% | 28% / 2095 Hz | 大调 | 水下训练 |
| MfC: Upbeat Inspiring Folk Acoustic Guitar | 5.5% | 17% / 1316 Hz | 大调 | 最后一潜 |
| Monplaisir: Je voudrais être un pigeon | 7.1% | 2% / 1093 Hz | 大调 | 浮潜 |
| ~~Monplaisir: On est bien, là~~ | 10.6% | 31% / 1859 Hz | 大调 | **不用：失真摇滚**（密度 0.88、高频 4.7%），用户说像重金属 |
| Monplaisir: Sincere Love / First / Red Hair Blue Sky / Time Gap / Stay Quiet | 3–10% | — | 多为大调 | 陆地、日常 |
| Music for Creators: Happy Feel Good Acoustic Pop | 8% | — | 大调 | 轻快的下午 |
| Music for Creators: Summer Tropical House | 9% | — | 小调 | 只用于开场和结尾呼应 |
| Shaloza: Mirihi / Maalhos / Waves | 16–31% | — | — | 不再用于海岛段落 |

## Pixabay 需要用户下载

Pixabay 有 Cloudflare 真人验证，脚本下载不了。需要时给用户列出搜索词和歌名，用户下载后放进 `music/`。

## 每一期用过的歌（下一期不要再用，2026-10-24 片主："音乐不要每次剪都是这几首"）

| 项目 | 用过的歌 |
|---|---|
| 菲律宾薄荷岛（2026-10） | Monplaisir: On the road / Red Hair Blue Sky / Sincere Love / Stay Quiet / Hop / Time Gap / First / Je voudrais être un pigeon；Music for Creators: Summer Tropical House / Peaceful Background Acoustic / Happy Feel Good Acoustic Pop / Upbeat Inspiring Folk Acoustic Guitar |
| 巴厘岛 v1（作废） | 同上一批，被片主否了 |
| 巴厘岛 v2 | Mixkit: Magical Moment / Traveling Along / A Happy Child / Forever in My Heart / Down the Hill / Slow Pop / Chillax / Relaxing Country / Swings and Slides / A Special Feeling；HoliznaCC0: Summer Felt Longer Then（FMA，CC0） |

下一期先从 `projects/*/edl/*.json` 里列出用过的歌，排除掉再选。
