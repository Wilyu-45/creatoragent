# 开发记忆 · Creator Agent Studio

**读者**：接手本项目的开发工程师。**回答**：还有哪些事没做、哪些设计是刻意为之（别乱改重来）。
**不负责**：需求定义（见 `creator.md`）、技术方案与选型取舍（见 `plan.md`）、使用与配置操作（见 `USER_GUIDE.md`）、
工程约定与历史教训（见 `ENGINEERING_PRINCIPLES.md`）、存储替换契约（见 `storage_contract.md`）。

> 上线前人工事项的权威副本见 `README.md`；本文保留一份供开发侧对照。

---

## 1. 项目定位与开发速查

编排式多智能体创作台：把一份营销 Brief 经「策略 → 创意 → 策划 → 文案 → 审校 → 事实核查 → 合规 → 人工审批」
流水线产出可交付内容，带质量门禁与合规红线。11 个智能体 A1–A11；A0 总控职责由编排器承担。

| 项 | 选型 |
| --- | --- |
| 后端 | Python 3.12 + FastAPI + LangGraph（conda env `multi-agent-creator`） |
| 模型 | OpenAI 兼容 Provider（httpx）+ 内置 Mock，无密钥也可完整跑通 |
| 前端 | React 19 + TypeScript + Vite，构建后由后端静态托管 `dist/` |
| 持久化 | 默认 file（JSON + SQLite）；`CREATOR_STORAGE=pg` 切 PostgreSQL + Redis |

开发时最常敲的几条（完整验证入口见 `README.md`）：

```bash
conda run -n multi-agent-creator python -m app.main            # 后端（默认 8787）
conda run -n multi-agent-creator python scripts/doctor.py      # 环境 + 智能体链路自检
conda run -n multi-agent-creator python scripts/smoke_api.py   # 端到端验收（真实 uvicorn + /api 契约）
conda run -n multi-agent-creator python scripts/stress_llm.py -n 12 -c 4  # 压测：mock 查并发 / openai 量真实成本
npm run build && npm run dev:web                               # 前端构建 / 仅前端开发
```

关键配置键：`CREATOR_DATA_DIR`（持久化根目录，测试与多实例隔离）、`CREATOR_STORAGE`、`CREATOR_API_TOKENS`、
`OTLP_ENDPOINT`；其余见 `.env.example`。

---

## 2. 非显然的设计决策（别乱改）

> 这些决策「看代码看不出来、后人容易推翻重来」。改之前先读理由；对应的踩坑教训见 `ENGINEERING_PRINCIPLES.md`。

**编排与契约**

1. **`interrupt()` 放 escalation / approval 节点第一行**：LangGraph 恢复中断时会从头重放节点，落盘、发布等副作用只能在拿到裁决后执行一次。
2. **续跑用 `invoke(None)`**：让 LangGraph 从检查点继续；只有新任务才注入初始状态。
3. **SPA 回落路由显式排除 `api/` 前缀**：`/api/*` 未命中必须保持 404，不能被前端页面顶掉。
4. **前端契约类型自持于 `src/lib/types.ts`**（不 import 后端），与后端逐字段对齐；改契约要同步前端联合类型，否则 typecheck 直接报错。

**记忆库与检索**

5. **检索只在编排层做一次**：`Orchestrator._memory_hits()` 在 `MEMORY_RECALL_AGENTS`（A1/A2/A4）执行前统一检索并注入 `ctx.memory`，智能体只消费 —— 各写一份检索口径必然漂移。
6. **召回必须 `exclude_task=task.id`**：否则 A11 会召回自己刚写入的卡片，变成自我循环的「假复用」。
7. **不引入专用向量库**：MVP 主场景是「同品牌历史资产复用」，元数据加成 + 2-gram 已够用；接口按 vector-ready 设计（`retrieve(query, …, top_k) -> list[MemoryHit]`），日后替换上层无需改动。
8. **混合权重必须能退化**：`weight=0` 即纯关键词分、与旧行为逐位一致，让「上向量」成为可回滚开关而不是一次性替换。
9. **本地 embedding 用确定性 hashing**（blake2b 符号哈希 + L2 归一化，`app/knowledge/embedding.py`）：零依赖、同一文本永远同一向量，测试才敢断言分数。
10. **去重键与容量都按租户**：去重键 `sha1(tenant|kind|title|content)` 保证同租户幂等、跨租户各自存活；`MAX_CARDS` 按每租户计量，否则先入库的租户会挤光后来者的名额。

**评估、回归与追踪**

11. **门禁是否决式、评估是度量式**：默认 `JUDGE_MODE=advisory` 只记录不改裁决；`judge.weight` 默认 0.2（=0 时与历史行为逐位一致）—— 把度量塞进门禁会把评分噪声放大成流程抖动。
12. **评估的合规维度复用 A7 词库**（`scan_compliance`）：同一事实只允许一个判定来源，自己再写一遍迟早出现「A7 判通过、评估说有问题」。
13. **评估口径带版本号**（`RUBRIC_VERSION`）：口径不同先拒绝跨版本比较，而不是默默算出一个差值。
14. **黄金数据集 runner 抽到 `app/core/golden_runner.py`**：Web「跑回归」与 CLI 必须共用同一段执行与判定逻辑，脚本只管进程编排与退出码。
15. **回归是后台任务，不挂在 HTTP 请求上**：`/api/golden/run` 立即返回 202，执行在后台线程，状态由 `RunState` 聚合轮询；运行中再触发返回 409 而不是排队（排队会掩盖双触发）。
16. **数据集覆盖口径选「分支覆盖」而非「组合覆盖」**：每渠道特化分支 + 每行业规则组至少一条用例命中，避免「渠道 × 行业」全铺拖慢回归。
17. **采样是 trace 级决策，且只作用于导出面**（OTLP + 落盘）：同一棵树要么全导出要么全不导出；未采样的 trace 不转发、不落盘，但进程内轨迹始终完整。
18. **跨进程用 W3C `traceparent` 传播**：入站 `POST /api/tasks` 沿用远端 `trace_id` 并把根 span 挂在其下，出站 webhook 自动携带；坏头一律忽略，绝不影响业务请求。

