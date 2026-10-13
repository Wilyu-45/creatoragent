# 能力接入样例与工场（可选增强）

**读者**：想启用「数字人 / 图片 / 视频 / 视频理解 / 桌面宠物」通道、「灵感冲浪」自主上网找灵感或两个前端工场的使用方与部署方。
**回答**：每条样例的通道与端点、界面怎么用、接口接缝与失败边界。
**不负责**：流水线智能体职责与黑板产物契约（[`creator.md`](creator.md)）、环境变量全集与界面配置步骤
（[`USER_GUIDE.md`](USER_GUIDE.md)）、部署与存储替换（[`DEPLOYMENT.md`](DEPLOYMENT.md) /
[`storage_contract.md`](storage_contract.md)）。本文的这些细节以那里为准。

---

## 0. 共同范式（读一次，后面不再重复）

五条旁路通道（数字人 / 图片 / 视频 / 视频理解 / 桌面宠物）行为完全同构，差别只在产物结构与端点配置：

- **旁路作业（job store）而不是黑板产物**：新增 Artifact 类型要同步改四处契约（MEMORY #31），风险高且这些能力的
  本质是「异步作业」而非创作链产物，所以统一走 `app/core/gen_jobs.py` 的作业后端（file 模式 JSON / pg 模式各一张表）。
- **状态惰性推进**：没有后台线程，读取时按流逝时间（样例引擎）或远端状态（真实通道）计算——服务空转时不产生任何写盘。
- **`POST 建作业 → GET 查状态`最小契约**：创建返回 201，请求体里的 `provider` 可覆盖默认通道（分派按作业实际生效的
  provider，不按全局配置，MEMORY #38）。
- **可观测与隔离**：每个作业一个根 span（名称见各节），状态迁移发 `event_bus` 事件、与任务轨迹对齐；按租户隔离；
  任务删除时作业连同该作业的落盘文件一起回收。
- **诚实失败**：未配置端点 / 缺前置产物 / 任一帧或单次结果拿不到 → 作业 `failed` 并写明原因，**绝不假装成功**；
  真实通道的保守成本估算在**受理前**纳入 `COST_BUDGET_USD`，超预算即熔断且不提交（本地端点计 0 元，MEMORY #29）。
- **界面**：任务详情页的对应面板（前置产物不满足时面板不出现）；通道与端点在「运行时设置 → 对应小节」或环境变量配置。

---

## 1. 数字人渲染

**模块**：`app/core/digital_human.py`。**定位**：HeyGen / D-ID / 腾讯智影的授权、形象库、计费与回调协议差异极大，
「接哪家、要不要接」是产品形态决策；系统提供的是可回归的接入样例，让联调在采购任何服务之前就能发生，换厂商时只替换网关层。

| 通道 | 行为 |
|---|---|
| `sample`（默认，零依赖） | 离线确定性模拟「排队 → 渲染 → 完成」，并按 `video_script` 生成**渲染清单**（每镜台词 / 字幕 / 机位 / 起止时间 / 是否有口播，无口播分镜显式告警） |
| `http` | 配置 `DIGITAL_HUMAN_API_URL` 后对接「POST 建任务 → GET 查状态」最小契约的任意网关（自建渲染农场、n8n 编排均可）；厂商私有协议由你的网关层消化 |

- **前置与接缝**：任务须已产出 `video_script`，否则 `POST /api/tasks/{id}/digital-human` 返回 409；
  `GET /api/tasks/{id}/digital-human` 查作业。
- **界面**：「数字人渲染」面板（创建作业、进度条、渲染清单分镜表、成片地址）。
- **span**：`digitalhuman.render`。

## 2. 图片生成

**模块**：`app/core/imagegen.py`。各家文生图的授权、计费与返回结构差异极大，系统不内置实现。目的是让 A8 从「只出 `image_prompts`」
升级到「真出封面 / 分镜静帧」，服务解说视频的封面与 B-roll。

| 通道 | 行为 |
|---|---|
| `sample`（默认，零依赖） | 离线确定性模拟「排队 → 生成 → 完成」，按 `visual_brief` 的 image_prompts 出**生成清单**（不产真图） |
| `openai` | OpenAI 协议 `POST {base_url}/images/generations`（覆盖经网关的即梦 / 通义万相 / Replicate 等）；返回只有 `b64_json` 时落盘 `assets/` 再引用（MEMORY #42） |
| `local` | 本地图生网关 `POST {base_url}/sdapi/v1/txt2img`（A1111 / SD-WebUI）；本地端点自动计 0 元 |

- **前置与接缝**：任务须已产出 `visual_brief`（含 image_prompts），否则 409；
  `POST /api/tasks/{id}/images` 建作业、`GET /api/tasks/{id}/images` 查状态。
- **界面**：「图片生成」面板（创建作业、进度、逐提示词出图状态与地址）。
- **span**：`imagegen.render`。

## 3. 视频生成

