"""运行时配置（移植自 server/config.ts）。

所有可调项均可通过根目录 `.env` 或进程环境变量覆盖，且可在运行期通过
`PUT /api/settings` 动态修改（`update_config`）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

# ------------------------------------------------------------------ #
# 路径与 .env 加载（不覆盖已存在的进程环境变量）                        #
# ------------------------------------------------------------------ #

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)

#: 持久化根目录。默认 ``<项目根>/data``，可用 ``CREATOR_DATA_DIR`` 覆盖
#: （测试与多实例部署时避免互相污染）。
_DATA_DIR_ENV = (os.environ.get("CREATOR_DATA_DIR") or "").strip()
DATA_DIR = Path(_DATA_DIR_ENV) if _DATA_DIR_ENV else (ROOT_DIR / "data")
TASK_DIR = DATA_DIR / "tasks"
BLACKBOARD_FILE = DATA_DIR / "blackboard.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
#: A11 记忆库（跨任务知识卡片），供其他智能体做 RAG 召回。
MEMORY_FILE = DATA_DIR / "memory.json"
#: Brief 素材目录（plan.md v2.0「多模态理解」）：本地图片素材只允许放在这里，
#: 由 ``app/core/assets.py`` 读取并内联为 data URL。**不接受任意绝对路径** ——
#: Brief 可来自 API 调用方，放开绝对路径等于给模型一个本机文件读取原语。
ASSETS_DIR = DATA_DIR / "assets"
#: 成品导出目录：审批通过后由交付环节把全部版本渲染成 txt 落盘，
#: 供 ``GET /api/tasks/{id}/export`` 下载（只写本目录，不碰任意路径）。
EXPORTS_DIR = DATA_DIR / "exports"
#: LLM-as-a-Judge 评估历史（plan.md 4.3 D14），用于 Prompt 回归与黄金集评测。
EVAL_FILE = DATA_DIR / "evaluations.json"
#: LangGraph checkpointer 的落地位置：用于人工审批中断后的断点续跑。
CHECKPOINT_FILE = DATA_DIR / "checkpoints.sqlite"

LLMProviderName = Literal["mock", "openai"]
#: 数字人渲染提供方：sample = 内置样例引擎（离线可跑）；http = 通用 HTTP 适配样例
DigitalHumanProviderName = Literal["sample", "http"]
#: 图片生成提供方（开发样例，见 core/imagegen.py）：
#: sample = 内置离线样例引擎（不产真图、只出清单）；openai = OpenAI 协议 /images/generations
#: （覆盖经网关的即梦/通义万相/Replicate 等云端模型）；local = 本地图生网关
#: （A1111/SD-WebUI 的 /sdapi/v1/txt2img 同步接口，私网端点计 0 元）
ImageGenProviderName = Literal["sample", "openai", "local"]
#: 视频生成提供方（开发样例，见 core/videogen.py）：sample = 内置离线样例；
#: http = 「POST 建任务 → GET 查状态」最小契约的任意网关（云端可灵/即梦/Runway 或自建
#: 本地渲染农场均可，厂商私有协议由你的网关层消化，与数字人同一取舍）
VideoGenProviderName = Literal["sample", "http"]
#: 视频理解提供方（开发样例，见 core/videounderstand.py）：
#: sample = 内置离线样例（不调网关、只出带 simulated 标记的占位视觉摘要骨架）；
#: real = 整集视频交给**原生支持视频输入的理解网关**（一集一次调用）真理解，未配网关即显式失败
VideoUnderstandProviderName = Literal["sample", "real"]
#: 配音提供方（见 core/dubbing.py）：真实出音频的功能，五个引擎通道 + 离线联调通道：
#: sample = 离线模拟（不出真音频，专供联调/回归）；sapi = 本机系统音色真合成（Windows
#: SAPI，零依赖零费用，但**不克隆参考音频**，汇总里如实标注）；sovits = 自建
#: GPT-SoVITS/CosyVoice 克隆服务（参考音频零样本克隆，免费离线）；elevenlabs = 云端
#: Instant Voice Clone（参考音频上传克隆 + 逐句合成）；real = 「POST 建任务 → GET
#: 查状态」最小契约的自定义配音网关；auto = 按 elevenlabs→sovits→sapi→real 择优取
#: 第一个已配置可用的引擎，全都没配则回退 sample 并在汇总里写明
DubbingProviderName = Literal["sample", "auto", "sapi", "sovits", "elevenlabs", "real"]
#: 桌面宠物提供方（见 core/petgen.py）：sample = 内置离线样例引擎，用纯标准库
#: PNG 编码器画出真实可用的动作帧（简笔形象、非美术稿）；imagegen = 复用
#: IMAGEGEN_* 端点逐帧出真图（跨帧形象一致性由 prompt 承担，未做切帧/对齐后处理）
PetGenProviderName = Literal["sample", "imagegen"]
#: 创作技能提炼提供方（见 core/skillgen.py）：rules = 零依赖量化提炼（真统计作品正文的句长 /
#: 段落 / 语速，不含模型推理出的风格判断，产物 simulated=true）；llm = 走**系统既有模型网关**
#: （LLM_*，不另持端点与密钥）做一次结构化提炼，未配真实网关即显式失败
SkillGenProviderName = Literal["rules", "llm"]
EmbeddingProviderName = Literal["local", "openai"]
JudgeModeName = Literal["off", "advisory", "blocking"]
JudgeProviderName = Literal["offline", "llm"]
#: 联网检索提供方：none = 不联网（默认，离线可跑）；http = 走自建搜索网关
SearchProviderName = Literal["none", "http"]
#: 灵感冲浪提供方（见 core/surf.py）：像真人上网刷信息/看视频信号、消化成灵感卡：
#: sample = 离线确定性占位（不联网、产 simulated 灵感，供联调/回归）；
#: browser = 真·无头浏览器（Playwright，惰性 import，未装/未配白名单即显式失败）；
#: http = 复用既有联网检索网关（web_search+page_fetch）做轻量冲浪；
#: auto = 按 browser→http→sample 择优
SurfProviderName = Literal["sample", "auto", "browser", "http"]

#: 存储后端：file（默认，JSON + SQLite，零依赖离线可跑）| pg（PostgreSQL + Redis，支持多副本）。
#: 替换契约与实现指引见 storage_contract.md；PG 实现只在 ``pg`` 模式下被 import（零依赖保证）。
StorageModeName = Literal["file", "pg"]


def _storage_mode() -> str:
    raw = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
    if raw in ("file", "pg"):
        return raw
    import sys

    print(f"[config] CREATOR_STORAGE={raw!r} 非法（只支持 file/pg），回落 file", file=sys.stderr)
    return "file"


#: 存储后端选择在 **import 期固化**：单例构造、checkpointer 分支都依赖它，
#: 进程生命周期内不可变（与 DATA_DIR 同口径）。
STORAGE_MODE: StorageModeName = _storage_mode()  # type: ignore[assignment]

#: PG / Redis 连接配置（仅 ``STORAGE_MODE == "pg"`` 时被 app/core/pg.py 等读取）。
#: 留空时 pg.py 使用本地默认值（见各模块 docstring）。
DATABASE_URL = (os.environ.get("CREATOR_DATABASE_URL") or "").strip()
REDIS_URL = (os.environ.get("CREATOR_REDIS_URL") or "").strip()

#: 评估口径版本号。改动权重或规则时递增，便于「同一批数据跨版本对比」时区分。
#: 放在 config 而非 judge 模块，是为了让 ``public_config()`` 不必反向 import 评估器。
RUBRIC_VERSION = "2026-09-13.judge-v1"


# ------------------------------------------------------------------ #
# 配置模型                                                            #
# ------------------------------------------------------------------ #


@dataclass
class AgentModelOverride:
    """单个智能体的模型覆盖（``LLMSettings.agent_models`` 的值）。

    三个字段**空串都表示继承全局** ``llm`` 设置；用途是让某个智能体改用
    不同的模型或端点（例如 A10 分析走本地 Ollama，其余走云端 DeepSeek）。
    """

    model: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass
class LLMSettings:
    """模型接入设置。`base_url` 兼容一切 OpenAI 协议服务（DeepSeek/通义/豆包/vLLM/Ollama）。"""

    provider: LLMProviderName = "mock"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_ms: int = 60_000
    #: 是否把 Brief 素材里的图片作为多模态输入发给模型（plan.md v2.0「多模态理解」）。
    #: **默认关闭**：绝大多数 OpenAI 兼容文本模型（DeepSeek chat/flash、通义 qwen-plus、
    #: 本地 Ollama 文本模型…）收到图片内容块会直接返回 400，而这是不可重试的确定性失败，
    #: 只会让该智能体降级到离线引擎——默认开启等于给按默认配置跑通的链路埋雷。
    #: 确认所用模型支持读图后打开；素材的**文字清单**不受此项影响，始终可用。
    vision: bool = False
    #: 单次创作最多注入的图片数：多模态输入按图片计费，且图多了会挤占文本预算
    vision_max_images: int = 6
    #: DeepSeek 思考模式开关（``{"thinking": {"type": ...}}``，仅 DeepSeek 网关识别，
    #: 其他 OpenAI 兼容实现会忽略未知字段）。**默认 disabled**：
    #: 官方默认 enabled，思维链输出在 ``reasoning_content`` 且按 output 价计费——
    #: 长提示词下思维链会耗尽 ``max_tokens`` 导致 ``content`` 为空（real_check 实测
    #: A5 稳定复现），且思考模式下 temperature 等采样参数不生效。
    #: 本项目是提示词已含完整推理脚手架的结构化 JSON 流水线，默认关闭；要换更强
    #: 推理时设 ``LLM_THINKING=enabled`` 并调大 ``LLM_MAX_TOKENS``。
    thinking: str = "disabled"
    #: 每智能体模型覆盖：键为 ``A1``–``A99`` 形态的智能体 id，仅 model / base_url /
    #: api_key 三项可覆盖，空字段继承全局。与 site_urls 同属「用户在设置界面维护的
    #: 资产」，持久化到 ``SETTINGS_FILE``（见 ``_load_persisted_settings``）。
    agent_models: dict[str, AgentModelOverride] = field(default_factory=dict)


@dataclass
class EmbeddingSettings:
    """A11 记忆库的向量化设置（plan.md 2.2.3「RAG Server」）。

    默认 ``local``：用确定性 hashing embedding，零依赖、可离线；
    ``openai`` 时走任意 OpenAI 兼容 ``/embeddings`` 端点，失败自动回退本地向量。
    """

    provider: EmbeddingProviderName = "local"
    #: 留空则复用 ``llm.base_url`` / ``llm.api_key``
    base_url: str = ""
    api_key: str = ""
    model: str = "text-embedding-3-small"
    #: 本地向量维度（hashing trick 的桶数）
    dim: int = 256
    #: 混合检索中语义分量权重：0 = 纯关键词（与旧行为一致），越大越依赖向量相似度
    weight: float = 0.35


@dataclass
class PublishSettings:
    """多平台自动排期与投递设置（plan.md v2.0「自动发布」）。

    系统不内置各平台私有 SDK：统一用 webhook 把「到点该投什么」推给运营侧
    的发布网关（或用 n8n/Zapier 转接平台开放接口），本进程只负责排期与回执记账。
    """

    #: 投递 webhook 地址；为空时 dispatch 只做「登记发布」（离线可用）
    webhook_url: str = ""
    #: webhook 失败重试次数
    retry: int = 2
    #: 是否由后台定时器自动投递到期排期
    auto_dispatch: bool = False
    #: 后台自动投递的检查间隔（秒）
    tick_seconds: int = 60


@dataclass
class JudgeSettings:
    """LLM-as-a-Judge 评估流水线设置（plan.md 4.3 D14）。

    评估是**旁路**能力：默认 ``advisory``，只产出报告与建议，不改动门禁结论；
    置为 ``blocking`` 才会让评估分数参与门禁判定。这样「引入评估」不会在
    使用者不知情的情况下改变流水线行为。
    """

    #: off = 不评估；advisory = 只打分不影响门禁；blocking = 低分触发返工
    mode: JudgeModeName = "advisory"
    #: offline = 确定性规则评估器；llm = 走模型网关（失败自动回退 offline）
    provider: JudgeProviderName = "offline"
    #: 可选模型名（provider=llm 时生效，留空用当前 LLM 配置的模型）
    model: str = ""
    #: 通过线：total ≥ 该值为 pass
    pass_threshold: float = 75.0
    #: 评估分在最终质量分中的权重（0 = 不影响 scorecard.overall）
    weight: float = 0.2


@dataclass
class DigitalHumanSettings:
    """数字人视频渲染接入设置（plan.md v2.0「数字人」开发样例）。

    数字人渲染**不在本系统内实现**：各家服务的授权、形象库与回调协议差异极大，
    这里只提供接入样例（``app/core/digital_human.py``）：

    * ``sample``（默认）：内置离线样例引擎，确定性模拟「排队 → 渲染 → 完成」，
      并按视频脚本产出渲染清单 —— 未购买任何服务也能联调 API 与 UI；
    * ``http``：对接「POST 建任务 → GET 查状态」最小契约的任意网关
      （自建渲染农场 / n8n 编排的服务均可），按 ``DIGITAL_HUMAN_API_URL`` 启用。
    """

    provider: DigitalHumanProviderName = "sample"
    #: http 样例适配器的建任务端点（POST）；为空时 http provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 数字人形象标识（avatar id），随任务透传给渲染服务
    avatar: str = ""
    #: http 样例适配器的单次轮询超时（毫秒）
    timeout_ms: int = 10_000


@dataclass
class ImageGenSettings:
    """图片生成接入设置（开发样例，见 ``app/core/imagegen.py``）。

    图片生成**不在本系统内实现**：各家文生图服务的授权、计费与返回结构差异极大，
    这里只提供接入样例，让联调在采购任何服务之前就能发生：

    * ``sample``（默认）：离线确定性模拟「排队 → 生成 → 完成」，按 ``visual_brief``
      的 image_prompts 生成**生成清单**（不产真图）；
    * ``openai``：对接 OpenAI 协议 ``POST {base_url}/images/generations``（兼容经网关的
      即梦/通义万相/Replicate 等云端模型）；
    * ``local``：对接本地图生网关的 ``POST {base_url}/sdapi/v1/t2i txt2img`` 同步接口
      （A1111 / SD-WebUI；ComfyUI 需前置一个同构的同步网关）。

    未配置真实端点时 **显式失败，绝不假装成功**。
    """

    provider: ImageGenProviderName = "sample"
    #: openai/local 的端点根地址（openai 自动补 /images/generations，local 补 /sdapi/v1/txt2img）
    base_url: str = ""
    #: 鉴权密钥（openai 以 Bearer 拼接；local 可选）
    api_key: str = ""
    #: 模型标识（openai 生效；留空用网关默认）
    model: str = ""
    #: 出图尺寸（openai size 字段，如 1024x1024；local 用作宽高）
    size: str = "1024x1024"
    #: 单次作业最多生成的提示词数（图生按张计费，防止一次把整批分镜都烧出去）
    max_images: int = 4
    #: 单次生成请求超时（毫秒）
    timeout_ms: int = 60_000


@dataclass
class VideoGenSettings:
    """视频生成接入设置（开发样例，见 ``app/core/videogen.py``）。

    与数字人渲染同一哲学：系统不绑任何厂商，只提供「POST 建任务 → GET 查状态」
    最小契约的接入样例。

    * ``sample``（默认）：离线确定性模拟生命周期，按 ``video_script`` 分镜生成**渲染清单**；
    * ``http``：按 ``VIDEOGEN_API_URL`` 启用，云端视频网关或自建本地渲染农场均可。
    """

    provider: VideoGenProviderName = "sample"
    #: http 样例适配器的建任务端点（POST）；为空时 http provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: http 样例适配器的单次轮询超时（毫秒）
    timeout_ms: int = 30_000


@dataclass
class VideoUnderstandSettings:
    """视频理解接入设置（开发样例，见 ``app/core/videounderstand.py``）。

    「看懂画面」的推理不在本系统内实现：本通道把**整集视频**交给**原生支持视频输入的
    理解网关**（一集=一次调用，成本可控、贴「逐集理解再创作」节奏），由网关返回
    结构化视觉摘要。与视频生成同一「POST 建任务 → GET 查状态」最小契约范式。

    * ``sample``（默认）：离线确定性推进生命周期并产出**带 simulated 标记的占位摘要
      骨架**（不调网关，保证联调与回归）；
    * ``real``：按 ``VIDEOUNDERSTAND_API_URL`` 提交整集视频、惰性轮询取回摘要；任一前置
      （无视频素材 / 未配网关 / 视频非 assets/ 本地文件 / 成本超预算）不满足即**显式失败，
      绝不假装看懂**。

    系统不搬运大文件：只把 ``assets/`` 内的视频引用交给网关自取（网关需与素材同机/可达）。
    """

    provider: VideoUnderstandProviderName = "sample"
    #: real 样例的视频理解端点（POST 提交整集）；为空时 real provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 提交/轮询的单次请求超时（毫秒）
    timeout_ms: int = 120_000


@dataclass
class DubbingSettings:
    """全自动化配音设置（见 ``app/core/dubbing.py``）——**真实出音频**的功能。

    台词切分与说话人识别在系统内真做；声音克隆/合成按通道交给引擎执行：

    * ``sample``（离线联调通道，不出真音频）：确定性模拟「排队 → 合成 → 完成」，
      产出带 simulated 标记的时间轴汇总，供回归与流程联调；
    * ``sapi``：本机 Windows 系统音色**真合成**（零依赖、离线、零费用）。注意：
      该通道不消费参考音频做克隆，说话人映射到已安装音色，汇总中**如实标注**；
    * ``sovits``：自建 GPT-SoVITS / CosyVoice 克隆服务（``DUBBING_SOVITS_BASE_URL``，
      参考音频零样本克隆，逐句返回 wav）；
    * ``elevenlabs``：云端 Instant Voice Clone（上传参考音频建音色 → 逐句合成 mp3）；
    * ``real``：按 ``DUBBING_API_URL`` 对接「POST 建台词清单 → GET 查状态」的自定义
      配音网关。

    任一真实通道前置不满足（缺 key/端点、缺参考音频、超预算）即**显式失败，
    绝不假装配音**。
    """

    provider: DubbingProviderName = "sample"
    #: real 通道的建任务端点（POST）；为空时 real provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 提交/轮询的单次请求超时（毫秒）
    timeout_ms: int = 60_000
    #: 单作业最多配音的台词行数（克隆按句计费，防止一次把整本剧本烧出去）
    max_lines: int = 200
    #: 时间轴估算语速（字/秒）：先估后配，真实合成完成后按音频实长重建
    chars_per_second: float = 4.5
    #: sovits 通道：GPT-SoVITS api_v2 / CosyVoice 服务地址（如 http://127.0.0.1:9880）
    sovits_base_url: str = ""
    #: sovits 通道的文本语种（GPT-SoVITS 的 text_lang，如 zh / en / ja）
    sovits_language: str = "zh"
    #: elevenlabs 通道 API 地址（默认官方 v1，私有部署可改）
    elevenlabs_base_url: str = "https://api.elevenlabs.io/v1"
    #: elevenlabs 通道密钥（xi-api-key 头）；未配置时该通道显式不可用
    elevenlabs_api_key: str = ""
    #: elevenlabs 合成模型（multilingual 支持中文）
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    #: sapi 通道限定使用的系统音色（逗号分隔的 VoiceInfo.Name；空 = 用全部已安装音色）
    sapi_voices: str = ""


@dataclass
class PetGenSettings:
    """桌面宠物生成设置（开发样例，见 ``app/core/petgen.py``）。

    * ``sample``（默认，零依赖）：离线确定性推进，并用纯标准库 PNG 编码器**画出真帧**
      （简笔吉祥物，清单里 ``simulated=true``，不是真实美术稿）；
    * ``imagegen``：复用 ``IMAGEGEN_*`` 端点（openai/local 协议）**逐帧**出图，
      每帧一次调用，跨帧形象一致性由 prompt 承担。

    出帧尺寸由通道决定：sample 帧按 ``size`` 绘制；真实帧的尺寸归网关，运行器降采样显示。
    """

    provider: PetGenProviderName = "sample"
    #: sample 帧的边长（像素，正方形）。属性名不叫 ``size``：``_apply_flat`` 按属性名
    #: 夹紧，而 image_gen.size 是字符串（"1024x1024"），同名会把两处一起搞坏
    frame_size: int = 96
    #: 单次作业的帧数上限（真实通道按帧计费，防止一次把整套动作都烧出去）
    max_frames: int = 16
    #: imagegen 通道单帧请求超时（毫秒）
    timeout_ms: int = 60_000


@dataclass
class SkillGenSettings:
    """创作技能提炼设置（开发样例，见 ``app/core/skillgen.py``）。

    * ``rules``（默认，零依赖、零 token）：将作品正文**真统计**成结构与节奏技能，
      不含模型推理出的风格判断（产物 ``simulated=true`` 并写明这一点）；
    * ``llm``：复用系统既有模型网关（``LLM_*``）做一次结构化提炼——不另持端点与密钥，
      避免两处配置漂移；``LLM_PROVIDER=mock`` 时**显式失败，绝不假装提炼**。
    """

    provider: SkillGenProviderName = "rules"
    #: 单个作业最多纳入的作品份数（统计与提示词都按此截断，超出如实记入 issues）
    max_works: int = 12


@dataclass
class SearchSettings:
    """联网检索接入设置（A1/A2/A6/A10 的 ``web_search`` / ``page_fetch`` 工具）。

    检索**不内置任何搜索服务**：各家的配额、计费与返回结构差异极大，
    这里只定义「POST 查询 → JSON 结果」的最小契约（``app/core/web.py``），
    由使用者指向自己的搜索网关（SerpAPI / Bing / 自建聚合均可）。

    * ``none``（默认）：完全离线。工具会如实返回「未配置联网检索」，
      智能体据此**不得声称已联网核实**——离线可跑通的默认体验不受影响；
    * ``http``：按 ``WEB_SEARCH_API_URL`` 启用真实检索。
    """

    provider: SearchProviderName = "none"
    #: http 适配器的检索端点（POST）；为空时 http provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 单次检索返回的结果条数上限（同时限制进入提示词的篇幅）
    max_results: int = 8
    #: 单次请求超时（毫秒）；检索是旁路能力，超时必须短于创作链路可接受时延
    timeout_ms: int = 15_000
    #: 是否允许 ``page_fetch`` 抓取检索结果页正文（关掉则只给标题与摘要）
    fetch_pages: bool = True
    #: 单次 ``page_fetch`` 最多抓取的页面数
    max_pages: int = 4
    #: 「站点监控」：用户在运行时设置里配置的待爬页面 URL 列表，
    #: ``site_monitor`` 工具创作时现场抓取正文（与搜索网关无关，独立开关）。
    #: 仅此字段持久化到 ``SETTINGS_FILE``（界面设置不在 storage_contract.md 的
    #: PostgreSQL 实体清单内，pg 模式同样走本地文件，不做迁移）。
    site_urls: list[str] = field(default_factory=list)


@dataclass
class SurfSettings:
    """灵感冲浪接入设置（见 ``app/core/surf.py``）——像真人上网刷信息/看视频，消化成灵感库。

    冲浪**不内置任何浏览器或搜索服务**：真引擎通道要么惰性用 Playwright（``browser``），
    要么复用系统既有联网检索网关（``http``，见 ``app/core/web.py``）。消化灵感走系统既有
    模型网关（``LLM_*``，不另持端点与密钥），与 skillgen llm 同一取舍。

    * ``sample``（默认，零依赖、零外网）：离线把主题展开成查询，产**带 simulated 标记的
      占位灵感卡**（不联网、不调浏览器），保证 API/UI/回归可联调；
    * ``browser``：真·无头浏览器（Playwright）navigate→滚动→抽取正文/热榜。**惰性** import，
      未安装或没配 ``SURF_ALLOWED_DOMAINS`` 白名单即**显式失败、绝不假装浏览过**；
    * ``http``：复用 ``WEB_SEARCH_API_URL`` 检索网关做轻量冲浪，未配即显式失败；
    * ``auto``：按 browser→http→sample 择优，全不可用回退 sample 并在卡里写明。

    安全：只访问 ``SURF_ALLOWED_DOMAINS`` 白名单内的公开站点，复用 web.py 的 SSRF 拒绝口径；
    **不下载在线视频文件**——``watch_videos`` 开启后走「像真人点播放→播放中截图抽帧→一组截图交
    VLM 看懂」的在线看路径（仅 browser 生效）；未装浏览器 / 未配视觉网关时**绝不假装看懂**并如实标 ``simulated``。
    关闭时视频只作**信号**记录（标题/链接）。
    """

    provider: SurfProviderName = "sample"
    #: 无头模式（browser 通道）：True=后台运行，False=可见窗口（本机调试用）
    headless: bool = True
    #: 允许浏览的域名白名单；为空时 browser 通道拒绝冲浪（安全边界）
    allowed_domains: list[str] = field(default_factory=list)
    #: 冲浪起点站点/热榜 URL；browser 通道优先访问这些，其次按查询搜索
    seed_sites: list[str] = field(default_factory=list)
    #: 无主题时后台自主冲浪的话题清单；供调度器轮询囤灵感
    topics: list[str] = field(default_factory=list)
    #: 单次会话最多浏览/抓取的页面数（防一次把预算烧穿）
    max_pages: int = 6
    #: browser 通道每页滚动次数（模拟真人下滑看信息流）
    scroll_rounds: int = 3
    #: browser 通道每次滚动后的等待毫秒（等懒加载内容渲染）
    settle_ms: int = 1200
    #: 单次浏览器操作/请求超时（毫秒）
    timeout_ms: int = 30_000
    #: 每个主题展开的搜索查询数
    queries_per_topic: int = 3
    #: 每次会话消化产出的灵感卡上限
    cards_per_session: int = 5
    #: 灵感池跨任务保留的卡片上限（超出按新旧裁剪）
    pool_max: int = 200
    #: 是否由后台定时器自主冲浪囤灵感（默认关闭，保持进程零副作用）
    auto_enabled: bool = False
    #: 后台自主冲浪的检查间隔（秒）
    tick_seconds: int = 3600
    #: 「看懂视频」开关（仅 browser 通道生效）：像真人点播放按钮→播放中抽帧截图→一组截图交 VLM 看懂。
    #: 默认关（保持零副作用、离线不假装看懂）；开了且真装了 Playwright 才会播放与截图。
    watch_videos: bool = False
    #: 每个视频在播放中截取的画面帧数（交 VLM 的截图上限，控成本；实际还受硬上限约束）
    video_frames: int = 8
    #: 抽帧间隔毫秒（播放中每隔多久截一张，模拟真人边看边截）
    video_frame_interval_ms: int = 1_200
    #: 每组发给 VLM 的截图张数：真人是一段一段看的，故把一组截图交一次多模态调用，
    #: 多组结果再汇总成整段理解（组数 ≈ ceil(video_frames / vlm_group_frames)）
    vlm_group_frames: int = 4
    #: 单次会话最多「看懂」几个视频（控成本与时长；其余视频只作信号记录）
    max_videos_per_session: int = 2
    #: 是否抓取视频页的标题 / 简介 / 评论文字（DOM 直抓；截图里的文字由 VLM 兼做 OCR 转写）
    scrape_comments: bool = True
    #: 每个视频页最多收集多少条评论（逐条抓内容，滚动懒加载后取前 N 条）
    max_comments: int = 12
    #: 截图范围：True=只截播放器画面元素（含 iframe 内的播放器），False=整页截图（信息多、画面小）
    crop_to_video: bool = True
    #: 像真人一样从信息流里点进视频页：浏览页面时收集页内视频链接并逐条进入在线看
    click_into_videos: bool = True
    #: 真人节奏：每页的滚动幅度与停留时长随机浮动（False=等距滚动、固定等待的机器节奏）
    human_pace: bool = True
    #: 站内搜索：像真人那样用**起点站自己的搜索框**逐字打字搜主题，再读结果页；
    #: 找不到搜索框或回车后页面没变化，如实不搜（不假装搜过）
    search_on_seeds: bool = True
    #: 信息流多跳：每个页面从条目里挑几条**普通内容页**点进去继续读（0=只看当前页不点进）
    hops_per_page: int = 2
    #: 列表翻页：像真人那样点站点自己的「下一页/加载更多」，一个列表最多刷几页（1=只看第一页）。
    #: 每翻一页都算进 SURF_MAX_PAGES 的共用预算；没这个控件或点了页面没变就如实停下（不硬拼 ?page=2）
    list_pages: int = 2
    #: 正文页也读评论区：点开「展开全文」再取正文，并像真人那样翻到底逐条抓评论（抓不到就空，绝不编造）
    page_comments: bool = True
    #: 开新页之前的停顿基准毫秒（真人是「看一眼、想一下、再点」；human_pace 开启时按它随机浮动，0=不停）
    page_gap_ms: int = 1_500
    #: 挑条目先扫标题：信息流里同域条目按「标题认不认得出本次主题的词」排序（认得出的先点，并把
    #: 凭哪几个词点进去写成 picked_for）；关掉就退回列表原顺序，不再按主题挑
    topic_focus: bool = True
    #: 搜不对题就换个说法再搜：结果页标题里一个主题词都认不出时，清空搜索框把关键词说短再搜，
    #: 最多再搜几次（0=整句搜一次就完）。换的词只从本次主题里切出来，不凭空造词
    search_retries: int = 1
    #: 打字时看站点自己弹出的搜索联想：只点**认得出本次主题词**的那一条（点不动就把那个词打回框里再回车），
    #: 一条都认不出就照原词回车——换联想词是把话说得像站内的叫法，不是换主题，也绝不凭空造词
    use_suggestions: bool = True
    #: 一次最多读回几条联想候选（各站下拉长度不同，超了就不再看后面的）
    suggestions_max: int = 8
    #: 读到门口就退出来：页面短到像一扇门且写着「请先登录/付费阅读/请输入验证码/页面不存在」时，
    #: 如实标成墙（信号 wall）、只留门口那几句话，不绕墙也不把墙话当正文；关掉=照旧当正文读
    avoid_walls: bool = True
    #: 记住读过哪些页（跨会话）：上几轮真读进去的条目/看完的视频记在 surf_history 里，
    #: 下一轮挑条目就跳过它们改读没见过的（整版都读过时照样重读并如实说明）；关掉=每轮都当第一次刷
    remember_read: bool = True
    #: 每个租户最多记多少条读过的页（真人也记不住几百个页面；超了就忘最旧的，忘了的下一轮会重新读）
    history_max: int = 300


@dataclass
class TracingSettings:
    """分布式追踪设置（plan.md 2.4）。

    默认 **不导出**：进程内 span 树完全自实现，因此「零外部依赖、离线可跑通」
    的默认体验不受影响。配置 ``OTLP_ENDPOINT`` 后才加载 OTel SDK 并转发。
    采样只作用于**导出面**（OTLP 与落盘 JSON），进程内轨迹始终完整（UI 排障不受影响）。
    """

    #: 如 http://localhost:4318（OTLP/HTTP）；为空则只做进程内追踪
    otlp_endpoint: str = ""
    #: 资源里的服务名
    service_name: str = "creator-agent-studio"
    #: 形如 ``key1=value1,key2=value2``，供带鉴权的托管 collector 使用
    otlp_headers: str = ""
    #: 采样器，环境变量名与 OTel 规范对齐（OTEL_TRACES_SAMPLER）：
    #: parentbased_always_on（默认）/ parentbased_traceidratio /
    #: always_on / always_off / traceidratio
    sampler: str = "parentbased_always_on"
    #: traceidratio 的采样比例（0-1），对应 OTEL_TRACES_SAMPLER_ARG
    sample_ratio: float = 1.0


@dataclass
class RuntimeConfig:
    """编排与质量门禁的运行时参数。"""

    port: int = 8787
    #: 监听地址（HOST）。默认 127.0.0.1（仅本机，配合反向代理使用）；
    #: 容器端口发布要求进程监听非 loopback（容器清单已显式设 0.0.0.0），
    #: 局域网直连同样设 0.0.0.0 —— 暴露到本机以外前务必设置访问令牌。
    host: str = "127.0.0.1"
    turn_budget: int = 25
    max_revisions: int = 2
    quality_threshold: int = 75
    auto_approve: bool = False
    #: 单任务成本上限（美元）；超出即熔断到离线引擎（plan.md D12）
    cost_budget_usd: float = 0.5
    #: 单任务 token 上限，对应 plan.md 2.4「单 Session token 消耗 > 50K 触发审查」
    token_budget: int = 50_000
    #: 单任务文档研读（digest）的 LLM 调用上限：块数超出时按序截断并在产物里如实记录
    digest_max_calls: int = 12
    #: 是否启用 LLM 响应缓存（plan.md 4.5）
    llm_cache: bool = True
    llm: LLMSettings = field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    publish: PublishSettings = field(default_factory=PublishSettings)
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    digital_human: DigitalHumanSettings = field(default_factory=DigitalHumanSettings)
    image_gen: ImageGenSettings = field(default_factory=ImageGenSettings)
    video_gen: VideoGenSettings = field(default_factory=VideoGenSettings)
    video_understand: VideoUnderstandSettings = field(default_factory=VideoUnderstandSettings)
    dubbing: DubbingSettings = field(default_factory=DubbingSettings)
    pet_gen: PetGenSettings = field(default_factory=PetGenSettings)
    skill_gen: SkillGenSettings = field(default_factory=SkillGenSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    surf: SurfSettings = field(default_factory=SurfSettings)
    tracing: TracingSettings = field(default_factory=TracingSettings)


def _num(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else fallback


def _int(value: str | None, fallback: int) -> int:
    return int(_num(value, fallback))


def _bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in ("1", "true", "yes", "on")


def _csv(value: str | None) -> list[str]:
    """把逗号/分号/换行分隔的字符串拆成去空、去重、保序的列表。"""
    seen: set[str] = set()
    items: list[str] = []
    for chunk in (value or "").replace(";", ",").replace("\n", ",").split(","):
        part = chunk.strip()
        if part and part not in seen:
            seen.add(part)
            items.append(part)
    return items


def _build_config() -> RuntimeConfig:
    provider = (os.environ.get("LLM_PROVIDER") or "mock").strip()
    if provider not in ("mock", "openai"):
        provider = "mock"
    return RuntimeConfig(
        port=_int(os.environ.get("PORT"), 8787),
        host=(os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1",
        turn_budget=_int(os.environ.get("TURN_BUDGET"), 25),
        max_revisions=_int(os.environ.get("MAX_REVISIONS"), 2),
        quality_threshold=_int(os.environ.get("QUALITY_THRESHOLD"), 75),
        auto_approve=_bool(os.environ.get("AUTO_APPROVE"), False),
        cost_budget_usd=_num(os.environ.get("COST_BUDGET_USD"), 0.5),
        token_budget=_int(os.environ.get("TOKEN_BUDGET"), 50_000),
        digest_max_calls=max(1, _int(os.environ.get("DIGEST_MAX_CALLS"), 12)),
        llm_cache=_bool(os.environ.get("LLM_CACHE"), True),
        llm=LLMSettings(
            provider=provider,  # type: ignore[arg-type]
            base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY") or "",
            model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            temperature=_num(os.environ.get("LLM_TEMPERATURE"), 0.7),
            max_tokens=_int(os.environ.get("LLM_MAX_TOKENS"), 2048),
            timeout_ms=_int(os.environ.get("LLM_TIMEOUT_MS"), 60_000),
            vision=_bool(os.environ.get("LLM_VISION"), False),
            vision_max_images=min(12, max(0, _int(os.environ.get("LLM_VISION_MAX_IMAGES"), 6))),
            thinking=(
                "enabled"
                if (os.environ.get("LLM_THINKING") or "").strip().lower() == "enabled"
                else "disabled"
            ),
        ),
        embedding=EmbeddingSettings(
            provider=_embedding_provider(),
            base_url=os.environ.get("EMBEDDING_BASE_URL") or "",
            api_key=os.environ.get("EMBEDDING_API_KEY") or "",
            model=os.environ.get("EMBEDDING_MODEL") or "text-embedding-3-small",
            dim=max(16, _int(os.environ.get("EMBEDDING_DIM"), 256)),
            weight=min(1.0, max(0.0, _num(os.environ.get("EMBEDDING_WEIGHT"), 0.35))),
        ),
        publish=PublishSettings(
            webhook_url=os.environ.get("PUBLISH_WEBHOOK_URL") or "",
            retry=max(0, _int(os.environ.get("PUBLISH_RETRY"), 2)),
            auto_dispatch=_bool(os.environ.get("PUBLISH_AUTO_DISPATCH"), False),
            tick_seconds=max(5, _int(os.environ.get("PUBLISH_TICK_SECONDS"), 60)),
        ),
        judge=JudgeSettings(
            mode=_judge_mode(),
            provider=_judge_provider(),
            model=os.environ.get("JUDGE_MODEL") or "",
            pass_threshold=min(
                100.0, max(0.0, _num(os.environ.get("JUDGE_PASS_THRESHOLD"), 75.0))
            ),
            weight=min(1.0, max(0.0, _num(os.environ.get("JUDGE_WEIGHT"), 0.2))),
        ),
        digital_human=DigitalHumanSettings(
            provider=_digital_human_provider(),
            api_url=(os.environ.get("DIGITAL_HUMAN_API_URL") or "").strip(),
            api_key=(os.environ.get("DIGITAL_HUMAN_API_KEY") or "").strip(),
            avatar=(os.environ.get("DIGITAL_HUMAN_AVATAR") or "").strip(),
            timeout_ms=max(1_000, _int(os.environ.get("DIGITAL_HUMAN_TIMEOUT_MS"), 10_000)),
        ),
        image_gen=ImageGenSettings(
            provider=_image_gen_provider(),
            base_url=(os.environ.get("IMAGEGEN_BASE_URL") or "").strip(),
            api_key=(os.environ.get("IMAGEGEN_API_KEY") or "").strip(),
            model=(os.environ.get("IMAGEGEN_MODEL") or "").strip(),
            size=(os.environ.get("IMAGEGEN_SIZE") or "1024x1024").strip() or "1024x1024",
            max_images=min(8, max(1, _int(os.environ.get("IMAGEGEN_MAX_IMAGES"), 4))),
            timeout_ms=max(1_000, _int(os.environ.get("IMAGEGEN_TIMEOUT_MS"), 60_000)),
        ),
        video_gen=VideoGenSettings(
            provider=_video_gen_provider(),
            api_url=(os.environ.get("VIDEOGEN_API_URL") or "").strip(),
            api_key=(os.environ.get("VIDEOGEN_API_KEY") or "").strip(),
            timeout_ms=max(1_000, _int(os.environ.get("VIDEOGEN_TIMEOUT_MS"), 30_000)),
        ),
        video_understand=VideoUnderstandSettings(
            provider=_video_understand_provider(),
            api_url=(os.environ.get("VIDEOUNDERSTAND_API_URL") or "").strip(),
            api_key=(os.environ.get("VIDEOUNDERSTAND_API_KEY") or "").strip(),
            timeout_ms=max(1_000, _int(os.environ.get("VIDEOUNDERSTAND_TIMEOUT_MS"), 120_000)),
        ),
        dubbing=DubbingSettings(
            provider=_dubbing_provider(),
            api_url=(os.environ.get("DUBBING_API_URL") or "").strip(),
            api_key=(os.environ.get("DUBBING_API_KEY") or "").strip(),
            timeout_ms=max(1_000, _int(os.environ.get("DUBBING_TIMEOUT_MS"), 60_000)),
            max_lines=min(1000, max(1, _int(os.environ.get("DUBBING_MAX_LINES"), 200))),
            chars_per_second=min(20.0, max(0.5, _num(os.environ.get("DUBBING_CHARS_PER_SECOND"), 4.5))),
            sovits_base_url=(os.environ.get("DUBBING_SOVITS_BASE_URL") or "").strip(),
            sovits_language=(os.environ.get("DUBBING_SOVITS_LANGUAGE") or "zh").strip() or "zh",
            elevenlabs_base_url=(
                os.environ.get("DUBBING_ELEVENLABS_BASE_URL") or "https://api.elevenlabs.io/v1"
            ).strip().rstrip("/"),
            elevenlabs_api_key=(os.environ.get("DUBBING_ELEVENLABS_API_KEY") or "").strip(),
            elevenlabs_model_id=(
                os.environ.get("DUBBING_ELEVENLABS_MODEL") or "eleven_multilingual_v2"
            ).strip(),
            sapi_voices=(os.environ.get("DUBBING_SAPI_VOICES") or "").strip(),
        ),
        pet_gen=PetGenSettings(
            provider=_pet_gen_provider(),
            frame_size=min(192, max(32, _int(os.environ.get("PETGEN_FRAME_SIZE"), 96))),
            max_frames=min(24, max(1, _int(os.environ.get("PETGEN_MAX_FRAMES"), 16))),
            timeout_ms=max(1_000, _int(os.environ.get("PETGEN_TIMEOUT_MS"), 60_000)),
        ),
        skill_gen=SkillGenSettings(
            provider=_skill_gen_provider(),
            max_works=min(20, max(1, _int(os.environ.get("SKILLGEN_MAX_WORKS"), 12))),
        ),
        search=SearchSettings(
            provider=_search_provider(),
            api_url=(os.environ.get("WEB_SEARCH_API_URL") or "").strip(),
            api_key=(os.environ.get("WEB_SEARCH_API_KEY") or "").strip(),
            max_results=min(20, max(1, _int(os.environ.get("WEB_SEARCH_MAX_RESULTS"), 5))),
            timeout_ms=max(1_000, _int(os.environ.get("WEB_SEARCH_TIMEOUT_MS"), 8_000)),
            fetch_pages=_bool(os.environ.get("WEB_SEARCH_FETCH_PAGES"), True),
            max_pages=min(10, max(0, _int(os.environ.get("WEB_SEARCH_MAX_PAGES"), 2))),
        ),
        surf=SurfSettings(
            provider=_surf_provider(),
            headless=_bool(os.environ.get("SURF_HEADLESS"), True),
            allowed_domains=_csv(os.environ.get("SURF_ALLOWED_DOMAINS")),
            seed_sites=_csv(os.environ.get("SURF_SEED_SITES")),
            topics=_csv(os.environ.get("SURF_TOPICS")),
            max_pages=min(20, max(1, _int(os.environ.get("SURF_MAX_PAGES"), 6))),
            scroll_rounds=min(10, max(0, _int(os.environ.get("SURF_SCROLL_ROUNDS"), 3))),
            settle_ms=min(10_000, max(0, _int(os.environ.get("SURF_SETTLE_MS"), 1200))),
            timeout_ms=max(1_000, _int(os.environ.get("SURF_TIMEOUT_MS"), 30_000)),
            queries_per_topic=min(10, max(1, _int(os.environ.get("SURF_QUERIES_PER_TOPIC"), 3))),
            cards_per_session=min(20, max(1, _int(os.environ.get("SURF_CARDS_PER_SESSION"), 5))),
            pool_max=min(1000, max(10, _int(os.environ.get("SURF_POOL_MAX"), 200))),
            auto_enabled=_bool(os.environ.get("SURF_AUTO_ENABLED"), False),
            tick_seconds=max(60, _int(os.environ.get("SURF_TICK_SECONDS"), 3600)),
            watch_videos=_bool(os.environ.get("SURF_WATCH_VIDEOS"), False),
            video_frames=min(24, max(1, _int(os.environ.get("SURF_VIDEO_FRAMES"), 8))),
            video_frame_interval_ms=min(
                10_000, max(0, _int(os.environ.get("SURF_VIDEO_FRAME_INTERVAL_MS"), 1_200))
            ),
            vlm_group_frames=min(12, max(1, _int(os.environ.get("SURF_VLM_GROUP_FRAMES"), 4))),
            max_videos_per_session=min(
                10, max(1, _int(os.environ.get("SURF_MAX_VIDEOS_PER_SESSION"), 2))
            ),
            scrape_comments=_bool(os.environ.get("SURF_SCRAPE_COMMENTS"), True),
            max_comments=min(50, max(1, _int(os.environ.get("SURF_MAX_COMMENTS"), 12))),
            crop_to_video=_bool(os.environ.get("SURF_CROP_TO_VIDEO"), True),
            click_into_videos=_bool(os.environ.get("SURF_CLICK_INTO_VIDEOS"), True),
            human_pace=_bool(os.environ.get("SURF_HUMAN_PACE"), True),
            search_on_seeds=_bool(os.environ.get("SURF_SEARCH_ON_SEEDS"), True),
            hops_per_page=min(10, max(0, _int(os.environ.get("SURF_HOPS_PER_PAGE"), 2))),
            list_pages=min(8, max(1, _int(os.environ.get("SURF_LIST_PAGES"), 2))),
            page_comments=_bool(os.environ.get("SURF_PAGE_COMMENTS"), True),
            page_gap_ms=min(30_000, max(0, _int(os.environ.get("SURF_PAGE_GAP_MS"), 1_500))),
            topic_focus=_bool(os.environ.get("SURF_TOPIC_FOCUS"), True),
            search_retries=min(3, max(0, _int(os.environ.get("SURF_SEARCH_RETRIES"), 1))),
            use_suggestions=_bool(os.environ.get("SURF_USE_SUGGESTIONS"), True),
            suggestions_max=min(20, max(1, _int(os.environ.get("SURF_SUGGESTIONS_MAX"), 8))),
            avoid_walls=_bool(os.environ.get("SURF_AVOID_WALLS"), True),
            remember_read=_bool(os.environ.get("SURF_REMEMBER_READ"), True),
            history_max=min(5_000, max(20, _int(os.environ.get("SURF_HISTORY_MAX"), 300))),
        ),
        tracing=TracingSettings(
            otlp_endpoint=(os.environ.get("OTLP_ENDPOINT") or "").strip(),
            service_name=(os.environ.get("OTEL_SERVICE_NAME") or "creator-agent-studio").strip()
            or "creator-agent-studio",
            otlp_headers=(os.environ.get("OTLP_HEADERS") or "").strip(),
            sampler=_sampler(),
            sample_ratio=min(1.0, max(0.0, _num(os.environ.get("OTEL_TRACES_SAMPLER_ARG"), 1.0))),
        ),
    )


def _judge_mode() -> str:
    raw = (os.environ.get("JUDGE_MODE") or "advisory").strip().lower()
    return raw if raw in ("off", "advisory", "blocking") else "advisory"


def _judge_provider() -> str:
    raw = (os.environ.get("JUDGE_PROVIDER") or "offline").strip().lower()
    return raw if raw in ("offline", "llm") else "offline"


def _embedding_provider() -> str:
    raw = (os.environ.get("EMBEDDING_PROVIDER") or "local").strip().lower()
    return raw if raw in ("local", "openai") else "local"


def _digital_human_provider() -> str:
    raw = (os.environ.get("DIGITAL_HUMAN_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "http") else "sample"


def _image_gen_provider() -> str:
    raw = (os.environ.get("IMAGEGEN_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "openai", "local") else "sample"


def _video_gen_provider() -> str:
    raw = (os.environ.get("VIDEOGEN_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "http") else "sample"


def _video_understand_provider() -> str:
    raw = (os.environ.get("VIDEOUNDERSTAND_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "real") else "sample"


def _dubbing_provider() -> str:
    raw = (os.environ.get("DUBBING_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "auto", "sapi", "sovits", "elevenlabs", "real") else "sample"


def _pet_gen_provider() -> str:
    raw = (os.environ.get("PETGEN_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "imagegen") else "sample"


def _skill_gen_provider() -> str:
    raw = (os.environ.get("SKILLGEN_PROVIDER") or "rules").strip().lower()
    return raw if raw in ("rules", "llm") else "rules"


def _search_provider() -> str:
    raw = (os.environ.get("WEB_SEARCH_PROVIDER") or "none").strip().lower()
    return raw if raw in ("none", "http") else "none"


def _surf_provider() -> str:
    raw = (os.environ.get("SURF_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "auto", "browser", "http") else "sample"


_SAMPLERS = (
    "parentbased_always_on",
    "parentbased_traceidratio",
    "always_on",
    "always_off",
    "traceidratio",
)


def _sampler() -> str:
    raw = (os.environ.get("OTEL_TRACES_SAMPLER") or "parentbased_always_on").strip().lower()
    return raw if raw in _SAMPLERS else "parentbased_always_on"


_config: RuntimeConfig = _build_config()


# ------------------------------------------------------------------ #
# 设置持久化：字段 → 环境变量映射（环境变量已设置的字段不读回快照）     #
# ------------------------------------------------------------------ #

_RUNTIME_KEYS = (
    "port",
    "host",
    "turnBudget",
    "maxRevisions",
    "qualityThreshold",
    "autoApprove",
    "costBudgetUsd",
    "tokenBudget",
    "digestMaxCalls",
    "llmCache",
)

_FLAT_PERSIST_ENV: dict[str, str] = {
    "port": "PORT",
    "host": "HOST",
    "turnBudget": "TURN_BUDGET",
    "maxRevisions": "MAX_REVISIONS",
    "qualityThreshold": "QUALITY_THRESHOLD",
    "autoApprove": "AUTO_APPROVE",
    "costBudgetUsd": "COST_BUDGET_USD",
    "tokenBudget": "TOKEN_BUDGET",
    "digestMaxCalls": "DIGEST_MAX_CALLS",
    "llmCache": "LLM_CACHE",
    "embeddingProvider": "EMBEDDING_PROVIDER",
    "embeddingBaseUrl": "EMBEDDING_BASE_URL",
    "embeddingApiKey": "EMBEDDING_API_KEY",
    "embeddingModel": "EMBEDDING_MODEL",
    "embeddingDim": "EMBEDDING_DIM",
    "embeddingWeight": "EMBEDDING_WEIGHT",
    "publishWebhookUrl": "PUBLISH_WEBHOOK_URL",
    "publishRetry": "PUBLISH_RETRY",
    "publishAutoDispatch": "PUBLISH_AUTO_DISPATCH",
    "publishTickSeconds": "PUBLISH_TICK_SECONDS",
    "judgeMode": "JUDGE_MODE",
    "judgeProvider": "JUDGE_PROVIDER",
    "judgeModel": "JUDGE_MODEL",
    "judgePassThreshold": "JUDGE_PASS_THRESHOLD",
    "judgeWeight": "JUDGE_WEIGHT",
    "dhProvider": "DIGITAL_HUMAN_PROVIDER",
    "dhApiUrl": "DIGITAL_HUMAN_API_URL",
    "dhApiKey": "DIGITAL_HUMAN_API_KEY",
    "dhAvatar": "DIGITAL_HUMAN_AVATAR",
    "dhTimeoutMs": "DIGITAL_HUMAN_TIMEOUT_MS",
    "imagegenProvider": "IMAGEGEN_PROVIDER",
    "imagegenBaseUrl": "IMAGEGEN_BASE_URL",
    "imagegenApiKey": "IMAGEGEN_API_KEY",
    "imagegenModel": "IMAGEGEN_MODEL",
    "imagegenSize": "IMAGEGEN_SIZE",
    "imagegenMaxImages": "IMAGEGEN_MAX_IMAGES",
    "imagegenTimeoutMs": "IMAGEGEN_TIMEOUT_MS",
    "videogenProvider": "VIDEOGEN_PROVIDER",
    "videogenApiUrl": "VIDEOGEN_API_URL",
    "videogenApiKey": "VIDEOGEN_API_KEY",
    "videogenTimeoutMs": "VIDEOGEN_TIMEOUT_MS",
    "videounderstandProvider": "VIDEOUNDERSTAND_PROVIDER",
    "videounderstandApiUrl": "VIDEOUNDERSTAND_API_URL",
    "videounderstandApiKey": "VIDEOUNDERSTAND_API_KEY",
    "videounderstandTimeoutMs": "VIDEOUNDERSTAND_TIMEOUT_MS",
    "dubbingProvider": "DUBBING_PROVIDER",
    "dubbingApiUrl": "DUBBING_API_URL",
    "dubbingApiKey": "DUBBING_API_KEY",
    "dubbingTimeoutMs": "DUBBING_TIMEOUT_MS",
    "dubbingMaxLines": "DUBBING_MAX_LINES",
    "dubbingCharsPerSecond": "DUBBING_CHARS_PER_SECOND",
    "dubbingSovitsBaseUrl": "DUBBING_SOVITS_BASE_URL",
    "dubbingSovitsLanguage": "DUBBING_SOVITS_LANGUAGE",
    "dubbingElevenlabsBaseUrl": "DUBBING_ELEVENLABS_BASE_URL",
    "dubbingElevenlabsApiKey": "DUBBING_ELEVENLABS_API_KEY",
    "dubbingElevenlabsModel": "DUBBING_ELEVENLABS_MODEL",
    "dubbingSapiVoices": "DUBBING_SAPI_VOICES",
    "petgenProvider": "PETGEN_PROVIDER",
    "petgenFrameSize": "PETGEN_FRAME_SIZE",
    "petgenMaxFrames": "PETGEN_MAX_FRAMES",
    "petgenTimeoutMs": "PETGEN_TIMEOUT_MS",
    "skillgenProvider": "SKILLGEN_PROVIDER",
    "skillgenMaxWorks": "SKILLGEN_MAX_WORKS",
    "surfProvider": "SURF_PROVIDER",
    "surfHeadless": "SURF_HEADLESS",
    "surfAllowedDomains": "SURF_ALLOWED_DOMAINS",
    "surfSeedSites": "SURF_SEED_SITES",
    "surfTopics": "SURF_TOPICS",
    "surfMaxPages": "SURF_MAX_PAGES",
    "surfScrollRounds": "SURF_SCROLL_ROUNDS",
    "surfSettleMs": "SURF_SETTLE_MS",
    "surfTimeoutMs": "SURF_TIMEOUT_MS",
    "surfQueriesPerTopic": "SURF_QUERIES_PER_TOPIC",
    "surfCardsPerSession": "SURF_CARDS_PER_SESSION",
    "surfPoolMax": "SURF_POOL_MAX",
    "surfAutoEnabled": "SURF_AUTO_ENABLED",
    "surfTickSeconds": "SURF_TICK_SECONDS",
    "surfWatchVideos": "SURF_WATCH_VIDEOS",
    "surfVideoFrames": "SURF_VIDEO_FRAMES",
    "surfVideoFrameIntervalMs": "SURF_VIDEO_FRAME_INTERVAL_MS",
    "surfVlmGroupFrames": "SURF_VLM_GROUP_FRAMES",
    "surfMaxVideosPerSession": "SURF_MAX_VIDEOS_PER_SESSION",
    "surfScrapeComments": "SURF_SCRAPE_COMMENTS",
    "surfMaxComments": "SURF_MAX_COMMENTS",
    "surfCropToVideo": "SURF_CROP_TO_VIDEO",
    "surfClickIntoVideos": "SURF_CLICK_INTO_VIDEOS",
    "surfHumanPace": "SURF_HUMAN_PACE",
    "surfSearchOnSeeds": "SURF_SEARCH_ON_SEEDS",
    "surfHopsPerPage": "SURF_HOPS_PER_PAGE",
    "surfListPages": "SURF_LIST_PAGES",
    "surfPageComments": "SURF_PAGE_COMMENTS",
    "surfPageGapMs": "SURF_PAGE_GAP_MS",
    "surfTopicFocus": "SURF_TOPIC_FOCUS",
    "surfSearchRetries": "SURF_SEARCH_RETRIES",
    "surfUseSuggestions": "SURF_USE_SUGGESTIONS",
    "surfSuggestionsMax": "SURF_SUGGESTIONS_MAX",
    "surfAvoidWalls": "SURF_AVOID_WALLS",
    "surfRememberRead": "SURF_REMEMBER_READ",
    "surfHistoryMax": "SURF_HISTORY_MAX",
    "searchProvider": "WEB_SEARCH_PROVIDER",
    "searchApiUrl": "WEB_SEARCH_API_URL",
    "searchApiKey": "WEB_SEARCH_API_KEY",
    "searchMaxResults": "WEB_SEARCH_MAX_RESULTS",
    "searchTimeoutMs": "WEB_SEARCH_TIMEOUT_MS",
    "searchFetchPages": "WEB_SEARCH_FETCH_PAGES",
    "searchMaxPages": "WEB_SEARCH_MAX_PAGES",
    "tracingOtlpEndpoint": "OTLP_ENDPOINT",
    "tracingServiceName": "OTEL_SERVICE_NAME",
    "tracingOtlpHeaders": "OTLP_HEADERS",
    "tracingSampler": "OTEL_TRACES_SAMPLER",
    "tracingSampleRatio": "OTEL_TRACES_SAMPLER_ARG",
}

_LLM_PERSIST_ENV: dict[str, str] = {
    "provider": "LLM_PROVIDER",
    "baseUrl": "OPENAI_BASE_URL",
    "apiKey": "OPENAI_API_KEY",
    "model": "OPENAI_MODEL",
    "temperature": "LLM_TEMPERATURE",
    "maxTokens": "LLM_MAX_TOKENS",
    "timeoutMs": "LLM_TIMEOUT_MS",
    "vision": "LLM_VISION",
    "visionMaxImages": "LLM_VISION_MAX_IMAGES",
    "thinking": "LLM_THINKING",
}


def _load_persisted_settings() -> None:
    """启动时从 ``data/settings.json`` 读回设置界面保存的全量快照。

    优先级：**进程环境变量 > settings.json > .env > 内置默认** —— 环境变量是
    部署方的显式意志（例如 CI 注入 ``LLM_PROVIDER=mock``），必须压过界面保存值，
    门禁才能零 token 验证；因此对应环境变量已设置的字段跳过读回。
    v1 旧文件（仅顶层 siteUrls / agentModels）同样兼容。
    """
    if not SETTINGS_FILE.exists():
        return
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return

    flat: dict[str, Any] = {}
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        for key in _RUNTIME_KEYS:
            if key in runtime and not os.environ.get(_FLAT_PERSIST_ENV[key]):
                flat[key] = runtime[key]

    llm_raw = payload.get("llm")
    if isinstance(llm_raw, dict):
        llm_patch = {
            key: value
            for key, value in llm_raw.items()
            if key in _LLM_PERSIST_ENV and not os.environ.get(_LLM_PERSIST_ENV[key])
        }
        if llm_patch:
            flat["llm"] = llm_patch
        if isinstance(llm_raw.get("agentModels"), dict):
            flat["agentModels"] = llm_raw["agentModels"]
    elif isinstance(payload.get("agentModels"), dict):  # v1 旧文件
        flat["agentModels"] = payload["agentModels"]

    for section, prefix in (
        ("embedding", "embedding"),
        ("publish", "publish"),
        ("judge", "judge"),
        ("digitalHuman", "dh"),
        ("imageGen", "imagegen"),
        ("videoGen", "videogen"),
        ("videoUnderstand", "videounderstand"),
        ("dubbing", "dubbing"),
        ("petGen", "petgen"),
        ("skillGen", "skillgen"),
        ("surf", "surf"),
        ("search", "search"),
        ("tracing", "tracing"),
    ):
        data = payload.get(section)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            flat_key = f"{prefix}{key[0].upper()}{key[1:]}"
            env_var = _FLAT_PERSIST_ENV.get(flat_key)
            if env_var is None or os.environ.get(env_var):
                continue
            flat[flat_key] = value

    site_urls = payload.get("search", {}).get("siteUrls") if isinstance(payload.get("search"), dict) else None
    if not isinstance(site_urls, list):
        site_urls = payload.get("siteUrls")  # v1 旧文件
    if isinstance(site_urls, list):
        flat["siteUrls"] = site_urls

    update_config(flat)


def save_persisted_settings() -> None:
    """把设置界面维护的全量快照原子写入 ``SETTINGS_FILE``（PUT /api/settings 调用）。

    密钥随快照落盘：本文件就是「用户的设置界面」的存储，与本机数据同目录
    （``data/``，不入版本库）；对外 API 仍只回掩码（``public_config``）。
    """
    c = _config
    llm = c.llm
    payload = json.dumps(
        {
            "version": 2,
            "runtime": {
                "port": c.port,
                "host": c.host,
                "turnBudget": c.turn_budget,
                "maxRevisions": c.max_revisions,
                "qualityThreshold": c.quality_threshold,
                "autoApprove": c.auto_approve,
                "costBudgetUsd": c.cost_budget_usd,
                "tokenBudget": c.token_budget,
                "digestMaxCalls": c.digest_max_calls,
                "llmCache": c.llm_cache,
            },
            "llm": {
                "provider": llm.provider,
                "baseUrl": llm.base_url,
                "apiKey": llm.api_key,
                "model": llm.model,
                "temperature": llm.temperature,
                "maxTokens": llm.max_tokens,
                "timeoutMs": llm.timeout_ms,
                "vision": llm.vision,
                "visionMaxImages": llm.vision_max_images,
                "thinking": llm.thinking,
                "agentModels": {
                    aid: {"model": ov.model, "baseUrl": ov.base_url, "apiKey": ov.api_key}
                    for aid, ov in sorted(llm.agent_models.items())
                },
            },
            "embedding": {
                "provider": c.embedding.provider,
                "baseUrl": c.embedding.base_url,
                "apiKey": c.embedding.api_key,
                "model": c.embedding.model,
                "dim": c.embedding.dim,
                "weight": c.embedding.weight,
            },
            "publish": {
                "webhookUrl": c.publish.webhook_url,
                "retry": c.publish.retry,
                "autoDispatch": c.publish.auto_dispatch,
                "tickSeconds": c.publish.tick_seconds,
            },
            "judge": {
                "mode": c.judge.mode,
                "provider": c.judge.provider,
                "model": c.judge.model,
                "passThreshold": c.judge.pass_threshold,
                "weight": c.judge.weight,
            },
            "digitalHuman": {
                "provider": c.digital_human.provider,
                "apiUrl": c.digital_human.api_url,
                "apiKey": c.digital_human.api_key,
                "avatar": c.digital_human.avatar,
                "timeoutMs": c.digital_human.timeout_ms,
            },
            "imageGen": {
                "provider": c.image_gen.provider,
                "baseUrl": c.image_gen.base_url,
                "apiKey": c.image_gen.api_key,
                "model": c.image_gen.model,
                "size": c.image_gen.size,
                "maxImages": c.image_gen.max_images,
                "timeoutMs": c.image_gen.timeout_ms,
            },
            "videoGen": {
                "provider": c.video_gen.provider,
                "apiUrl": c.video_gen.api_url,
                "apiKey": c.video_gen.api_key,
                "timeoutMs": c.video_gen.timeout_ms,
            },
            "videoUnderstand": {
                "provider": c.video_understand.provider,
                "apiUrl": c.video_understand.api_url,
                "apiKey": c.video_understand.api_key,
                "timeoutMs": c.video_understand.timeout_ms,
            },
            "dubbing": {
                "provider": c.dubbing.provider,
                "apiUrl": c.dubbing.api_url,
                "apiKey": c.dubbing.api_key,
                "timeoutMs": c.dubbing.timeout_ms,
                "maxLines": c.dubbing.max_lines,
                "charsPerSecond": c.dubbing.chars_per_second,
                "sovitsBaseUrl": c.dubbing.sovits_base_url,
                "sovitsLanguage": c.dubbing.sovits_language,
                "elevenlabsBaseUrl": c.dubbing.elevenlabs_base_url,
                "elevenlabsApiKey": c.dubbing.elevenlabs_api_key,
                "elevenlabsModel": c.dubbing.elevenlabs_model_id,
                "sapiVoices": c.dubbing.sapi_voices,
            },
            "petGen": {
                "provider": c.pet_gen.provider,
                "frameSize": c.pet_gen.frame_size,
                "maxFrames": c.pet_gen.max_frames,
                "timeoutMs": c.pet_gen.timeout_ms,
            },
            "skillGen": {
                "provider": c.skill_gen.provider,
                "maxWorks": c.skill_gen.max_works,
            },
            "surf": {
                "provider": c.surf.provider,
                "headless": c.surf.headless,
                "allowedDomains": list(c.surf.allowed_domains),
                "seedSites": list(c.surf.seed_sites),
                "topics": list(c.surf.topics),
                "maxPages": c.surf.max_pages,
                "scrollRounds": c.surf.scroll_rounds,
                "settleMs": c.surf.settle_ms,
                "timeoutMs": c.surf.timeout_ms,
                "queriesPerTopic": c.surf.queries_per_topic,
                "cardsPerSession": c.surf.cards_per_session,
                "poolMax": c.surf.pool_max,
                "autoEnabled": c.surf.auto_enabled,
                "tickSeconds": c.surf.tick_seconds,
                "watchVideos": c.surf.watch_videos,
                "videoFrames": c.surf.video_frames,
                "videoFrameIntervalMs": c.surf.video_frame_interval_ms,
                "vlmGroupFrames": c.surf.vlm_group_frames,
                "maxVideosPerSession": c.surf.max_videos_per_session,
                "scrapeComments": c.surf.scrape_comments,
                "maxComments": c.surf.max_comments,
                "cropToVideo": c.surf.crop_to_video,
                "clickIntoVideos": c.surf.click_into_videos,
                "humanPace": c.surf.human_pace,
                "searchOnSeeds": c.surf.search_on_seeds,
                "hopsPerPage": c.surf.hops_per_page,
                "listPages": c.surf.list_pages,
                "pageComments": c.surf.page_comments,
                "pageGapMs": c.surf.page_gap_ms,
                "topicFocus": c.surf.topic_focus,
                "searchRetries": c.surf.search_retries,
                "useSuggestions": c.surf.use_suggestions,
                "suggestionsMax": c.surf.suggestions_max,
                "avoidWalls": c.surf.avoid_walls,
                "rememberRead": c.surf.remember_read,
                "historyMax": c.surf.history_max,
            },
            "search": {
                "provider": c.search.provider,
                "apiUrl": c.search.api_url,
                "apiKey": c.search.api_key,
                "maxResults": c.search.max_results,
                "timeoutMs": c.search.timeout_ms,
                "fetchPages": c.search.fetch_pages,
                "maxPages": c.search.max_pages,
                "siteUrls": list(c.search.site_urls),
            },
            "tracing": {
                "otlpEndpoint": c.tracing.otlp_endpoint,
                "serviceName": c.tracing.service_name,
                "otlpHeaders": c.tracing.otlp_headers,
                "sampler": c.tracing.sampler,
                "sampleRatio": c.tracing.sample_ratio,
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


def get_config() -> RuntimeConfig:
    """返回全局配置单例（就地修改）。"""
    return _config


def llm_settings_for(agent_id: str) -> LLMSettings:
    """按智能体合并 LLM 设置：该 id 无覆盖（或覆盖字段全空）时返回全局设置。"""
    override = _config.llm.agent_models.get((agent_id or "").strip().upper())
    if override is None:
        return _config.llm
    patch: dict[str, str] = {}
    if override.model:
        patch["model"] = override.model
    if override.base_url:
        patch["base_url"] = override.base_url
    if override.api_key:
        patch["api_key"] = override.api_key
    return replace(_config.llm, **patch) if patch else _config.llm


def update_config(patch: dict[str, Any]) -> RuntimeConfig:
    """按 patch 就地更新配置；`llm` 子对象做浅合并。"""
    if patch.get("turnBudget") is not None:
        _config.turn_budget = int(patch["turnBudget"])
    if patch.get("maxRevisions") is not None:
        _config.max_revisions = int(patch["maxRevisions"])
    if patch.get("qualityThreshold") is not None:
        _config.quality_threshold = int(patch["qualityThreshold"])
    if patch.get("autoApprove") is not None:
        _config.auto_approve = bool(patch["autoApprove"])
    if patch.get("costBudgetUsd") is not None:
        _config.cost_budget_usd = max(0.0, float(patch["costBudgetUsd"]))
    if patch.get("tokenBudget") is not None:
        _config.token_budget = max(0, int(patch["tokenBudget"]))
    if patch.get("digestMaxCalls") is not None:
        _config.digest_max_calls = max(1, min(60, int(patch["digestMaxCalls"])))
    if patch.get("llmCache") is not None:
        _config.llm_cache = bool(patch["llmCache"])
    # host / port 是启动期参数：写入后随快照持久化，**下次启动生效**
    if isinstance(patch.get("host"), str) and patch["host"].strip():
        _config.host = patch["host"].strip()
    if patch.get("port") is not None:
        try:
            _config.port = max(1, min(65535, int(patch["port"])))
        except (TypeError, ValueError):
            pass
    llm_patch = patch.get("llm") or {}
    if llm_patch:
        for key, value in llm_patch.items():
            attr = _LLM_FIELD_BY_CAMEL.get(key)
            if attr is None or value is None:
                continue
            if attr == "provider" and value not in ("mock", "openai"):
                continue
            if attr == "thinking" and value not in ("enabled", "disabled"):
                continue
            if attr == "vision":
                value = bool(value)
            elif attr == "vision_max_images":
                value = min(8, max(0, _int(str(value), 3))) if isinstance(value, (int, float, str)) else 3
            setattr(_config.llm, attr, value)
    # 每智能体模型覆盖：整体替换（前端恒传全量，缺失的条目即被清除）。
    # apiKey 缺失 = 保留已存值；显式 "" = 清除（继承全局）——前端只在用户
    # 输入非空时才带该键，防止「打开设置点保存」误清已存的覆盖密钥。
    if isinstance(patch.get("agentModels"), dict):
        merged: dict[str, AgentModelOverride] = {}
        for raw_id, raw_spec in patch["agentModels"].items():
            aid = str(raw_id).strip().upper()
            if not (aid.startswith("A") and aid[1:].isdigit()):
                continue
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            existing = _config.llm.agent_models.get(aid)
            api_key = (
                str(spec["apiKey"]).strip()
                if "apiKey" in spec
                else (existing.api_key if existing else "")
            )
            entry = AgentModelOverride(
                model=str(spec.get("model") or "").strip(),
                base_url=str(spec.get("baseUrl") or "").strip(),
                api_key=api_key,
            )
            if entry.model or entry.base_url or entry.api_key:
                merged[aid] = entry
        _config.llm.agent_models = merged
    _apply_flat(
        patch,
        _config.embedding,
        {
            "embeddingProvider": ("provider", ("local", "openai")),
            "embeddingBaseUrl": ("base_url", None),
            "embeddingApiKey": ("api_key", None),
            "embeddingModel": ("model", None),
            "embeddingDim": ("dim", None),
            "embeddingWeight": ("weight", None),
        },
    )
    _apply_flat(
        patch,
        _config.publish,
        {
            "publishWebhookUrl": ("webhook_url", None),
            "publishRetry": ("retry", None),
            "publishAutoDispatch": ("auto_dispatch", None),
            "publishTickSeconds": ("tick_seconds", None),
        },
    )
    _apply_flat(
        patch,
        _config.judge,
        {
            "judgeMode": ("mode", ("off", "advisory", "blocking")),
            "judgeProvider": ("provider", ("offline", "llm")),
            "judgeModel": ("model", None),
            "judgePassThreshold": ("pass_threshold", None),
            "judgeWeight": ("weight", None),
        },
    )
    _apply_flat(
        patch,
        _config.digital_human,
        {
            "dhProvider": ("provider", ("sample", "http")),
            "dhApiUrl": ("api_url", None),
            "dhApiKey": ("api_key", None),
            "dhAvatar": ("avatar", None),
            "dhTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.image_gen,
        {
            "imagegenProvider": ("provider", ("sample", "openai", "local")),
            "imagegenBaseUrl": ("base_url", None),
            "imagegenApiKey": ("api_key", None),
            "imagegenModel": ("model", None),
            "imagegenSize": ("size", None),
            "imagegenMaxImages": ("max_images", None),
            "imagegenTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.video_gen,
        {
            "videogenProvider": ("provider", ("sample", "http")),
            "videogenApiUrl": ("api_url", None),
            "videogenApiKey": ("api_key", None),
            "videogenTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.video_understand,
        {
            "videounderstandProvider": ("provider", ("sample", "real")),
            "videounderstandApiUrl": ("api_url", None),
            "videounderstandApiKey": ("api_key", None),
            "videounderstandTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.dubbing,
        {
            "dubbingProvider": ("provider", ("sample", "auto", "sapi", "sovits", "elevenlabs", "real")),
            "dubbingApiUrl": ("api_url", None),
            "dubbingApiKey": ("api_key", None),
            "dubbingTimeoutMs": ("timeout_ms", None),
            "dubbingMaxLines": ("max_lines", None),
            "dubbingCharsPerSecond": ("chars_per_second", None),
            "dubbingSovitsBaseUrl": ("sovits_base_url", None),
            "dubbingSovitsLanguage": ("sovits_language", None),
            "dubbingElevenlabsBaseUrl": ("elevenlabs_base_url", None),
            "dubbingElevenlabsApiKey": ("elevenlabs_api_key", None),
            "dubbingElevenlabsModel": ("elevenlabs_model_id", None),
            "dubbingSapiVoices": ("sapi_voices", None),
        },
    )
    _apply_flat(
        patch,
        _config.pet_gen,
        {
            "petgenProvider": ("provider", ("sample", "imagegen")),
            "petgenFrameSize": ("frame_size", None),
            "petgenMaxFrames": ("max_frames", None),
            "petgenTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.skill_gen,
        {
            "skillgenProvider": ("provider", ("rules", "llm")),
            "skillgenMaxWorks": ("max_works", None),
        },
    )
    _apply_flat(
        patch,
        _config.surf,
        {
            "surfProvider": ("provider", ("sample", "auto", "browser", "http")),
            "surfHeadless": ("headless", None),
            # 白名单/起点站/话题为字符串列表，逐项校验与去重在路由层做，这里原样写入（空数组 = 清空）
            "surfAllowedDomains": ("allowed_domains", None),
            "surfSeedSites": ("seed_sites", None),
            "surfTopics": ("topics", None),
            "surfMaxPages": ("max_pages", None),
            "surfScrollRounds": ("scroll_rounds", None),
            "surfSettleMs": ("settle_ms", None),
            "surfTimeoutMs": ("timeout_ms", None),
            "surfQueriesPerTopic": ("queries_per_topic", None),
            "surfCardsPerSession": ("cards_per_session", None),
            "surfPoolMax": ("pool_max", None),
            "surfAutoEnabled": ("auto_enabled", None),
            "surfTickSeconds": ("tick_seconds", None),
            "surfWatchVideos": ("watch_videos", None),
            "surfVideoFrames": ("video_frames", None),
            "surfVideoFrameIntervalMs": ("video_frame_interval_ms", None),
            "surfVlmGroupFrames": ("vlm_group_frames", None),
            "surfMaxVideosPerSession": ("max_videos_per_session", None),
            "surfScrapeComments": ("scrape_comments", None),
            "surfMaxComments": ("max_comments", None),
            "surfCropToVideo": ("crop_to_video", None),
            "surfClickIntoVideos": ("click_into_videos", None),
            "surfHumanPace": ("human_pace", None),
            "surfSearchOnSeeds": ("search_on_seeds", None),
            "surfHopsPerPage": ("hops_per_page", None),
            "surfListPages": ("list_pages", None),
            "surfPageComments": ("page_comments", None),
            "surfPageGapMs": ("page_gap_ms", None),
            "surfTopicFocus": ("topic_focus", None),
            "surfSearchRetries": ("search_retries", None),
            "surfUseSuggestions": ("use_suggestions", None),
            "surfSuggestionsMax": ("suggestions_max", None),
            "surfAvoidWalls": ("avoid_walls", None),
            "surfRememberRead": ("remember_read", None),
            "surfHistoryMax": ("history_max", None),
        },
    )
    _apply_flat(
        patch,
        _config.search,
        {
            "searchProvider": ("provider", ("none", "http")),
            "searchApiUrl": ("api_url", None),
            "searchApiKey": ("api_key", None),
            "searchMaxResults": ("max_results", None),
            "searchTimeoutMs": ("timeout_ms", None),
            "searchFetchPages": ("fetch_pages", None),
            "searchMaxPages": ("max_pages", None),
            # site_urls 的逐项校验与去重在 routes 层做，这里原样写入（空数组 = 清空）
            "siteUrls": ("site_urls", None),
        },
    )
    _apply_flat(
        patch,
        _config.tracing,
        {
            "tracingOtlpEndpoint": ("otlp_endpoint", None),
            "tracingServiceName": ("service_name", None),
            "tracingOtlpHeaders": ("otlp_headers", None),
            "tracingSampler": ("sampler", _SAMPLERS),
            "tracingSampleRatio": ("sample_ratio", None),
        },
    )
    return _config


def _apply_flat(patch: dict[str, Any], target: Any, mapping: dict[str, tuple[str, tuple[str, ...] | None]]) -> None:
    """把扁平的 camelCase patch 写进目标设置对象，非法值静默跳过。"""
    for key, (attr, allowed) in mapping.items():
        value = patch.get(key)
        if value is None:
            continue
        if allowed is not None and value not in allowed:
            continue
        if attr == "dim":
            value = max(16, int(value))
        elif attr in ("weight", "pass_threshold"):
            # 0–1 的权重与 0–100 的分数线都按上界收敛，越界值直接夹紧而不是报错
            value = min(100.0 if attr == "pass_threshold" else 1.0, max(0.0, float(value)))
        elif attr == "retry":
            value = max(0, int(value))
        elif attr == "tick_seconds":
            value = max(1, int(value))
        elif attr == "max_results":
            value = min(20, max(1, int(value)))
        elif attr == "timeout_ms":
            value = max(1_000, int(value))
        elif attr == "max_pages":
            value = min(10, max(0, int(value)))
        elif attr == "frame_size":
            value = min(192, max(32, int(value)))
        elif attr == "max_frames":
            value = min(24, max(1, int(value)))
        elif attr == "max_images":
            value = min(8, max(1, int(value)))
        elif attr == "max_works":
            value = min(20, max(1, int(value)))
        elif attr == "video_frames":
            value = min(24, max(1, int(value)))
        elif attr == "vlm_group_frames":
            value = min(12, max(1, int(value)))
        elif attr == "max_videos_per_session":
            value = min(10, max(1, int(value)))
        elif attr == "max_comments":
            value = min(50, max(1, int(value)))
        elif attr == "hops_per_page":
            value = min(10, max(0, int(value)))
        elif attr == "list_pages":
            value = min(8, max(1, int(value)))
        elif attr == "search_retries":
            value = min(3, max(0, int(value)))
        elif attr == "suggestions_max":
            value = min(20, max(1, int(value)))
        elif attr == "history_max":
            value = min(5_000, max(20, int(value)))
        elif attr == "page_gap_ms":
            value = min(30_000, max(0, int(value)))
        elif attr == "max_frames":
            value = min(64, max(1, int(value)))
        elif attr == "sample_ratio":
            value = min(1.0, max(0.0, float(value)))
        setattr(target, attr, value)


_LLM_FIELD_BY_CAMEL = {
    "provider": "provider",
    "baseUrl": "base_url",
    "apiKey": "api_key",
    "model": "model",
    "temperature": "temperature",
    "maxTokens": "max_tokens",
    "timeoutMs": "timeout_ms",
    "vision": "vision",
    "visionMaxImages": "vision_max_images",
    "thinking": "thinking",
}

# 模块尾部统一执行：读回持久化快照（update_config 在上方已定义）
_load_persisted_settings()


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:4]}{'*' * max(0, len(secret) - 8)}{secret[-4:]}"


def _playwright_available() -> bool:
    """是否装了 Playwright（browser 冲浪通道的真引擎）。仅探测 spec，**不 import**（零副作用）。"""
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def public_config() -> dict[str, Any]:
    """对外输出配置，隐藏密钥明文（仅回传掩码与是否已设置）。"""
    llm = _config.llm
    embedding = _config.embedding
    publish = _config.publish
    judge = _config.judge
    digital_human = _config.digital_human
    tracing = _config.tracing
    return {
        "port": _config.port,
        "host": _config.host,
        "turnBudget": _config.turn_budget,
        "maxRevisions": _config.max_revisions,
        "qualityThreshold": _config.quality_threshold,
        "autoApprove": _config.auto_approve,
        "costBudgetUsd": _config.cost_budget_usd,
        "tokenBudget": _config.token_budget,
        "digestMaxCalls": _config.digest_max_calls,
        "llmCache": _config.llm_cache,
        "authRequired": auth_enabled(),
        # 存储后端概要：mode 用于前端/运维确认当前落库方式；
        # 只暴露「是否已配置连接串」的布尔值，URL 含密钥，绝不回传明文（与 _mask 同口径）
        "storage": {
            "mode": STORAGE_MODE,
            "databaseConfigured": bool(DATABASE_URL),
        },
        "llm": {
            "provider": llm.provider,
            "baseUrl": llm.base_url,
            "apiKey": "",
            "model": llm.model,
            "temperature": llm.temperature,
            "maxTokens": llm.max_tokens,
            "timeoutMs": llm.timeout_ms,
            "vision": llm.vision,
            "visionMaxImages": llm.vision_max_images,
            "thinking": llm.thinking,
            "apiKeySet": len(llm.api_key) > 0,
            "apiKeyMasked": _mask(llm.api_key),
            # 每智能体模型覆盖：只回显 model / baseUrl 与密钥掩码，绝不回明文
            "agentModels": {
                aid: {
                    "model": ov.model,
                    "baseUrl": ov.base_url,
                    "apiKeySet": len(ov.api_key) > 0,
                    "apiKeyMasked": _mask(ov.api_key),
                }
                for aid, ov in sorted(llm.agent_models.items())
            },
        },
        "embedding": {
            "provider": embedding.provider,
            "baseUrl": embedding.base_url,
            "model": embedding.model,
            "dim": embedding.dim,
            "weight": embedding.weight,
            "apiKey": "",
            "apiKeySet": len(embedding.api_key) > 0,
            "apiKeyMasked": _mask(embedding.api_key),
        },
        "publish": {
            "webhookUrl": publish.webhook_url,
            "retry": publish.retry,
            "autoDispatch": publish.auto_dispatch,
            "tickSeconds": publish.tick_seconds,
            "webhookSet": bool(publish.webhook_url),
        },
        "judge": {
            "mode": judge.mode,
            "provider": judge.provider,
            "model": judge.model,
            "passThreshold": judge.pass_threshold,
            "weight": judge.weight,
            "rubric": RUBRIC_VERSION,
        },
        "digitalHuman": {
            "provider": digital_human.provider,
            "apiUrl": digital_human.api_url,
            "avatar": digital_human.avatar,
            "apiKey": "",
            "apiKeySet": len(digital_human.api_key) > 0,
            "apiKeyMasked": _mask(digital_human.api_key),
            "timeoutMs": digital_human.timeout_ms,
        },
        "imageGen": {
            "provider": _config.image_gen.provider,
            "baseUrl": _config.image_gen.base_url,
            "model": _config.image_gen.model,
            "size": _config.image_gen.size,
            "maxImages": _config.image_gen.max_images,
            "apiKey": "",
            "apiKeySet": len(_config.image_gen.api_key) > 0,
            "apiKeyMasked": _mask(_config.image_gen.api_key),
            "timeoutMs": _config.image_gen.timeout_ms,
            "configured": (
                _config.image_gen.provider == "sample"
                or bool(_config.image_gen.base_url)
            ),
        },
        "videoGen": {
            "provider": _config.video_gen.provider,
            "apiUrl": _config.video_gen.api_url,
            "apiKey": "",
            "apiKeySet": len(_config.video_gen.api_key) > 0,
            "apiKeyMasked": _mask(_config.video_gen.api_key),
            "timeoutMs": _config.video_gen.timeout_ms,
        },
        "videoUnderstand": {
            "provider": _config.video_understand.provider,
            "apiUrl": _config.video_understand.api_url,
            "apiKey": "",
            "apiKeySet": len(_config.video_understand.api_key) > 0,
            "apiKeyMasked": _mask(_config.video_understand.api_key),
            "timeoutMs": _config.video_understand.timeout_ms,
            "configured": (
                _config.video_understand.provider == "sample"
                or bool(_config.video_understand.api_url)
            ),
        },
        "dubbing": {
            "provider": _config.dubbing.provider,
            "apiUrl": _config.dubbing.api_url,
            "apiKey": "",
            "apiKeySet": len(_config.dubbing.api_key) > 0,
            "sovitsBaseUrl": _config.dubbing.sovits_base_url,
            "sovitsLanguage": _config.dubbing.sovits_language,
            "elevenlabsBaseUrl": _config.dubbing.elevenlabs_base_url,
            "elevenlabsApiKey": "",
            "elevenlabsApiKeySet": len(_config.dubbing.elevenlabs_api_key) > 0,
            "elevenlabsModel": _config.dubbing.elevenlabs_model_id,
            "sapiVoices": _config.dubbing.sapi_voices,
            "engines": {
                "elevenlabs": len(_config.dubbing.elevenlabs_api_key) > 0,
                "sovits": bool(_config.dubbing.sovits_base_url),
                "real": bool(_config.dubbing.api_url),
            },
            "apiKeyMasked": _mask(_config.dubbing.api_key),
            "timeoutMs": _config.dubbing.timeout_ms,
            "maxLines": _config.dubbing.max_lines,
            "charsPerSecond": _config.dubbing.chars_per_second,
            "configured": (
                _config.dubbing.provider in ("sample", "sapi", "auto")
                or bool(_config.dubbing.api_url)
                or bool(_config.dubbing.sovits_base_url)
                or bool(_config.dubbing.elevenlabs_api_key)
            ),
        },
        "petGen": {
            "provider": _config.pet_gen.provider,
            "frameSize": _config.pet_gen.frame_size,
            "maxFrames": _config.pet_gen.max_frames,
            "timeoutMs": _config.pet_gen.timeout_ms,
            # 真实通道的可用性由图片通道决定（桌宠不另持端点，避免两处配置漂移）
            "configured": (
                _config.pet_gen.provider == "sample"
                or (_config.image_gen.provider != "sample" and bool(_config.image_gen.base_url))
            ),
        },
        "skillGen": {
            "provider": _config.skill_gen.provider,
            "maxWorks": _config.skill_gen.max_works,
            # llm 通道复用 LLM_*：网关没配好就是「不可用」，界面据此提示改走 rules
            "configured": (
                _config.skill_gen.provider == "rules"
                or (_config.llm.provider == "openai" and bool(_config.llm.base_url))
            ),
        },
        "surf": {
            "provider": _config.surf.provider,
            "headless": _config.surf.headless,
            "allowedDomains": list(_config.surf.allowed_domains),
            "seedSites": list(_config.surf.seed_sites),
            "topics": list(_config.surf.topics),
            "maxPages": _config.surf.max_pages,
            "scrollRounds": _config.surf.scroll_rounds,
            "settleMs": _config.surf.settle_ms,
            "timeoutMs": _config.surf.timeout_ms,
            "queriesPerTopic": _config.surf.queries_per_topic,
            "cardsPerSession": _config.surf.cards_per_session,
            "poolMax": _config.surf.pool_max,
            "autoEnabled": _config.surf.auto_enabled,
            "tickSeconds": _config.surf.tick_seconds,
            "watchVideos": _config.surf.watch_videos,
            "videoFrames": _config.surf.video_frames,
            "videoFrameIntervalMs": _config.surf.video_frame_interval_ms,
            "vlmGroupFrames": _config.surf.vlm_group_frames,
            "maxVideosPerSession": _config.surf.max_videos_per_session,
            "scrapeComments": _config.surf.scrape_comments,
            "maxComments": _config.surf.max_comments,
            "cropToVideo": _config.surf.crop_to_video,
            "clickIntoVideos": _config.surf.click_into_videos,
            "humanPace": _config.surf.human_pace,
            "searchOnSeeds": _config.surf.search_on_seeds,
            "hopsPerPage": _config.surf.hops_per_page,
            "listPages": _config.surf.list_pages,
            "pageComments": _config.surf.page_comments,
            "pageGapMs": _config.surf.page_gap_ms,
            "topicFocus": _config.surf.topic_focus,
            "searchRetries": _config.surf.search_retries,
            "useSuggestions": _config.surf.use_suggestions,
            "suggestionsMax": _config.surf.suggestions_max,
            "avoidWalls": _config.surf.avoid_walls,
            "rememberRead": _config.surf.remember_read,
            "historyMax": _config.surf.history_max,
            # browser 通道真能冲浪 = 装了 Playwright 且配了域名白名单；否则 auto 会回退
            "browserAvailable": _playwright_available(),
            # 在线看视频的前置：装了 Playwright + 配了白名单（起点站或请求里的视频链接均可）；
            # 画面是否**真看懂**还取决于 visionConfigured，两者缺一即如实降级/失败
            "watchReady": _playwright_available() and bool(_config.surf.allowed_domains),
            "visionConfigured": _config.llm.provider == "openai" and bool(_config.llm.vision),
            "configured": (
                _config.surf.provider in ("sample", "auto")
                or (_config.surf.provider == "browser" and _playwright_available() and bool(_config.surf.allowed_domains))
                or (_config.surf.provider == "http" and _config.search.provider == "http" and bool(_config.search.api_url))
            ),
        },
        "search": {
            "provider": _config.search.provider,
            "apiUrl": _config.search.api_url,
            "apiKey": "",
            "apiKeySet": len(_config.search.api_key) > 0,
            "apiKeyMasked": _mask(_config.search.api_key),
            "maxResults": _config.search.max_results,
            "timeoutMs": _config.search.timeout_ms,
            "fetchPages": _config.search.fetch_pages,
            "maxPages": _config.search.max_pages,
            "siteUrls": list(_config.search.site_urls),
            "configured": _config.search.provider == "http" and bool(_config.search.api_url),
        },
        "tracing": {
            "otlpEndpoint": tracing.otlp_endpoint,
            "serviceName": tracing.service_name,
            "otlpConfigured": bool(tracing.otlp_endpoint),
            "otlpHeadersSet": bool(tracing.otlp_headers),
            "sampler": tracing.sampler,
            "sampleRatio": tracing.sample_ratio,
        },
    }


# ------------------------------------------------------------------ #
# API 鉴权（plan.md D17「安全加固」）                                  #
# ------------------------------------------------------------------ #


def _parse_api_tokens(raw: str) -> dict[str, str]:
    """解析 ``CREATOR_API_TOKENS``，返回 ``token → 租户名`` 映射。

    支持两种写法：``tok1,tok2``（租户均为 ``default``）与
    ``acme:tok1,beta:tok2``（显式指定租户，用于数据隔离）。
    """
    tokens: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        if ":" in item:
            tenant, _, token = item.partition(":")
            tenant, token = tenant.strip(), token.strip()
        else:
            tenant, token = "default", item
        if token:
            tokens[token] = tenant or "default"
    return tokens


#: API Token → 租户名。为空表示不鉴权（单机默认；见 plan.md D17）
API_TOKENS: dict[str, str] = _parse_api_tokens(os.environ.get("CREATOR_API_TOKENS") or "")


def auth_enabled() -> bool:
    """是否已通过 ``CREATOR_API_TOKENS`` 开启 API 鉴权。"""
    return bool(API_TOKENS)


def ensure_dirs() -> None:
    """确保持久化目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
