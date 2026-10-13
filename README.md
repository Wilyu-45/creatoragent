# Creator Agent Studio

> 把一句营销 Brief 交给一支编排好的 AI 内容团队：**策略 → 创意 → 策划 → 文案 → 审校 → 事实核查 → 合规 → 视觉 → 渠道 → 评估 → 人工审批 → 发布 → 复盘**，全程看得见、可干预、有门禁、能复盘，**不填一个 API Key 也能完整跑通**。

**读者**：第一次接触项目的人与部署方。**回答**：这是什么、怎么最快跑起来、上线前必须由人做什么。
**不负责**：界面操作与配置细节（→ [`USER_GUIDE.md`](USER_GUIDE.md)）、架构与角色定义（→ [`creator.md`](creator.md)）、工程约定（→ [`ENGINEERING_PRINCIPLES.md`](ENGINEERING_PRINCIPLES.md)）。

---

## 这是什么

编排式多智能体内容创作台：11 个专业智能体（A1–A11）由总控 A0 驱动，审核智能体把守质量与合规门禁，
人在关键节点拍板，产出可直接投放的多平台内容。

它不是「一个能写文案的提示词」，而是一套带状态机、门禁、成本熔断、质量度量与回归门禁的**生产线**。
架构分层、状态机、数据模型与智能体详细定义见 [`creator.md`](creator.md)。

**技术栈**：Python 3.12 · FastAPI · LangGraph · Pydantic · React 19 + TypeScript + Vite 7

---

## 快速开始

### 依赖

- **Python 3.12**（推荐 conda 环境 `multi-agent-creator`）
- **Node.js ≥ 22.6**（仅前端构建需要；生产模式后端直接托管 `dist/`）

### 安装与启动

```bash
conda activate multi-agent-creator
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
npm install
npm run build
python -m app.main          # 默认 http://127.0.0.1:8787
```

浏览器打开 `http://127.0.0.1:8787`。**默认使用内置离线引擎，无需任何密钥。**

开发模式（前后端热更新）：`npm run dev` —— 同时起 `python -m app.main`（:8787）与 Vite（:5273）。

### 部署（多种方案）

```bash
docker compose up -d --build     # 容器一键起（应用 + Jaeger）
#   应用   http://127.0.0.1:8787
#   Jaeger http://127.0.0.1:16686
```

- 只要应用：`docker compose up -d --build app`；受限网络：`DOCKERFILE=Dockerfile.offline docker compose up -d --build`
- **数据在 `/data`，必须挂卷**，否则容器重建即丢任务与记忆库

**裸机（systemd）/ Windows 任务计划 / Nginx·Caddy 反向代理 / k8s 的完整方案见 [`DEPLOYMENT.md`](DEPLOYMENT.md)**。

### 接入真实模型

界面「运行时设置 → 模型」切到 `openai` 并填 Base URL / Key / 模型名即可
（兼容一切 OpenAI 协议网关：DeepSeek / 通义 / 豆包 / vLLM / Ollama）。也可用环境变量：

```bash
LLM_PROVIDER=openai OPENAI_BASE_URL=https://api.deepseek.com/v1 \
OPENAI_API_KEY=sk-xxx OPENAI_MODEL=deepseek-chat python -m app.main
```

**配置项与环境变量清单的唯一归属是 [`USER_GUIDE.md`](USER_GUIDE.md)**（另见 [`.env.example`](.env.example)），本文件不再重复。

---

## 能力概览