**模块**：`app/core/videogen.py`。**定位**：消费 `video_script` 的分镜清单，按镜产出画面段（逼近「可开拍素材」），
**不做剪辑合成**——合成归 [7. 视频工场](#7-视频工场字幕--成片) 或人工。与数字人同范式，复用 `build_manifest()` 把分镜翻译成渲染清单。

| 通道 | 行为 |
|---|---|
| `sample`（默认） | 离线确定性模拟生命周期，按分镜出**渲染清单** |
| `http` | 按 `VIDEOGEN_API_URL` 启用：云端视频网关（可灵 / 即梦 / Runway…）或自建本地渲染农场均可 |

- **为什么只有两条**：建任务 / 查状态本身就是统一的异步 http 契约，本地 ComfyUI 农场把 http 指向本地端点即可覆盖
  （自动计 0 元），不需要单独的 `local` provider。
- **前置与接缝**：任务须已产出 `video_script`，否则 409；`POST /api/tasks/{id}/videos`、`GET /api/tasks/{id}/videos`。
- **界面**：「视频生成」面板（创建作业、估算成本 Chip、逐镜画面段 URL / 时长 / 状态）。
- **span**：`videogen.render`。

## 4. 视频理解

**模块**：`app/core/videounderstand.py`。**定位**：与 2 / 3 的「输出画面」互补，这里补**摄取侧的「看懂画面」**——服务 B 站解说号「逐集看完番剧再据内容创作」。
它是 `app/core/digest.py`（文档版多模态理解）的**画面版镜像**：把**整集视频**交给**原生支持视频输入的理解网关**
（一集=一次调用，成本可控、贴「逐集理解再创作」的节奏）→ 取回该集**结构化视觉摘要** → 一键「注入 Brief」驱动下游创作。
理解推理不在系统内实现、不绑厂商（Gemini 视频输入 / 自建理解农场等私有协议由网关层消化）。

| 通道 | 行为 |
|---|---|
| `sample`（默认） | 离线模拟「提交整集 → 轮询 → 完成」，产出带 `simulated` 标记的**占位视觉摘要骨架**（不调网关） |
| `real` | 按 `VIDEOUNDERSTAND_API_URL` 提交整集、惰性轮询取回摘要 |

- **前置与接缝**：任务在 Brief 素材里带一条视频（`kind=video` 或带视频扩展名的 `assets/` 本地文件），否则 409；
  `POST /api/tasks/{id}/video-understanding`、`GET .../video-understanding`（含 `has_video_asset`）、
  `POST .../apply`（把 `summary.text_brief` 幂等追加进 `brief.constraints`）。
- **不搬运大文件**：只把 `assets/` 内的**视频引用**交给网关自取（网关需与素材同机 / 可达），沿用「只认 assets/ 本地、
  不下载公网与内联视频」的安全边界，拒绝对路径与 `..` 越界。视频识别按**扩展名**兜底——`load_local` 的 8MB 闸门是为
  「进模型的字节」设的，整集番剧会被 `parse_asset` 提前返回、kind 仍默认 image（MEMORY #46）。
- **失败链顺序**：`real` 的三项前置（未配端点 → 非本地文件 → 文件缺失）全部在触网前判定，未配端点放最前，
  使 mock 门禁下确定性失败且不发任何请求（MEMORY #45）。
- **界面**：「视频理解」面板（选通道、创建作业、进度、结构化摘要——主题 / 场景 / 人物 / 动作 / 名场面 / 镜头 / 节奏、
  `simulated` 标注、「注入 Brief」按钮）。摘要**不落黑板**，注入 Brief 是显式动作。
- **span**：`videounderstand.understand`。

## 5. 桌面宠物

**模块**：`app/core/petgen.py`，运行器 `app/desktop_pet.py`。**定位**：把 A8 的视觉指导变成一个**能真跑在桌面上的交付物**（宠物包），用来验证旁路通道不止出清单、也能出可运行产物。
形象的美术生产不在系统内实现。

| 通道 | 行为 |
|---|---|
| `sample`（默认，零依赖） | 纯标准库（zlib 手写 PNG 编码）按清单**真画出各动作帧**并装包：帧是真帧、形象是简笔稿，`simulated=true` 如实标注 |
| `imagegen` | 复用 [2. 图片生成](#2-图片生成) 的 `IMAGEGEN_*` 端点**逐帧**出真图（一帧=一次调用），不另持端点与密钥（避免两处配置漂移） |

- **不做图像处理**：无抠图 / sprite sheet 切片 / 帧对齐。帧背景统一为透明键纯色 `#FF00FE`，运行器把它设为窗口
  `-transparentcolor`（Tk 没有桌面 alpha 合成路径），界面预览按同一规则逐像素抠透明——所见即桌面所得。
- **交付物**：`data/pets/<job_id>/`（`frames/` + `pet.json` + `README.md`），装配 zip 时内嵌 `runner/pet.py`
  （读不到源码则 `pet.json` 的 `runner=""`，属可见降级而不是静默缺文件）。
- **运行**：仓库内 `python -m app.desktop_pet <zip 或目录>`；脱离仓库跑包内 `python runner/pet.py <解压目录>`。
  运行器只用标准库 tkinter：无边框置顶透明窗、按 `pet.json` 播放动作、自动走动与边缘折返、可拖拽（`drag` 动作）、
  双击卖萌、右键菜单切动作与退出。
- **前置与接缝**：任务须已产出 `visual_brief`，否则 409；`POST /api/tasks/{id}/pets`、`GET /api/tasks/{id}/pets`
  （含 `has_visual_brief`）、`GET .../pets/{job}/frames/{action}/{index}`（帧不在静态目录里：只认已知动作 +
  正整数帧号 + 目录必须仍在 `pets/` 下）、`GET .../pets/{job}/package`（未完成 → 409，作业不属于该任务 → 404）。
- **整包失败口径**：任一帧拿不到图则整包判失败——缺帧的宠物包不算交付。
- **span**：`petgen.render`；删除任务时作业与帧目录一起回收。

## 6. 动效工场

**模块**：`src/motion/`（视频动效组件库）。顶栏「✦ 动效工场」进入。纯前端工具，把视频脚本（[`USER_GUIDE.md` §12.3](USER_GUIDE.md)）的分镜画面设计成可预览的动效序列。

- **预设试演**：22 个预设按 入场 / 强调 / 出场 / 转场 分组（FadeIn、Typewriter、HighlightSweep、Glitch…），选中即自动播放。
- **JSON 脚本**：声明式时间轴 `{duration, tracks:[{preset, at, duration, hold, text, easing, params, style}]}`，
  「应用到预览」整体预演，非法字段以中文报错；内置示例一键载入。
- **播放控制**：播放 / 暂停、进度拖动、0.5–2× 倍速、循环；播完出现「重播 / 回到起点」覆盖层，重播时状态完整重置。
- **实现**：纯 React 时间驱动渲染（无内部定时器），seek 与倍速天然免费；接渲染服务即可导出 MP4。

## 7. 视频工场（字幕 → 成片）

顶栏「▶ 视频工场」进入，纯前端字幕成片工具（`src/vg/`）：粘贴字幕，自动建轴、实时预览、导出真实视频文件。

- **字幕**：SRT / WebVTT / ASS / 纯文本（按标点自动断句、按语速计时），解析后即时显示条数与总时长。
- **自动建轴**：智能分幕 + 节拍点卡点吸附（偏差 0，满足 ±0.1s）；6 主题 × 4 背景、7 档调色、6 类转场、6 种字幕动效、
  Ken Burns 推拉、引号 / 数字强调，每项可从「智能轮换」改为固定。
- **去 AI 味**：种子随机轮换动效避免机械重复，「🎲 换一批」一键重随机；内置 AI 味词 lint（「首先 / 其次」式开头提示改写）。
- **音频**：可挂 BGM（循环 + 淡入淡出）与旁白，旁白出现时 BGM 自动压低（ducking）。
- **导出**：Canvas 实时编码 720p / 1080p / 4K、24 / 30 / 60 fps，MP4 优先、WebM 回退；进度 / 暂停 / 继续 / 取消齐全；
  耗时 ≈ 片长，导出期间需保持页面前台（切后台自动暂停，跨会话续传受容器格式限制，如实标注）。

## 8. 创作技能提炼

**模块**：`app/core/skillgen.py`。**定位**：把 UP 主**自己的作品**（往期文案、口播字幕、成片解说）提炼成
扩展插件可直接装载的**通用技能**——一份带 frontmatter 的 `SKILL.md` + `skill.json` + zip。它同样是旁路作业，
**不落黑板**（MEMORY #31）：技能是作品统计的产物，不是流水线的一道工序。

| 通道 | 行为 |
|---|---|
| `rules`（默认，零 token） | 纯标准库把正文的**结构与节奏真统计**成技能（句长 / 段落 / 语速 / 疑问句与第二人称占比），每条结论都带 `evidence` 指向本组观察值；`simulated=true` 如实标注「不含风格判断」 |
| `llm` | 复用系统已有的 `LLM_*` 网关做**带判断力**的提炼（一次作业=一次调用），不另持端点与密钥；`LLM_PROVIDER=mock` 或未配 `OPENAI_BASE_URL` 时**显式失败、不假装提炼** |

- **作品取自三条现成接缝**：`Brief.assets` 的 `document`（`.md/.txt/.srt/.vtt/.ass`，走 `digest.collect_documents`
  这条唯一摄取路径，8MB 闸门、不搬远端大文件）→ 已产出的文本产物（同类型只取最新版，返工稿不重复计入）→
  已完成的 [§4 视频理解](#4-视频理解) 摘要。正文短于 40 字**记进 issues 而不是静默丢掉**；超出
  `SKILLGEN_MAX_WORKS`（1–20）同样如实声明。
- **统计口径本身就是产品主张**，所以三条坑都堵在代码里：断句保留句末标点（否则疑问句占比恒为 0）、字数按
  **非空白字符**（与文案编辑器同口径，否则语速虚高）、字幕**只认整行以时间戳开头**的时间轴行——研读摘要里
  转述的 `00:00:00,000 --> …` 若被当成一条巨型字幕，整篇段落与语速会一起失真。
- **诚实边界可见**：`simulated=true` 同时写进产物与面板；只有 1 份作品时首条反例是「样本只有 1 份，
  不构成通用方法，请再投 2–3 份重跑」；`examples` 里的逐字原句一律标注出处，且反例明令不许搬用。
- **交付物**：`data/skills/<job_id>/`（`SKILL.md` + `skill.json` + `README.md`），装配 zip 时把 `package.zip`
  留在目录外（`rglob` 排除自身）。`SKILL.md` 的 `name` / `description` 即插件技能格式，可直接投给扩展插件；
  「沉淀进记忆库」把同一份正文交给 `memory_store.remember` 的 `template` 卡片（内容指纹幂等，重复点不产生副本）。
- **前置与接缝**：无作品时 `POST` 返回 409（附缺了什么）；`GET /api/tasks/{id}/skills`（含 `has_materials`）、
  `GET .../skills/{job}/document`（未完成 → 409）、`GET .../skills/{job}/package`、`POST .../skills/apply`；
  跨租户或不存在的作业一律 404。**空转不写文件**：作业完成时才落盘。
- **成本**：`rules` 恒为 0；`llm` 在受理前按一次调用预估并纳入 `COST_BUDGET_USD` 熔断（超预算 `attempts=0`、不提交）。
- **span**：`skillgen.distill`；删除任务时作业与技能目录一起回收。

---

## 9. 灵感冲浪（自主上网找灵感 → 跨任务 idea 库）

**模块**：`app/core/surf.py`。**定位**：解决「创作时严重缺灵感」——像真人一样上网刷信息流/看视频，
把外部信号经系统 LLM 网关消化成一批**灵感卡**，沉淀成跨任务的**灵感 idea 库**，由用户自行挑选、调整后再创作。

| 通道 | 行为 |
|---|---|
| `sample`（默认，零依赖、零外网） | 离线把主题展开成查询→产确定性**占位灵感**，自报 `simulated=True`；API/UI/doctor 回归全链路可跑 |
| `browser`（真引擎·惰性可选） | **惰性** `from playwright.sync_api import sync_playwright`，只 navigate 白名单域内公开站，**像真人一样浏览**：随机幅度/随机停留地刷页面（偶尔原地停一下、开新页前先犹豫一下）→ 用**起点站自己的搜索框**逐字打字搜主题（打字时站点弹出的**搜索联想**里也挑认得出主题词的那条点上）再读结果页 → 点站点自己的「下一页/加载更多」接着刷列表 → 内容页点开「展开全文」并顺手逐条抓评论区 → 从信息流/结果列表**先扫标题认出主题词**挑几条同域**普通内容页**点进去继续读（并记下凭哪几个词点进去，**上几轮读过的先跳过、改读没见过的新条目**；读到登录/订阅/验证/维护页就**只记下门口那几句话就退出，不绕墙**）→ 抽取正文/热榜；`SURF_WATCH_VIDEOS`（或会话带 `videoUrls`）开启后再**点进视频页在线看**：点播放→确认画面在推进→播放中一段一段截图→**一组一组**交 VLM→逐组小结后整段汇总；未装/未配即受理点名诚实失败 |
| `http`（轻量真通道） | 复用系统既有 `web.py` 的 `web_search`+`fetch_page`（`WEB_SEARCH_*`）做冲浪；未配检索即诚实失败点名 `WEB_SEARCH_API_URL` |
| `auto` | 按 browser→http→sample 择优（Playwright 可用则 browser，搜索网关已配则 http，否则 sample 并在 warnings 写明） |

- **与其他旁路通道唯一不同**：会话走 `surf_jobs`（同 `gen_jobs` 双后端），但产物**灵感池是独立的
  跨任务存储**（`surf_insights` / `data/surf_pool.json`），**不随任务删除**（`drop_task()` 只清会话），
  按 `SURF_POOL_MAX` 裁剪最旧的。同一条路上还有第三份跨会话存储——**浏览历史**
  （`surf_history` / `data/surf_history.json`，主键 `<tenant>|<url>`，行带 `reads`/`first_seen`/`last_seen`），
  同一套双后端、同样**不随任务删除**、按 `SURF_HISTORY_MAX` 先忘最旧的，且**只有 browser 通道写**（见下条真人式浏览）。
- **灵感不自动交付智能体**：卡入 idea 库后，用户在 `SurfPanel`「灵感库」tab 勾选、调整，再
  `POST /api/tasks/{id}/surf/apply` 幂等注入 `brief.constraints`（前缀 `【灵感冲浪】`）供下游 A2/A3/A4 引用。
- **真人式浏览（仅 browser）**：`SURF_HUMAN_PACE` 开启后 `_pace_rng` 用**会话级确定种子** `f"surf:{topic}|{起点的 URL}"`
  喂 `_dwell`/`_scroll_like_human`，每轮滚动的幅度与停留随机浮动，且约 1/5 的概率**原地停一停不滚**；关掉就是等距滚、固定等待的机器节奏
  （每条文本信号都记下 `scrolls`/`pauses`/`dwell_ms` 供核对）。`SURF_SEARCH_ON_SEEDS` 开启后 `_site_search` 像真人那样
  点起点站的搜索框、`press_sequentially` 逐字打字（失败退回 `fill`）、回车或点提交按钮、等 load、再滚两轮读结果；
  **没有搜索框 / 点不动 / 填不进 / 回车与按钮都没触发 / URL 未变 / 结果页越界**时一律**如实不搜**（返回空并写进 `note`，退回起点站继续刷，绝不假装搜过）。
  **搜出来一看不对题就换个说法再搜**（`SURF_SEARCH_RETRIES`，`_search_like_human`）：`_query_variants` 把主题排成真人会依次试的几版词
  （整句 → 按顿号/逗号/斜杠切开的词组 → 只留最前面那一两个词，**换出来的字都来自本次主题**，不引同义词表也不凭空造词），
  `_results_look_relevant` 用 `_page_links` 扫一眼前几条结果标题（一个主题词都认不出、或一条都没读到 = 没搜到对题的东西），
  不满意就 `_site_search` 先 `fill("")` **清空搜索框里的旧词**再逐字打新词；换到贴主题为止，**再没有更短的说法可换就照旧读最后一版**
  （不重复写说明，「标题里认不出主题词」仍由挑条目那步的 `note` 如实交代）。搜过的每版关键词落进信号的 `query`/`search_tried`，
  真换了词才写「换了 N 次说法才搜到贴主题的条目」——**没搜到对题的东西就绝不写「才搜到」**；`SURF_TOPIC_FOCUS=false` 或 `SURF_SEARCH_RETRIES=0` 时不判、不换词，退回整句搜一次的旧口径。
  **打字时站点弹出的搜索联想也瞄一眼**（`SURF_USE_SUGGESTIONS`，一次最多读回 `SURF_SUGGESTIONS_MAX` 条）：`_site_search` 打完字先用
  `_read_suggestions` 按候选选择器读回下拉里的候选词（保持站点给的原顺序、去空去重），再用 `_pick_suggestion` 拿**挑条目那同一只眼**
  （`_relevance` 数认出几个主题词）挑出认得出本次主题词的那条点下去——同分时保持站点给的顺序（站点排序本身是信息），
  点中了通常直接就跳到那个词的结果页、不再多回一次车。这一版关键词因此**不是我们切的词，而是站点自己的叫法**，
  落进信号的 `suggested`，`_digest_prompt` 与 `_SURF_SYSTEM` 第 10 条据此告诉模型「这是站内真实叫法」。
  三条退路都如实写明：一条都认不出就照原词回车（**绝不为了一条联想把主题改成站点更想让我们搜的东西**）；
  联想项点不动（那种只有样式、没有点击处理的下拉）就把那个词 `fill` 回搜索框再回车，仍算站点给的词，连填都填不进就照原词搜；
  **没弹出联想下拉就一个字都不提**，绝不凭空造一条建议。`SURF_USE_SUGGESTIONS=false` 或没有主题词可认（`SURF_TOPIC_FOCUS=false`）时
  连下拉都不读一眼，退回「打完原词就回车」。
  **读到门口就退出来**（`SURF_AVOID_WALLS`）：真人看到「请先登录 / 开通会员才能看 / 请验证你不是机器人 / 页面不存在」
  是站着看一眼就走，不会把门上的话当内容读完。`_wall_reason` 判得保守——正文得**短到像一扇门**（≤600 字）且墙话出现在标题或
  开头，才认成 `登录墙`/`订阅墙`/`人机验证`/`维护/出错页` 四类之一（一篇长文章里顺口提一句「登录后可同步收藏」不算墙）；
  认成墙就给这条信号打上 `wall`、把 `excerpt` 收成门口那几句话（`_WALL_EXCERPT_CHARS`）并在 `warnings` 写明「没有绕墙，
  也没把墙上的话当成正文」，`_digest_prompt` 据此告诉模型「这一页被…挡住，没读到正文」，`_SURF_SYSTEM` 第 11 条要求
  **绝不拿它当内容证据出卡**；被挡的页**也不算「上几轮读过的页」**（没读进去就不装读过，下一轮还会再试），
  被挡页数汇总进「本轮浏览说明」。**绝不绕墙**：不试验证码、不猜登录 cookie、不买会员。关掉开关 = 一律照正文读（墙话也会进摘录）。
  `SURF_HOPS_PER_PAGE` 决定 `_page_links`+`_pick_hops` 每个页面从条目列表里挑几条**普通内容页**点进去读**一层**：
  视频链接交给看视频队列、图片/音视频/压缩包等**非内容直链**跳过、当前页与已读过的不重复点，**同域优先**，越界条数只计数并写进 `note`（不访问）。
  `SURF_TOPIC_FOCUS` 让 `_pick_hops` 在同域之内再像真人那样**先扫标题**：`_topic_tokens` 把主题切成词块（中文两字滑窗 + 西文按词，
  不引分词器与外部词表），`_relevance` 数一条标题认出几个词块（带年份/数量钩子只加分、不记名），认得出的排到前面点，
  并把**凭哪几个词点进去**落成 `picked_for`；一个都认不出时照旧按列表原顺序读，同时写一条 `note` 说明「是按原顺序顺路读的」，
  绝不假装条条都贴主题；关掉开关即退回纯 DOM 顺序（`_relevance` 一律给 0 分）。**同一套「扫标题」也决定点哪条视频看**：
  `_discover_video_links` 用 `_LINK_TEXT_HREF_JS` 把链接连标题一起读回（不再只看地址），按主题词排序后才交看片队列，
  于是 `SURF_MAX_VIDEOS_PER_SESSION` 的额度花在贴主题的标题上，视频信号同样带 `listed_as`/`picked_for`；
  一条视频的标题都没认出主题词时照旧按页面顺序看前几条，并另写一条 `note` 说明「这些是顺路看到的」。
  `SURF_LIST_PAGES` 让 `_surf_page` 像真人那样**接着刷列表后面的页**：`_click_next` 只点页面里真存在的「下一页/加载更多」控件
  （`a[rel=next]`、`:has-text('下一页')`、`加载更多`…），**绝不硬拼 `?page=2` 这种伪链接**；点之前用 `_page_fingerprint`
  记下「地址 + 正文字数 + 条目数」，点之后一模一样就判定**没真翻页**（无限滚动/已到末页）立刻收手，翻过去落到白名单外也立刻收手——
  三种情况都写进 `note`，翻成的页才落一条带 `list_page`/`paged_by` 的信号并继续从它挑条目。
  `SURF_PAGE_COMMENTS` 让**正文页也像真人那样读全文与评论区**：`_read` 先用 `_expand_collapsed` 点开「展开全文/展开更多/Read more」
  （不点只抓得到被截断的第一段，`expanded` 记点开了几处），再复用 `_collect_comments` 滚到底逐条抓评论（**网友原话**，
  抓不到就没有 `comments` 字段，绝不编造），`_digest_prompt` 把它渲染成「评论区：…」并交 `_SURF_SYSTEM` 第 7 条口径
  「是原话不是事实陈述」。`SURF_PAGE_GAP_MS` 让 `_read` 在**开新页之前**先 `_gap_like_human` 停一停（真人是「看一眼、想一下、再点」），
  会话第一页与同页继续读（搜索结果页、翻页后的列表页）不硬停，`gap_ms` 随信号记账。
  **读过的不重读**（`SURF_REMEMBER_READ`，跨会话浏览历史）：一轮浏览开始前先按**租户**读出「上几轮读过哪些页」
  （`read_history()` → `_gather_browser(read_before=…)`），`_harvest` 从信息流挑条目时先把读过的滤掉、只点没见过的新条目，
  `_queue_video` 也不再重复点进上几轮看过的视频页——**只拦顺路发现的**，用户在 `videoUrls` 里显式指定要看的**永远照看**（`via="direct"`）；
  有这份记忆时 `_discover_video_links` 把标题多扫几倍（真人是从更长的列表里挑没看过的那条），`SURF_MAX_VIDEOS_PER_SESSION`
  的额度才花在新鲜视频上（没有记忆时探测条数与旧口径逐字一致）。轮末 `remember_read_pages()` 只把**真读进去的**记下来
  （`kind` 为 `item`/`video` 的信号：读完的条目页与在线看完的视频页）——起点站首页、列表页、搜索结果页、失败页与 `note`
  都不算，**sample/http 通道整轮都不写**（没联网真读，就不装读过）；同一页再读只 `last_seen`/`reads` 递增、不重复记账，
  超过 `SURF_HISTORY_MAX` 先忘 `last_seen` 最旧的（真人也记不住几百个页面，忘了的会重新读）。
  三件事写进**同一条** `kind="note"` 的「本轮浏览说明」：跳过几条读过的条目、几条顺路发现的视频页上几轮已看过、
  这轮新记了几页（「下几轮会优先改读没见过的新条目」）；而**整版条目都读过时照样按页面原顺序重读**，
  只如实说明「没找到没见过的新条目」，此时**绝不写「跳过 N 条」**（没跳过就不谎报）。关掉开关 = 既不记也不跳，
  每轮都当第一次刷这些站；查看与清空走 `GET`/`DELETE /api/surf/history`（按租户隔离，删了就能让它重看）。
  每条信号带 `via` 轨迹（`seed` 起点站首页 / `search` 站内搜索结果 / `feed` 信息流点进 / `direct` 用户指定链接）与
  `listed_as`（列表里看到的标题，正文可能与它不符）、`landed_on`（goto 后被带到别的地址）、`list_page`/`paged_by`、`picked_for`、
  `query`/`search_tried`（站内搜索这一页时用的关键词，以及为它换过哪几版说法）、`suggested`（这一版词是站点弹出的联想里挑的）、
  `wall`（这一页根本没让读：登录墙/订阅墙/人机验证/维护页，摘录只是门口那几句话）、
  `scrolls`/`pauses`/`dwell_ms`/`gap_ms`/`expanded`——`_SURF_SYSTEM` 明确教模型按这些线索判来源与可信度（第 8 条：`picked_for` 标了的条目最贴主题、优先据此出卡，
  没标的只是顺路读读，不当作主题的直接证据；第 9 条：标了「站内搜索关键词」的是照着这些词搜出来的结果页，
  写明「换短的说法才搜到」的贴题程度最高；第 10 条：标了「站点联想」的那版关键词是站点自己的叫法、不是我们切的词，
  据此判断这个词在这站里到底搜得出什么；第 11 条：标了「这一页被…挡住」的信号**根本没读到正文**，
  绝不能拿它当内容证据出卡（冲浪手也不会绕墙）。
  **所有页面共用一个 `SURF_MAX_PAGES` 预算**（`_pages_per_session`）：起点的、搜索结果页、翻页翻出的列表后续页、点进的条目页、视频页各算一页，
  超出的条目**不再访问**，改为落一条 `kind="note"` 的「本轮浏览说明」信号（不进消化提示词，只由 `_finish` 折进会话 warnings 给人看）。
- **安全与诚实边界**：browser 只访问 `SURF_ALLOWED_DOMAINS` 白名单并复用 web.py 的 `_ensure_public_url` SSRF 拒绝（站内搜索跳到的结果页、翻页后的列表页、信息流点进的条目页都重新过一遍白名单+公网校验，越界即放弃）；**不下载在线视频文件**——下载不下来的视频就在线观看。
  未开看视频时，发现的视频链接只作**信号**记录（标题/链接）；开 `SURF_WATCH_VIDEOS`（仅 browser，`videoUrls` 亦视为要在线看）后**先扫标题决定点哪条看**，再按真人动作看片：
  先关遮罩/同意弹窗→`_click_play` 点开始播放（点不到再静音强推 `play()`）→`wait_for_function` 校验 `!paused`，**只有真在播才记 `played=True`**；
  播放中按 `SURF_VIDEO_FRAME_INTERVAL_MS` 逐段 `screenshot`（`SURF_CROP_TO_VIDEO` 只截播放器画面元素，含 iframe 内播放器；关则整页）共 `SURF_VIDEO_FRAMES` 帧，
  并记录每帧的 `currentTime`：一帧都不推进即 `stalled=True`（如实说明画面没动，不冒充看完整段）。
  截图按 `SURF_VLM_GROUP_FRAMES`（与网关单次图片上限取小）**一组一组**交 VLM（复用 `LLM_*`，需 `LLM_VISION`）：每组一次调用出 `segment_summary` + 画面文字 `on_screen_text`（兼做 OCR）；
  多组或页面上有文字信息时再做一次**纯文本汇总**，把逐组小结与标题/简介/UP主/播放量/评论合成整段结论。
  文字侧先走 DOM 直抓（`SURF_SCRAPE_COMMENTS`，先点「展开更多/查看全部评论」再滚动触发懒加载，评论按 `SURF_MAX_COMMENTS` 去空去重截断）；
  **DOM 没抓到标题/简介/评论时像真人那样截一张整页图（`SURF_CROP_TO_VIDEO` 在此让位整页）交模型逐字转写（截图 OCR，`purpose="SURF.videounderstand.ocr"`）**：
  转写**只填 DOM 空缺的字段**（真实 DOM 文字优先，`ocr_texts` 标出这份文字来自 OCR 而非抓取），离线/降级引擎给的「转写」**一律不采信**。
  **没配读图网关（`vision_enabled()` 不成立 = `LLM_VISION` 关闭，或引擎是离线/mock）时只截帧不交模型**，如实标 `vision_skipped=True`+`groups_used=0`、画面摘要自报 `simulated=True`；
  抽到 0 帧直接 `SurfUnavailable` 失败，单条视频失败只落 `video_error` 信号并写 warnings，绝不伪造看懂（逐帧理解另一条路径见 [§4 视频理解](#4-视频理解)）。
- **前置与接缝**：`POST /api/tasks/{id}/surf`（建会话，body 可带 `videoUrls` 指定要在线看的视频页）、`GET .../surf`（列表+惰性推进）、`POST /api/surf/session`（无任务囤灵感）、
  `GET /api/surf/pool`、`DELETE /api/surf/pool/{card_id}`、`POST .../surf/apply`；主题为空且无 Brief/无 `SURF_TOPICS` 则 409。
  浏览记忆的接缝是 `GET /api/surf/history`（回 `remember`/`max`/`items`，`SURF_REMEMBER_READ=false` 时 `items` 恒空——
  没记就是没记，不拿别处的数据凑）与 `DELETE /api/surf/history`（清掉本租户的，下一轮就当第一次刷这些站）。
  `GET /api/settings` 附带 `surf.watchReady`（Playwright+白名单是否就绪）与 `surf.visionConfigured`（读图网关是否就绪），UI 据此提示「能不能真看懂」。
- **成本**：`sample`/`auto` 归 0；`browser`/`http` 的浏览本身不计费（自建/本机；站内搜索与信息流多跳只增加**页数**，
  消化仍是一次 LLM 调用，且进提示词的信号按 `_MAX_SIGNALS_FOR_DIGEST=40` 截断），消化按一次 LLM 调用估算（仅真非本地网关）；**看视频按模型调用次数计**：
  每个视频 = `ceil(SURF_VIDEO_FRAMES / 每组张数)` 次逐组调用（多组再 +1 次汇总，开 `SURF_SCRAPE_COMMENTS` 再 +1 次整页截图 OCR），
  仅在 `vision_enabled()` 时按 `SURF_MAX_VIDEOS_PER_SESSION` 上限保守预估，一并纳入 `COST_BUDGET_USD` 熔断。
- **后台自主冲浪**：`server.py` 仿 `_publish_loop` 起 `_surf_loop`，仅 `SURF_AUTO_ENABLED` 为真时按 `SURF_TICK_SECONDS` 调
  `autonomous_tick()` 按 `SURF_TOPICS` 各囤一轮（默认关闭以保持进程零副作用、无外网连接）。span：`surf.digest`、`surf.videounderstand`。

---

## 10. 回归口径

这些通道全部在离线门禁里被真跑：`python scripts/doctor.py`（清单、惰性推进、帧与包落盘、租户隔离、
真人式浏览的正反向下限——随机节奏 vs 机器节奏、站内搜索成功与「没搜索框就不搜」、点「下一页」真翻到第 3 页与
「没控件/点了没变/页数用尽」三种如实收手、信息流多跳只点同域、扫标题挑条目（贴主题的排在前且记下凭哪几个词点进去 /
整版认不出就按原顺序读并如实说明 / 关掉开关即退回列表顺序 / 要在线看的视频同样按标题挑，单会话只看 1 条时看点哪条即由此定）、
搜不对题就换个更短的说法再搜（`_query_variants` 的切法、清空搜索框重打、只读贴主题那版结果、再没更短的说法就照旧读最后一版、
`SURF_SEARCH_RETRIES=0` 时绝不偷偷多搜一轮）、打字时也用站点弹出的搜索联想（`_pick_suggestion` 只挑认得出主题词的那条、同分保持站点顺序、
点中即不再回车并把这一版词记成 `suggested`、只读回前 N 条、一条都认不出就照原词回车且不写 `suggested`、联想项点不动就退回打回搜索框再回车、
关掉开关连下拉都不读、这站根本没弹下拉时说明里一个字都不提「联想」）、读到门口就退出来（`_wall_reason` 认出登录/订阅/人机验证/维护
四类墙且**长正文里提一句「登录」也不误判**、墙上原话只当门口摘录不冒充正文、汇总说明有几页被挡住、消化提示词写明「这一页被…挡住」、
`SURF_AVOID_WALLS=false` 时照旧当正文读、**被挡的页不写进「读过的页」**）、读过的不重读（给一份「上几轮读过」的记忆就改读没见过的条目 /
整版都读过仍按原顺序重读并如实说明「没找到没见过的新条目」且**不写跳过** / 看过的视频不重复点进、但用户指定的链接照样看 /
只记真读到的 item·video 页且按租户隔离 / 同一页再读只加遍数不重复记账 / 超 `SURF_HISTORY_MAX` 先忘最旧的 /
关掉开关既不记也不跳 / 清掉即归零——存储侧直接跑在 doctor 的临时 `CREATOR_DATA_DIR` 上）、
展开全文与页面评论区按开关分野、页间停顿随机且第一页不硬停、
`SURF_MAX_PAGES` 共用预算截断、越界/环回链接被放弃并写进说明、以及失败与熔断的反向断言——全部用假 Playwright 页面跑，零外网零安装）
与 `python scripts/verify_contracts.py`（真实服务下的路由状态码与包内容：样例会话推进到 done、灵感全部自报占位并沉淀进灵感池、
选卡注入 Brief 幂等且不堆叠、空 cardIds→409 / 池里没有→404，`GET /api/settings` 的 surf 快照必须带「挡墙」开关（前端才有地方关它），
以及 `GET`/`DELETE /api/surf/history` 的契约——
**sample 通道跑完后浏览历史必须还是空的**，不拿占位信号冒充读过的页）。
新增或修改通道时，**先补这两处断言再改界面**——样例的价值就在于换厂商前就能被验证。