**离线引擎、多语言与存储**

19. **mock 离线引擎必须覆盖新语言与新产物**：默认 `LLM_PROVIDER=mock`，若 mock 只会中文，新能力在 CI 与自检里就是假的。
20. **本地化要「原生创作」而不是翻译**：提示词显式写明「不要先写中文再翻译」，翻译腔文案在本地市场不可用。
21. **字数口径按语言**：`title_measure()` 英文按词、中日韩按字，交付标题压缩同口径 —— Instagram 的 12 词上限按字符算会得出完全错误的结论。
22. **双存储后端且默认 file**（`CREATOR_STORAGE=file|pg`）：file 模式零外部依赖，pg 实现放在条件分支里延迟 import（doctor 有反向断言）；切换步骤与替换契约见 `storage_contract.md`，迁移与专项回归用 `scripts/pg_migrate.py`（幂等）/ `scripts/pg_check.py`。
23. **pg 连接池必须 `autocommit=True`**：`PostgresSaver.setup()` 的 `CREATE INDEX CONCURRENTLY` 不允许在事务块内执行；代价是多语句原子性要显式 `conn.transaction()`。
24. **测试临时目录一律走 `make_temp_dir()`**（逐级 `mkdir` + 随机后缀），不用 `tempfile.mkdtemp`：`0o700` 在受限环境下会让落盘与 SQLite 静默失败，而自检仍然全绿。
25. **数字人渲染不在本系统内实现**：授权、形象库、计费与回调协议差异极大，「接哪家」是产品形态决策。系统只提供 `sample` 内置引擎与 `http` 适配样例（未配置 `DIGITAL_HUMAN_API_URL` 时显式失败，绝不假装成功），让 API / UI / 下游联调先于采购发生。
26. **界面设置里只有两类落盘项**：`PUT /api/settings` 整体是纯内存（重启即丢），仅 `search.site_urls`（站点监控）与 `llm.agent_models`（每智能体模型覆盖）持久化到 `data/settings.json` 并在启动时回读——两者都是用户精心维护的资产，不能丢；其余设置保持「即时生效、重启回退 env」的轻行为。settings 不在 `storage_contract.md` 的 PG 实体清单内，pg 模式同样走本地文件。
27. **放开全局编排预算要同步强监管用例的返工期望**：黄金用例的 `expect.max_revisions`（golden/briefs/*.json）与编排侧 `MAX_REVISIONS`（全局）是两个口径——编排读全局、断言读用例。全局放宽后 mock 在强监管用例会第 3 轮才收敛，「返工轮次 ≤ 2」断言随之失败，属口径漂移而非质量回归（同步用例期望即可，勿误判成 Prompt 退化）。
28. **「模型返回空内容」按瞬态可重试**：real_check 实测 deepseek-flash 会间歇返回空 content，命中即降级离线引擎会连锁引发 A6「无来源」返工、拉低全任务质量分；故纳入 `_RETRIABLE_RE`（engine.py）重试，3 次仍空才降级；400 等参数错误仍不重试。
29. **本地推理端点计 0 元但不跳过记账**：Ollama / LM Studio / vLLM 等私网端点（engine.py `_is_local_endpoint`）硬件归用户、无云单价可依，`cost_guard.charge` 仍必须调用、成本传 0——charge 同时驱动 token 熔断，跳过会让 token 预算彻底失效；span 与 `AgentMetrics.cost_usd` 同口径记 0（`LLMResponse.local`）。

**素材研读与长文分篇（core/digest.py + A4 分篇）**

30. **研读「降级」看 `degraded_reason` 而不是 `simulated`**：mock 引擎常态就是 simulated=True，拿它判定会把每次离线自检误报成「研读已降级」；只有熔断/引擎回退才置 degraded_reason（digest.py 单点判定），simulated 只做 sources 的诚实标注。
31. **新增 Artifact type 要过四处契约**：`core/types.py` 的 Literal + `ARTIFACT_LABEL`、前端 `src/lib/types.ts` 联合类型、`ArtifactViewer` 标签——漏 Literal 会在 A0 写黑板时被 Pydantic 判死整个任务（真实踩坑：`document_digest` 首跑 failed）；doctor 第 19 项已加「研读产物过黑板写入」断言守护。
32. **A4 分篇的字数区间取「上界最大的一组」**：`_WORD_RANGE_RE` 扫 constraints+deliverables，期望交付物写「合计 8000-10000 字」会被当成每篇字数 → prompt 撑爆；合计口径要写「约 N 字」。
33. **`PUT /api/settings` 的 patch 白名单与 `config.update_config` 必须同步扩**：只扩后者会出现「设置返回成功但值不生效」（`digestMaxCalls` 曾漏，白名单在 routes.py）。
34. **研读切块配额「每文档保底 1 块 + 按字数比例」**：按「全文统一块长 + 按文档顺序截断」会让长文档吃光 `digestMaxCalls` 配额、把排在后面的短文档（往期成稿、风格 skill）整份挤出研读——真实链路踩过（6 份素材 16 块，前三卷杂谈全部落空）。配额少于文档数时按字符数保长文档，落选文档如实计入 dropped。
35. **导出拼装时 cta 与正文结尾同句则不重复拼接**：模型常把收尾句同时写进正文结尾和 cta 字段（真实链路「咱们下回再见。」连出两次），忽略尾部标点差异后判重。同理：新加的自检断言必须实跑全绿再报完成——研读写黑板检查曾因 `Artifact.id` 默认空串而从未真正通过过（doctor 构造时须传 `new_id("art")`）。

**部署与监听地址**

36. **`HOST` 默认 127.0.0.1，容器清单显式覆盖 0.0.0.0**：默认绑 loopback 是安全默认
    （配合反向代理，样例在 `deploy/` 下）；容器端口发布则要求监听非 loopback ——
    绑错时容器内健康检查照常通过、宿主机连不上，自检 / 冒烟全部绿，只有跑真容器才暴露。
    `check_deploy.py` 已把三处清单声明升级为断言；改绑定相关代码前先读 `app/main.py` 与部署清单。

**图片/视频生成接入样例（core/imagegen.py + core/videogen.py）**

37. **图片/视频作业走旁路 job store、不新增黑板产物**：新增 Artifact type 要过四处契约（见 #31），风险高且本次能力本质是「旁路异步作业」而非创作链产物；改用与 `digital_human.py` 同构的 `gen_jobs.py` 作业后端（POST 建作业/GET 查状态），绕开四处契约同步，前端用独立面板（ImageGenPanel/VideoGenPanel）展示。
38. **生成通道必须按「作业实际生效的 provider」分派、不按全局配置**：请求体可覆盖 provider，而全局 `get_config().xxx.provider` 仍是默认值。两处同根 bug（真实踩坑）：`videogen._estimate_cost` 读全局会漏算成本绕过熔断；`imagegen._submit_real` 读全局会在「全局=sample + 覆盖=openai」时误走 local 分支。两者均改为用 `job["provider"]`/chosen 参数。
39. **视频只有 sample/http 两条通道**：建任务/查状态本身就是统一异步 http 契约，本地 ComfyUI 农场经 http 指向本地端点即可覆盖（`_is_local_endpoint` 计 0 元，见 #29），无需像图片那样单列 `local` provider。
40. **新能力先过 sample 通道 + 黄金新用例不破坏基线**：`golden_eval.compare()` 中 `previous is None → verdict="new"` 在内容级检查之前跳过，新增 B 站解说用例不会触发 regressed、不需 `--update-baseline`（避免重写整个版本化资产固化回归）；ok 只看 regressed/failed/missing。
41. **二创版权只给 checklist + 强制 `needs_human_review`**：`knowledge/copyright.py` 不假装能自动检测侵权（镜像 #非中文词库的降级哲学），命中解说类才叠加，A7 把 items 转 hits 并置 `needs_human`，最终人工把关。
42. **openai 图片返回只有 `b64_json` 时必须落盘 assets**：OpenAI 默认 `response_format` 与多数兼容网关会返回 base64 而非 url；`imagegen._openai_image` 无 url 且有 b64_json 时走 `_persist_data_image` 落盘再引用（与 local 通道对齐），否则生成的图会被丢弃、前端拿到空 url（doctor 已断言 b64→assets 与直连 url 透传两种路径）。
43. **视频理解是生成侧的镜像、走旁路 job store 不新增产物**：`videounderstand.py` 与 imagegen/videogen/digital_human 同范式（复用 `gen_jobs.py`、惰性推进、span、租户隔离、删除回收），同样避开四处契约（见 #31/#37）。它是 `digest.py`（文档 map-reduce 理解）的画面版镜像：输出「视觉摘要」而非「画面」。**摘要不新增 Artifact type而是经「注入 Brief」显式接缝（把 `summary.text_brief` 幂等追加进 `brief.constraints`）喂给下游 A2/A3/A4**，避免落黑板。
44. **视频理解 real = 原生视频理解网关（整集一次调用），不依赖本机 ffmpeg、不改共享 LLM 层**：`videounderstand.py` 把**整集视频**交给**原生支持视频输入的理解网关**（`VIDEOUNDERSTAND_API_URL`），一集=一次调用、成本可控、最贴「逐集理解再创作」节奏；与 videogen 同一「POST 提交整集 → GET 轮询 → 取回摘要」异步契约。**不抽帧、不调 chat/ImagePart**（故不再依赖本机 ffmpeg、也不再走多模态读图路径）；厂商私有协议（Gemini 视频输入/自建农场）由网关层消化、不绑厂商。
45. **real 失败链把「未配网关（`api_url` 空）」置最前，保证 mock 门禁下确定性失败且不触网**：`_submit_http` 开头依次检查 api_url 空 / video_source!=local / `_resolve_video_path` 文件缺失——均在 `httpx.post` 之前，故未配网关/非本地/缺文件都确定 failed、绝不发网络请求、doctor 可稳定断言不假装。（这取代了旧「抽帧喂图时代把 vision_enabled 放最前」的设计：现已无 ffmpeg/vision_enabled。）
46. **本地大视频不能套 `assets.load_local` 的 8MB 闸门**：`load_local` 的体积上限是为「进模型的字节」设的，整集番剧视频远超此值会被 `parse_asset` 提前返回（kind 保持默认 image）→ 若依赖 asset.kind/usable 检测视频会失败。videounderstand 自写 `_resolve_video_path`（复用 load_local 的相对路径/拒绝绝对路径与 `..`/限 `assets/` 的安全不变量，但**不读字节、不限体积**，只把路径/引用交给网关自取、不搬运大文件）+ 按**扩展名**兑底识别视频。
47. **桌宠 `sample` 通道必须真落盘 PNG，不是只出清单**：`petgen` 用标准库（zlib + struct + crc32）编码 PNG、程序化绘制吉祥物帧，并复用图片通道的 `IMAGEGEN_*` 端点做 `imagegen` 通道（端点契约见 `capability_samples.md` §2）（一帧一次调用、**不新增端点/密钥配置**）。理由：「交付物是可运行的宠物包」这一形态本身要能被回归（doctor 断言帧数、PNG magic、zip 内含运行器），只在内存里编一个清单会把最该验证的部分留在门禁之外。顺带两条口径：分派只看 `job["provider"]`/`chosen`（承 #38），而 `_image_channel_usable()` **故意**读全局 `image_gen` 配置——它回答的是「共享图片端点配了没」而不是「该作业走哪条通道」；逐帧计费在受理前按帧数预估，超 `COST_BUDGET_USD` 则 `submitted=false`、`attempts=0`。
48. **桌宠不做图像处理：纯色键 `#FF00FE` + 双端各自透明**：Tk 桌面侧用 `-transparentcolor`，浏览器预览侧逐像素把同色置 alpha 0——同一色键两边复用，故不引入抠图/切帧/对齐链路（也就零 Pillow 依赖）。附带约束：调色板解析要把与色键冲突的颜色挪开（`_avoid_key`），否则帧会被自己吃掉。
49. **宠物包内嵌 `runner/pet.py`，读不到源码时写 `runner: ""` 而不是静默缺失**：包要脱离仓库也能跑（PyInstaller 冻结环境与部署副本都没有 `app/desktop_pet.py` 源码路径）。可用 `runner` 字段是否为空来判断运行器是否内嵌；打包 zip 不写死绝对路径，降级必须在 `pet.json` 里可见。
50. **技能提炼：统计口径本身就是产品主张，改口径等于改卖点**：`skillgen.py` 的 `rules` 通道卖的是「数字来自正文的**真实统计**」，三个口径都必须守住，否则产物看着像技能其实是噪声——① 断句用 lookbehind `(?<=[。！？!?；;])|\n+` **保留句末标点**（若把标点吃掉，`question_ratio` 恒为 0，「优先疑问句开场」就永远统计不出来）；② 字数一律走 `_visible_len`（非空白字符，与文案编辑器同口径；用 `len()` 会把换行缩进算进字幕字数，语速虚高到 300+ 字/分钟）；③ 字幕**只认整行以时间戳开头**的 `-->` 行且需 ≥2 条（`digest` 研读摘要会把 `00:00:00,000 --> …` 写进句子，一旦被当成一条巨型字幕，该作品的段落数与语速统计会一起失真——doctor 有这条反向断言）。另外两条口径：作品门槛（正文 <40 字）与上限（`SKILLGEN_MAX_WORKS`）都**写进 issues、连 409 文案一起带出**，绝不静默丢弃；`simulated=true` 是诚实标注而非缺陷——数字是真的，但「为什么这样写有效」属风格判断，只有 `llm` 通道才声称带判断力。技能目录不变量由 `skill_dir_of()` 守（`dir` 须以 `skills/` 开头且父目录 == `SKILLS_DIR`），`build_package()` 装配时要排除 `package.zip` 自身，否则重复打包会层层嵌套。
51. **配音是真实功能：引擎通道用后台 worker 逐句真合成，不是 sample 那种惰性时间推进**：`dubbing.py` 的 `sapi/sovits/elevenlabs` 受理后由 `_spawn_worker` 起线程逐句合成并**立即落盘**（`.part` 改名原子写），`_WORKERS` 集合防重、`_refresh` 读到无活体 worker 的作业就复活——进程重启后续跑的依据是 `seg["audio_ref"]` 非空（已合成的句子没有重跑的理由，克隆音色 `voice_id` 也持久化在 `job["clone_voices"]`）。分派只看 `job["provider"]`（承 #38），`auto` 的择优结果与回退理由写进 `manifest["warnings"]`，绝不默默降级。
52. **sapi 的语种必须匹配文本，且静音要用测量揭穿**（真实踩坑：首轮端到端 4 句全是 46 字节 wav，只有文件头）：英文音色读中文不出声，故枚举音色要连 `Culture` 一起拿（`Name|Culture`），台词含 CJK 时**只在 `zh-*` 音色**里选，本机无中文音色直接 `DubbingEngineError`——宁失败不交静音成品。测量口径两条：① wav 用标准库 `wave` 实测（`frames/rate`），`frames=0` 也照实返回 `0.0s`（测得出静音比「测不出」诚实），合成后 `duration<=0` 或 `size<200` 判失败；mp3 按 128kbps CBR 粗估并标 `estimated=True`；② 整轨拼接比较 `getparams()` **必须先 `_replace(nframes=0)`**，否则每句帧数必然不同 → 恒判「参数不一致」而拼不出 full.wav。本机无 ffmpeg（`imageio_ffmpeg` 不可用）也是这两条走标准库的理由。
53. **合成音频只走带鉴权路由、引用三段式定死边界；不做克隆的通道必须自报**：音频落 `data/dubbing/<job_id>/`（**不放静态目录**），读取 `GET /api/tasks/{id}/dubbing/{job_id}/audio?ref=` 需租户校验 + `ref` 形如 `dubbing/<作业id>/<文件>`（拒绝对路径与 `..`、文件名 `[\w.-]+` 不以点开头）且**引用里的作业 id 必须等于路由已鉴权的作业 id**；`drop_task` 回收目录前还要比对 `parent == data/dubbing`，不放开任意删除。诚实标注三条：`sapi` 汇总固定追加「未消费参考音频做克隆」、粗估段数进 issues、`_estimate_cost` 按通道计费（本机 `sapi`/自建 `sovits`/`sample` 归 0，`elevenlabs` 按字符，外部 `real` 网关按句）。
54. **跨语言译制配音：翻译复用 `LLM_*` 网关、句序必须 1:1、占位译文必须自报**：`dubbing.py` 的 `translate_manifest` 在受理时同步把台词逐句译成 `target_lang`（复用系统既有 LLM 网关，与 skillgen 的 `llm` 通道同一「不另持端点与密钥」取舍）——原句存 `seg["source_text"]`、`text` 换成译文，时间轴 txt 原文/译配对照。三条硬口径：① **句序 1:1**——网关返回与台词句数不齐（按 `index` 或位置对齐后仍缺句）直接抛 `DubbingTranslateError` 受理即失败并点名缺哪句，绝不拿原文顶替、绝不静默丢句；② **`translation_simulated` 自报**——`response.simulated` 或有 `degraded_reason`（离线引擎/降级）时译文是 `[lang] 原句` 占位串，写进 warnings + `manifest` 字段，绝不冒充真实译文（要真译制必须配真实 LLM 网关）；③ **成本同口径**——`_estimate_translate_cost` 仅在 `llm.provider==openai` 且非本地端点时按源文本字符估算（离线/本地翻译归 0），与 `_is_local_endpoint` 一致。`target_lang` 为空或等于粗判 `detect_language` 的源语种时不翻译（直接配原文）；`sovits` 译制时按 `target_lang` 传 `text_lang`。前端 `targetLang` 走 `POST /dubbing` 请求体，语种清单由 `GET /api/dubbing/languages` 提供。
55. **灵感冲浪五条硬口径（旁路作业范式的新实例）**：`surf.py` 像真人上网刷信息/看视频→经系统 LLM 网关（`purpose="SURF.digest"`）消化成灵感卡，严守五条：① **Playwright 惰性可选、诚实失败**——`browser` 通道**惰性** `from playwright.sync_api import sync_playwright`，未装/未配白名单即受理即 `failed` 并点名 `pip install playwright && playwright install chromium` 与 `SURF_ALLOWED_DOMAINS`/`SURF_SEED_SITES`，绝不假装配浏览过（Playwright 连注释行都不写进 `requirements.txt`，保默认零依赖离线）；② **`allowed_domains` 白名单安全**——browser 只 navigate 白名单域内公开站，复用 `web.py` 的 `_ensure_public_url` SSRF 私网/环回拒绝；③ **`simulated` 自报 + 不假装看懂视频**——`sample`（默认）离线产确定性占位灵感自报 `simulated=True`；发现的视频链接默认只作**信号**记录（标题/摘要/链接）；④ **在线看视频=真人动作，不下载文件**——`SURF_WATCH_VIDEOS` 或会话带 `videoUrls` 时（仅 browser）：关遮罩→**点开始播放**→`wait_for_function` 校验 `!paused` 才记 `played=True`→播放中逐段截图（`SURF_CROP_TO_VIDEO` 只截播放器元素，含 iframe）→按 `SURF_VLM_GROUP_FRAMES` **一组一组**交 VLM 出逐组小结（兼做画面文字 OCR）→多组再做一次纯文本汇总（并入 DOM 抓的标题/简介/评论，评论按 `SURF_MAX_COMMENTS` 去空去重）；**DOM 抓不到页面文字时再截一张整页图交模型逐字转写（截图 OCR）**——转写只填 DOM 空缺的字段（真实 DOM 文字优先，`ocr_texts` 标明来源），离线/降级给的「转写」一律不采信；**没配 `LLM_VISION` 读图网关就只截帧不交模型**，标 `vision_skipped=True`/`groups_used=0` 自报 `simulated=True`，0 帧直接 `SurfUnavailable`，单条视频失败落 `video_error` 信号写 warnings，绝不伪造看懂；成本按**模型调用次数**（`ceil(总帧数/每组张数)` + 1 次汇总 + 1 次整页 OCR 兜底）× 每会话视频数纳入 `COST_BUDGET_USD` 熔断；⑤ **真人式浏览（仅 browser）= 随机节奏 + 站内搜索 + 信息流多跳，共用页数预算且处处留痕**——`SURF_HUMAN_PACE` 用**会话级确定种子** `f"surf:{topic}|{起点的 URL}"` 驱动 `_dwell`/`_scroll_like_human`（每轮滚动幅度与停留随机浮动、约 1/5 概率原地停一停不滚；关掉即等距滚+固定等待的机器节奏），每条文本信号落 `scrolls`/`pauses`/`dwell_ms` 供核对；`SURF_SEARCH_ON_SEEDS` 用**起点站自己的搜索框**逐字打字（`press_sequentially`，失败退回 `fill`）→回车/点提交→等 load→再滚两轮读结果，**没搜索框 / 点不动 / 填不进 / 回车与按钮都没触发 / URL 未变 / 结果页越界**六种情况一律**如实不搜**（退回起点站继续刷、原因写进 `note`，绝不假装搜过）；`SURF_HOPS_PER_PAGE` 由 `_page_links`+`_pick_hops` 从当前页条目里挑**同域普通内容页**点进去读一层（视频链接转看片队列、图片/音视频/压缩包等非内容直链跳过、当前页与已读过不重复、越界条数只计数并披露不访问）；每条信号带 `via` 轨迹（seed/search/feed/direct）+ `listed_as`（列表里看到的标题，正文可能与它不符）+ `landed_on`（goto 后被带到别处），`_SURF_SYSTEM` 明确教模型按这些线索判来源；**起点页/搜索结果页/点进的条目页/视频页共用一个 `SURF_MAX_PAGES` 预算**（`_pages_per_session`），超出的条目不再访问，改落一条 `kind="note"` 的「本轮浏览说明」——**不进消化提示词**（`_digest_prompt` 跳过 note、信号按 40 条截断），只由 `_finish` 折进会话 warnings 给人看；浏览只增加页数不增加模型调用次数，消化仍是一次调用；⑤b **接着刷列表/读全文/看评论区也是真人动作**——`SURF_LIST_PAGES` 让 `_surf_page` 点**站点自己的**「下一页/加载更多」控件翻列表（`_click_next` 只认页面里真存在的 `a[rel=next]`/`:has-text('下一页')`/`加载更多`，**绝不硬拼 `?page=2` 伪链接**），点前后用 `_page_fingerprint`（地址+正文字数+条目数）比对，**没控件 / 点了页面没变（无限滚动或末页）/ 翻过去落到白名单外**三种情况一律立刻收手并写进 `note`，翻成的页落带 `list_page`/`paged_by` 的信号且继续从它挑条目、同样吃 `SURF_MAX_PAGES` 预算；`SURF_PAGE_COMMENTS` 让正文页先 `_expand_collapsed` 点开「展开全文/Read more」（不点只抓得到截断第一段，记 `expanded`）再复用 `_collect_comments` 逐条抓**网友原话**（抓不到就没有 `comments` 字段，**绝不编造评论**），`_digest_prompt` 渲染成「评论区：…」且 `_SURF_SYSTEM` 第 7 条要求把它当**观点素材而非事实**写卡；`SURF_PAGE_GAP_MS` 让 `_read` 开新页前先 `_gap_like_human` 停一停（「看一眼、想一下、再点」），**会话第一页与同页继续读（搜索结果页、翻页后的列表页）不硬停**，`gap_ms` 随信号记账；⑤c **挑条目先扫标题认主题词**——`SURF_TOPIC_FOCUS` 让 `_pick_hops` 在同域桶内按 `_relevance(标题, _topic_tokens(主题))` 排序（`_topic_tokens` 中文两字滑窗 + 西文按词，不引分词器也不引外部词表；标题里的年份/数量只加分、不记名为「主题词」），认得出主题词的先点并把命中的词落成 `picked_for`，`_digest_prompt` 渲染成「因标题含…点进」且 `_SURF_SYSTEM` 第 8 条要求据此判贴题程度（没标的只是顺路读读，不算主题的直接证据）；**整版一个都认不出仍照列表原顺序读**，只额外落一条 `note` 说明是顺路读的，绝不假装条条都贴主题（关掉开关即退回纯 DOM 顺序）；**同一套扫标题也决定点哪条视频在线看**——`_discover_video_links` 用 `_LINK_TEXT_HREF_JS` 把链接连标题一起读回、按主题词排序后才交看片队列，
视频信号同样带 `listed_as`/`picked_for`，一条标题都没认出就按页面顺序看并另落 `note` 说明是顺路看到的；
**搜不对题就换个更短的说法再搜**（`SURF_SEARCH_RETRIES`）——`_query_variants` 只会把本次主题切短（整句→按标点切开的词组→最前面一两个词，
**绝不凭空造同义词**），`_results_look_relevant` 扫结果页标题判贴不贴题，不满意就先 `fill("")` **清空搜索框**再打下一版（不清就把两个词粘在一起），
换到贴题为止；再没有更短的说法就照旧读最后一版并由既有的 `note` 如实说明。`query`/`search_tried` 把搜过的每一版关键词全记在信号上，
没搜到对题的东西就绝不写「才搜到」；`SURF_TOPIC_FOCUS=false` 或 `SURF_SEARCH_RETRIES=0` 退回「整句搜一次就完」。
**打字时也瞄一眼站点弹出的搜索联想**（`SURF_USE_SUGGESTIONS`，一次最多读回 `SURF_SUGGESTIONS_MAX` 条）——`_read_suggestions` 按候选选择器读回下拉里的候选词
（保持站点给的顺序、去空去重，读不到就是这站根本没弹联想），`_pick_suggestion` 用挑条目那同一只眼（`_relevance`）挑出**认得出本次主题词**的那条点下去
（同分时保持站点顺序——站点排序本身是信息；点中通常直接就跳结果页、不再多回一次车），于是这一版关键词是**站内的真实叫法**而不是我们切的词，
落进信号的 `suggested`，`_digest_prompt` 与 `_SURF_SYSTEM` 第 10 条据此说明来历；三条退路都如实写明：**一条都认不出就照原词回车（绝不为了一条联想把主题改成站点更想让我们搜的东西）**、
联想项点不动（只有样式没有点击处理）就退回把那个词 `fill` 回搜索框再回车、**没弹下拉就一个字都不提**（绝不凭空造一条建议）；开关关掉或没有主题词可认时连下拉都不读。
**读到门口就退出来**（`SURF_AVOID_WALLS`）——真人看到「请先登录 / 开通会员才能看 / 请验证你不是机器人 / 页面不存在」是看一眼就走，
不会把门上的话当成内容读完，更不会去试验证码、猜登录 cookie、买会员。`_wall_reason` 是纯函数、判得保守：正文得**短到像一扇门**
（`_WALL_MAX_CHARS`=600 字以内）且墙话出现在标题或开头，才认成 `登录墙`/`订阅墙`/`人机验证`/`维护/出错页` 四类之一
（一篇长文章里顺口提一句「登录后可同步收藏」不算墙），认成墙就给这条信号打 `wall`、`excerpt` 收成门口那几句话（`_WALL_EXCERPT_CHARS`=260）、
`warnings` 写明「没有绕墙，也没把墙上的话当成正文」；`_digest_prompt` 渲染成「这一页被…挡住」且 `_SURF_SYSTEM` 第 11 条要求
**绝不拿它当内容证据出卡**，被挡页数汇总进「本轮浏览说明」；**被挡的页也不算「上几轮读过的页」**（`remember_read_pages` 直接跳过带 `wall` 的信号——
没读进去就不装读过，下一轮还会再试）；关掉开关 = 一律照正文读（墙话也会进摘录）。
**读过的不重读**（`SURF_REMEMBER_READ`，跨会话浏览历史）——一轮浏览前按**租户**读出上几轮读过的页（`read_history()`），
`_harvest` 挑条目时先滤掉读过的、改点没见过的新条目，`_queue_video` 也不重复点进上几轮看过的视频页（**只拦顺路发现的，`videoUrls` 显式指定的永远照看**）；
有记忆时 `_discover_video_links` 把标题多扫几倍（真人从更长的列表里挑没看过的那条），没记忆时探测条数与旧口径逐字一致。
轮末 `remember_read_pages()` **只记真读进去的**（`kind` 为 item/video 的信号；首页/列表页/搜索结果页/失败页/note 都不算，
**sample/http 整轮都不写**——没联网真读就不装读过），同一页再读只 `last_seen`/`reads` 递增不重复记账，超 `SURF_HISTORY_MAX` 先忘最旧的；
跳过几条 / 几条视频上几轮看过 / 这轮新记了几页都并进**同一条** `note` 如实交代，而**整版都读过时照样按原顺序重读**、
只说「没找到没见过的新条目」且**绝不写「跳过 N 条」**；关掉开关既不记也不跳，`GET /api/surf/history` 此时 `items` 恒空（没记就是没记）。
排序、`picked_for`、如实说明、视频挑选、换词重搜、联想词挑一条、挡墙退出、读过的不重读八种口径都在 doctor 里断言。关键产品取舍（承需求）：**灵感不自动交付智能体**——会话产物进独立跨任务灵感池（idea 库，`surf_insights`/`data/surf_pool.json`，与作业后端 `surf_jobs` 分家、**不随任务删除**），用户在 `SurfPanel` 自行勾选、调整后才由 `POST /tasks/{id}/surf/apply` 幂等注入 `brief.constraints`（前缀 `【灵感冲浪】`）；浏览记忆也进**第三份跨会话存储**（`surf_history`/`data/surf_history.json`，主键 `<tenant>|<url>`，同一套双后端、同样不随任务删除、面板可查看可清空）。会话生命周期 `queued→surfing→digesting→done|failed` 走 `gen_jobs` 双后端；`auto` 按 browser→http→sample 择优；`server.py` 仿 `_publish_loop` 起 `_surf_loop`（`SURF_AUTO_ENABLED` 默认关、保持零副作用）。
56. **任务终态 ≠ 追踪导出完成：doctor 里读落盘轨迹文件的两处都必须先等它出现**（真实踩坑，两处各踩一次）：`orchestrator` 先把任务状态落库，图退出后 `tracer.finish_trace` 才算 `duration_ms`→转发 OTel→把十几万字的 OTel 形状 JSON 写 `.tmp` 再 `os.replace`。`duration_ms` 一改主线程就读得到，而**文件还在后面**，于是「`data/traces/<task>.json` 不存在」成了偶发假失败（第十四轮在 `check_otel` 撞到，冲浪这一轮又在 `check_tracing` 撞到）。两处统一用同一条宽限轮询（≤10s、每 50ms 探一次文件是否存在）；未采样的 trace 本就不落盘，宽限到头仍没有就照实判失败——**是等导出，不是重试断言**。

---

## 3. 未完成的开发待办

**当前无未完成开发待办**。原文档按轮次罗列的开发项均已落地（其勾选条目已按文档规范删除，不在本文档保留「进度感」）。

- 需要新功能时，回到 `creator.md`（角色与契约）/ `plan.md`（方案与排期）清点，**不要在本文档追加流水账**。
- 代码与文档不一致时以代码为准，并立即修正文档。
- 部署方 / 用户侧事项见下一节，不属于开发待办。

---

## 4. 部署方 / 用户侧事项（非开发待办）

> 上线前人工事项的权威副本见 `README.md`，此处保留供开发侧对照。
> 以下事项代码侧已提供接口、样例或如实标注边界，由部署方按需实施。

| 事项 | 代码侧现状 |
|---|---|
| 吊销并更换联调用的 DeepSeek API Key | 该 Key 已在对话中明文出现，应视为泄露；替换 `.env` 即可，无代码改动（P0） |
| 设置生产访问令牌 | `CREATOR_API_TOKENS` + 鉴权中间件 + 租户隔离已就绪（P0） |
| 非中文市场法规词库 | 语言画像已写入当地合规红线并强制 `needs_human_review`；词库内容需目标市场法务确认（P0） |
| 提供品牌 VI 与产品资料 | 品牌 RAG、禁用词、术语表接口已就绪（P1） |
| 配置发布网关 | 平台无关 webhook + 到期队列 + 退避重试已备；多平台真实一键发布的私有授权由网关承接（P1） |
| 补充黄金数据集的行业用例 | 用例格式与判定器已就绪，加 JSON 即可（P1） |
| 确认行业合规词库（医疗/金融/教育） | 词库结构就绪，填入法务确认的规则即可（P1） |
| 选定数字人服务商并正式接入 | `sample` 内置引擎 + `http` 适配样例 + 前端面板 + API 已备（P2） |
| 选定图片/视频生成服务商并正式接入 | `imagegen.py`（sample/openai/local）+ `videogen.py`（sample/http）+ `petgen.py`（sample/imagegen，帧复用图片通道；含内置运行器 `app/desktop_pet.py`）接入样例 + 前端面板 + API + 成本熔断已备；选型与真机跑通由部署方决定；桌宠运行器形态（内置样例 vs 接入既有桌面宠物宿主）属产品决策（P2） |
| 选定原生视频理解网关以启用视频理解 | `videounderstand.py`（sample/real）+ 注入 Brief 接缝 + 前端面板 + API 已备；`real` 把整集交给**原生支持视频输入的理解网关**（`VIDEOUNDERSTAND_API_URL`，网关需与素材同机/可达），未配则如实失败（P2） |
| 选定配音引擎与真克隆服务 | 真实功能（非样例）：`dubbing.py` 六通道 `auto/sapi/sovits/elevenlabs/real/sample` + 前端试听与整轨下载 + API + 成本熔断 + **跨语言译制配音**（`target_lang` 逐句译成目标语言再合成，复用 `LLM_*` 网关）已备；`sapi` 在 Windows 本机即可出声但**不克隆**，真克隆需部署方起自建 GPT-SoVITS api_v2（`DUBBING_SOVITS_BASE_URL`）或配 `DUBBING_ELEVENLABS_API_KEY`（按字符计费），自定义网关走 `DUBBING_API_URL`；真译制需配真实 LLM 网关（否则只出占位译文）（P2） |
| 启用灵感冲浪的浏览器/检索真引擎 | `surf.py`（sample/auto/browser/http）+ 跨任务灵感 idea 库 + `SurfPanel` 勾选注入 Brief + API + 成本熔断已备；默认 `sample` 离线零依赖跑通。要真冲浪：部署方手动 `pip install playwright && playwright install chromium`（不进默认依赖）+ 配 `SURF_ALLOWED_DOMAINS`/`SURF_SEED_SITES`（只访问白名单公开站），或配 `WEB_SEARCH_*` 走 `http`；后台定时囤灵感需显式开 `SURF_AUTO_ENABLED`+`SURF_TOPICS`。**看视频走在线看（非下载）**：`SURF_WATCH_VIDEOS`（或会话传 `videoUrls`）开启后 browser 像真人点播放→校验真在播→播放中逐段截图→按 `SURF_VLM_GROUP_FRAMES` **一组一组**交 VLM（复用 `LLM_*`、需 `LLM_VISION`）出逐组小结再整段汇总，截图同时兼做画面文字 OCR，标题/简介/UP主/播放量/评论从 DOM 直抓（`SURF_MAX_COMMENTS` 上限），抓不到时截整页图交模型逐字转写（只补空缺、离线的转写不采信）；离线/无读图网关时只截帧不交模型，画面摘要自报 `simulated=True`+`vision_skipped=True`，**绝不假装看懂**；**浏览本身也像真人**：`SURF_HUMAN_PACE` 随机节奏刷页 + `SURF_SEARCH_ON_SEEDS` 用起点站自己的搜索框逐字打字搜主题（打字时站点弹出的**搜索联想**里也只点认得出主题词的那条，`SURF_USE_SUGGESTIONS`+`SURF_SUGGESTIONS_MAX`，点中就把这一版关键词记成 `suggested`——**站内的真实叫法**，认不出/点不动/没下拉三种情况一律照原词回车并如实说明，**绝不为了一条联想改主题**；结果页标题里一个主题词都认不出就**清空搜索框换个更短的说法再搜**，`SURF_SEARCH_RETRIES` 上限，`_query_variants` 只把本次主题切短、**绝不凭空造词**） + `SURF_LIST_PAGES` 点站点自己的「下一页/加载更多」接着刷列表 + 内容页点开「展开全文」并逐条抓评论区（`SURF_PAGE_COMMENTS`）+ `SURF_HOPS_PER_PAGE` 从信息流点进同域内容页读一层 + `SURF_TOPIC_FOCUS` 挑条目先**扫标题认出主题词**（`picked_for` 记下凭标题里哪几个词点进去，整版都认不出就退回按列表原顺序读；**要在线看的视频也一样按标题挑**（`_discover_video_links` 连标题读回再排序，视频信号带 `listed_as`/`picked_for`）），四类页面共用 `SURF_MAX_PAGES` 预算 + `SURF_REMEMBER_READ` **读过的不重读**（上几轮真读到的条目页/看完的视频页按租户记进跨会话浏览历史 `surf_history`/`data/surf_history.json`，`SURF_HISTORY_MAX` 先忘最旧的；下一轮顺路发现读过的就跳过、改读没见过的，用户 `videoUrls` 显式指定的照样看，**整版都读过仍按原顺序重读**），逐条信号留 `via`/`listed_as`/`list_page`/`gap_ms`/`scrolls`/`picked_for`/`query`+`search_tried`+`suggested`+`wall` 轨迹，没搜索框/点了下一页但页面没变/结果页越界/标题里认不出主题词/联想项点不动（退回打回框再回车）或这站根本没弹下拉/换到没更短的说法仍搜不出对题的东西/读到门口被登录·订阅·人机验证·维护页挡住（`SURF_AVOID_WALLS`：只记下门口那几句话就退出，**绝不绕墙**，也不拿它当正文出卡、不装读过）/整版条目都读过（此时不写「跳过 N 条」）等一律如实不做过并落 `note` 说明，浏览记忆**只由 browser 写**（sample/http 不联网真读就不装读过）；真看需装 Playwright + 配支持图像输入的多模态网关 + 配白名单（`surf.watchReady`/`surf.visionConfigured` 在 `GET /api/settings` 里如实反馈就绪度）（P2） |
| 决定是否切换 pg 存储模式 | 双后端已实现（`CREATOR_STORAGE=pg`）；是否启用、PG/Redis 托管还是自建、k8s 副本数调整由部署方决策（P2） |
| 接入 OTel Collector / Jaeger 生产实例 | `OTLP_ENDPOINT` 已支持，导出的 id 与本地一致（P2） |
| 建立人工抽检机制 | 评估报告、审批工作流、抽检面板已就绪（P2） |