| 能力 | 现状 |
|---|---|
| 多智能体流水线 | A0 编排 A1–A11，LangGraph `StateGraph` 承载控制流（条件边门禁环 + `interrupt()` 人工审批 + 断点续跑） |
| 门禁与人工在环 | `pass / revise / reject / escalate`；A6 事实核查与 A7 品牌合规（广告法词库 + 二创版权 checklist）可否决；关键节点挂起等待人工拍板 |
| 两级容错与成本可控 | 重试退避 → 降级内置离线引擎；一任务一账本，超预算熔断；LLM 响应缓存 |
| 知识复利 | A11 记忆库（关键词 + 向量混合检索），按租户 + 语言分区，过期自动下线 |
| 质量评估 | 与门禁解耦的 LLM-as-a-Judge 六维评分；模型版失败自动回退规则版 |
| 回归门禁 | 黄金数据集（固定用例 + 分数层与内容层双向断言），质量下降会被 CI 拦下 |
| 发布闭环 | 审批 → 排期 → 登记发布 / webhook 自动投递 → 回填真实效果 → A10 复盘 → A/B 结论 |
| 多语言与视频脚本 | 中/英/日/韩/西**原生创作**（非翻译）；短视频产出独立 `video_script` 产物 |
| 多租户与可观测 | 令牌 → 租户，任务与记忆库双向隔离；span 树覆盖整个 session，可选 OTLP 导出 |
| MCP 插件 | `app/mcp_server.py` 暴露 9 个 MCP 工具（stdio / streamable-http 双传输），DeepSeek harness、Claude Code、Trae 等 agent 可直接驱动流水线 |
| 动效工场 / 视频工场 | 纯前端工具（`src/motion/` 22 预设 + JSON 时间轴脚本；`src/vg/` 字幕 → 自动建轴 → 导出 MP4/WebM 成片，720p–4K，去 AI 味 + 卡点吸附） |
| 生成模型接入样例 | 图片（`app/core/imagegen.py`：sample/openai/local）、视频（`app/core/videogen.py`：sample/http，逐镜画面段+成本熔断）、桌面宠物（`app/core/petgen.py` sample/imagegen + 内置运行器 `app/desktop_pet.py`，交付**可运行宠物包**）、数字人——均遵同一「POST 建作业 / GET 查状态」最小契约范式，未配端点时显式失败不假装成功 |
| 视频理解接入样例 | 摄取侧「看懂画面」（`app/core/videounderstand.py`：sample/real）：把**整集视频**交给**原生支持视频输入的理解网关**（一集=一次调用）→ 结构化视觉摘要 → 注入 Brief 驱动创作；与视频生成同一 POST/GET 异步契约，只传 `assets/` 引用不搬运大文件，未配网关/非本地文件时显式失败不假装看懂 |
| 创作技能提炼接入样例 | 把 UP 主**自己的作品**（`app/core/skillgen.py`：rules/llm）提炼成扩展插件可装载的通用技能：文档/字幕素材 + 已产出文本 + 视频理解摘要 → `SKILL.md`（frontmatter）+ `skill.json` + 技能包 zip，并可一键沉淀成记忆库 `template` 卡片；`rules` 零 token 出**真统计骨架**（`simulated=true` 声明不含风格判断），`llm` 复用 `LLM_*` 网关、未配真实网关时显式失败不假装提炼 |
| 全自动配音（**真实功能**，非样例） | `app/core/dubbing.py`：剧情文本（说话人标记 / 小说式句式）+ 说话人参考音频 → 引擎**逐句真合成**并落盘（`sapi` Windows 系统音色 / `sovits` 自建 GPT-SoVITS 零样本克隆 / `elevenlabs` 云端克隆 / `real` 自定义网关 / `auto` 择优 / `sample` 离线联调）→ 按音频**实长**重建时间轴 + 整轨 wav + 汇总 txt；后台 worker 逐句推进、**断点续跑**（已合成句没有重跑的理由），缺 key/端点/参考音频**受理即失败并点名变量与说话人**；可选 `target_lang` 走**跨语言译制配音**（复用系统 LLM 网关逐句译成目标语言再合成，原句保留、离线占位译文如实标注） |
| 灵感冲浪（**自主上网找灵感**） | 解决「创作时缺灵感」：`app/core/surf.py`（`sample`/`auto`/`browser`/`http`）像真人一样上网刷信息流/看视频，把外部信号经系统 LLM 网关消化成一批**灵感卡**（点子/切入角度/开场钩子/为什么现在火/参考链接），沉淀成跨任务**灵感 idea 库**，用户在面板自行勾选、调整后再「注入 Brief」驱动创作（灵感**不自动交付智能体**）。默认 `sample` 零依赖零外网离线跑通（自报 `simulated`）；`browser` 走惰性可选 Playwright、`http` 复用检索网关，缺依赖/缺配置**受理即诚实失败并点名**；`browser` 的**浏览本身也像真人**（随机幅度与随机停留刷页、偶尔原地停一下、开新页前先犹豫 → 用**起点站自己的搜索框**逐字打字搜主题（打字时站点弹出的**搜索联想**里也挑认得出主题词的那条点上——`SURF_USE_SUGGESTIONS`＋`SURF_SUGGESTIONS_MAX`，这一版关键词因此是**站内的真实叫法**、落在信号的 `suggested` 上，一条都认不出就照原词回车、**绝不为了点一条联想把主题改掉**，没弹下拉就一个字都不提；结果标题里认不出主题词就**清空搜索框换个更短的说法再搜**，最多 `SURF_SEARCH_RETRIES` 次，只把本次主题切短、不凭空造词） → 点站点自己的「下一页/加载更多」接着刷列表 → 内容页点开「展开全文」并逐条抓评论区 → 从信息流**先扫标题认出主题词**再挑同域内容页点进去读一层，**要在线看的视频也一样按标题挑**（链接连标题一起读回再排序，视频信号带「列表里看到的标题/凭哪几个词点进去」），**碰到登录墙/订阅墙/人机验证/维护页就只记下门口那几句话就退出来**（`SURF_AVOID_WALLS`，信号标 `wall`——**不绕墙**、不把墙话当正文出卡，这类页也不算读过的页），四类页面**共用 `SURF_MAX_PAGES` 预算**、并按**跨会话浏览历史**（`SURF_REMEMBER_READ`，`surf_history`，`SURF_HISTORY_MAX` 先忘最旧的）**读过的不重读**——顺路发现上几轮读过的条目/看过的视频就跳过、改读没见过的新页（用户在会话里显式指定要看的视频链接照样看；只记**真读进去的**条目页与视频页，首页/列表页/搜索结果页一律不算，`sample`/`http` 整轮都不写），逐条信号留「入口/页码/滚停/凭标题哪个词点进来/站内搜索用的哪几版关键词（哪版是站点给的联想）」证据轨迹，搜不动、联想项点不动（就退回把那个词打回搜索框再回车）、换到没更短的说法仍搜不出对题的东西、没有下一页控件、点了页面没变、页数用尽、整版标题认不出主题词（条目与视频都算）、整版条目上几轮都读过（此时照样按原顺序重读，不谎报跳过）、读到门口就被墙挡住（写明几页没让读，绝不拿门口那几句话充内容）都**如实说明而不是假装做过**）；安全上只访问 `SURF_ALLOWED_DOMAINS` 白名单并复用 web.py 的 SSRF 拒绝；**看视频走在线看、不下载**（`SURF_WATCH_VIDEOS` 或会话传 `videoUrls` 时 browser 像真人点播放→校验画面真在推进→播放中逐段截图→**一组一组**交多模态 LLM 逐组看懂并兼做画面文字 OCR→再整段汇总，标题/简介/评论先从 DOM 直抓、抓不到时截整页图逐字转写（只补空缺，离线的「转写」不采信）；离线/未开 `LLM_VISION` 读图网关时只截帧不交模型，自报 `simulated`/`vision_skipped`、绝不假装看懂） |
| 桌面应用 | `make app` 打包单文件 exe（内嵌前端，PyInstaller）；全部配置可在界面「运行时设置」修改并持久化，双击即用 |
| 部署 | 裸机 systemd / Docker·compose / k8s / Windows 任务计划 + Nginx·Caddy 反代样例，含清单静态核验 |

---

## 智能体清单

总控 A0 负责解析 Brief、拆任务卡与编排流水线；A1–A11 为专业智能体。
**职责、产出物与协作流程的完整定义见 [`creator.md`](creator.md)**，此处只列一句话职责。

| ID | 名称 | 一句话职责 |
|---|---|---|
| A1 | 策略洞察 | 受众画像、核心信息屋、传播目标 |
| A2 | 创意方向 | Big Idea 与创意方向 |
| A3 | 内容策划 | 选题、标题备选、内容结构 |
| A4 | 文案创作 | 多版本文案（理性/感性/主推） |
| A5 | 编辑审校 | 结构 / 表达 / 品牌语气打分与修订 |
| A6 | 事实核查 | 主张核查、来源标注、风险分级（**可否决**） |
| A7 | 品牌合规 | 广告法词库、行业规则、品牌一致性（**可否决**） |
| A8 | 视觉美术指导 | 视觉方向、配色、配图 Prompt、分镜 |
| A9 | 渠道运营 | 多平台适配、SEO、发布排期 |
| A10 | 效果分析 | 发布前预估 / 发布后复盘与 A/B 结论 |
| A11 | 知识沉淀 | 归档最佳实践、跨任务 RAG 召回 |

> A0 的编排职责由编排器承担，不在 `registry.AGENTS` 中。

---

## 上线前人工事项

以下是**必须由人来做**、代码无法替代的事，按优先级排列。

### P0 — 上线前必须完成

| # | 事项 | 为什么需要人 | 建议做法 |
|---|---|---|---|
| 1 | **设置生产访问令牌** | `CREATOR_API_TOKENS` 决定租户隔离边界；为空则完全不鉴权 | 生成强随机令牌，按 `acme:tok,beta:tok` 形式配置，放入 Secret 而非 ConfigMap |
| 2 | **人工审核非中文市场的合规表述** | 系统只有中文广告法词库；英文/日文/韩文/西语的合规红线已写入提示词但**未经法规校验** | 由目标市场法务复核首批产出，必要时补当地词库 |
| 3 | **吊销并更换联调用的 API Key** | 若该 Key 曾明文出现在对话或日志中，应视为已泄露 | 在模型控制台吊销并新建；新 Key 只写进 `.env`（已 gitignore），不要提交 |

### P1 — 影响可用性

| # | 事项 | 为什么需要人 |
|---|---|---|
| 4 | **提供品牌 VI 与产品资料** | 品牌语气、禁用词、Logo 规范、产品参数与可引用证据目前是通用示例；真实效果取决于喂给系统的品牌资产 |
| 5 | **配置发布网关** | `PUBLISH_WEBHOOK_URL` 需指向能调用各平台开放接口的服务（n8n / Zapier / 自建），平台授权与限流必须由该网关承接 |
| 6 | **补充黄金数据集的行业用例** | 现有用例覆盖渠道与边界，但真实业务的高频选题应由业务方提供 |
| 7 | **确认行业合规词库** | 医疗健康 / 金融 / 教育等强监管行业的规则需法务确认（当前为示例词库） |

### P2 — 增强项

| # | 事项 | 为什么需要人 |
|---|---|---|
| 8 | **选定图片/视频/数字人服务商并决定是否正式接入** | 系统提供的是接入样例（内置引擎 + openai/http/local 适配）；正式接入需明确产品形态并选型服务商（图：即梦/通义万相/Replicate/本地 SD·ComfyUI；视频：可灵/即梦/Runway/本地农场；桌宠帧复用图片通道，运行器形态——内置 tkinter 样例 vs 接入既有桌面宠物宿主——属产品决策） |
| 8b | **选定原生视频理解网关以启用视频理解** | `real` 通道把整集视频交给**原生支持视频输入的理解网关**（一集一次调用，如 Gemini 视频输入 / 自建视频理解农场）；系统不搬运大文件、只传 `assets/` 引用，网关需与素材同机/可达；未配 `VIDEOUNDERSTAND_API_URL` 则如实失败、不假装看懂 |
| 9 | **决定是否切换 pg 存储模式** | 默认 `file`（单机零依赖）与 `CREATOR_STORAGE=pg`（PostgreSQL + Redis，多副本前提）均已实现；是否为生产启用、库用托管还是自建属架构决策，切换步骤见 [`storage_contract.md`](storage_contract.md) |
| 10 | **接入 OTel Collector / Jaeger 生产实例** | 本地用 compose 里的 all-in-one 即可；生产需要持久化存储与采样策略 |
| 11 | **建立人工抽检机制** | 评估与门禁能拦住大部分问题，但品牌调性与创意质量最终仍需人判断 |

---

## 验证命令

```bash
# 环境与能力自检（覆盖范围以脚本输出清单为准，退出码即结论）
python scripts/doctor.py

# 黄金数据集回归（分数层 + 内容级硬约束）
python scripts/golden_eval.py

# 端到端验收（拉起真实服务，核对全部接口与租户隔离）
python scripts/smoke_api.py

# 快速契约核验（评估 / 租户 / 轨迹 / 传播采样 / 各旁路生成与提炼通道闭环）
python scripts/verify_contracts.py

# 部署清单核验（不需要 Docker daemon）
python scripts/check_deploy.py

# 并发正确性 / 性能基线
python scripts/stress_llm.py -n 8 -c 4

# 前端
npm run typecheck && npm run build
```

一键入口：`make verify`（全量门禁）/ `make release`（发行版自检）/ `make help`；Windows 用根目录 `make.bat`，版本号在 `VERSION`。

全部关卡已接入 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)：**无外部依赖、退出码即结论**，
PR 阶段就能拦住质量回归。各脚本的覆盖范围与用法见 [`USER_GUIDE.md`](USER_GUIDE.md)。

---

## 文档导航

| 文档 | 回答什么问题 |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | **使用与运营**：界面操作、运行时配置与环境变量、评估 / 回归 / 排障步骤 |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | **部署方案**：裸机 / 容器 / Windows / 反向代理的选择、安装、升级与自检 |
| [`creator.md`](creator.md) | **架构与角色**：智能体职责、协作流程、状态机、数据契约、门禁与权限设计 |
| [`plan.md`](plan.md) | **技术选型与排期**：框架对比、分层架构、测试策略、验收指标、as-built 对照 |
| [`storage_contract.md`](storage_contract.md) | **存储契约**：file / pg 双后端接口签名、不变量、实体映射与切换步骤 |
| [`capability_samples.md`](capability_samples.md) | **能力接入样例**：数字人 / 图片 / 视频 / 视频理解 / 桌面宠物 / 创作技能提炼六条旁路通道与两个前端工场——配置、界面与失败边界 |
| [`ENGINEERING_PRINCIPLES.md`](ENGINEERING_PRINCIPLES.md) | **工程约定**：从真实踩坑提炼的开发原则（验证、契约、可失败设计、断言口径、环境） |
| [`MEMORY.md`](MEMORY.md) | **开发记忆**：未完成的开发待办、非显然的设计决策（别乱改重来） |

按目的走：快速跑起来 → 本文「快速开始」；排查线上问题 → `USER_GUIDE.md`；想知道「做到哪了」→ `plan.md` + `MEMORY.md`。

---

## 许可

见 [LICENSE](LICENSE)。
