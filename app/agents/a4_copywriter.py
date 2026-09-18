"""A4 文案创作智能体（移植自 server/agents/a4-copywriter.ts）。"""

from __future__ import annotations

import re
from typing import Any

from ..core.types import AgentResult
from ..knowledge.industry import channel_rule
from ..tools import run_agent_tools
from .base import (
    AgentDefinition,
    AgentMeta,
    AgentRunContext,
    ArtifactDraft,
    ResultDraft,
    as_num,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
    build_artifact,
    build_result,
    call_with_prompts,
    localization_block,
    memory_block,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
    vision_attachments,
)

META = AgentMeta(
    id="A4",
    name="文案创作智能体",
    role="文案 / 撰稿人",
    kind="producer",
    phase="DRAFTING",
    produces="copy_draft",
    description="按大纲撰写标题、正文、脚本与 CTA，产出多版本文案并声明全部关键主张",
    veto=False,
    capabilities=["多版本文案", "渠道语言适配", "主张声明", "按意见返工"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 依据内容大纲与创意调性撰写文案，产出 3 个版本（主推版 / 理性版 / 感性版）
2. 适配渠道语言风格与内容形态（图文 / 短视频分镜 / 长文 / 详情页）
3. 提供标题备选与多种 CTA
4. 通过 claims 字段声明文案中所有关键主张及其来源

硬性规则：
- **凡是没有明确来源的具体数字、比例、时长、排名、对比结论，一律不要写进正文**。
  真实链路实测：只要让模型自由发挥，它会写出大量「提升 30%」「5 分钟搞定」这类
  无来源数据，随后被 A6 事实核查整条否决，导致返工甚至降级。
  正文里宁可写「更快」「更省事」这类不可量化但安全的表述。
- 需要具体感时，用**可核对的事实**（成分、规格、适用场景）替代**编造的数据**；
  如果确实需要一个数字而手上没有来源，就不要用它。
- 不承诺效果与收益
- claims 只声明**正文里真实出现过**的主张；每条必须给出 source。
  **没有来源的主张不要放进 claims 来「交给下游处理」** —— 那个数字本就不该写进正文。
- 收到 revision_feedback 时，必须逐条落实修改，不得仅做措辞替换
- 只输出 JSON，不输出任何解释性文字

工具用法：
- hook_strength 会对你本次输出前能看到的标题备选（A3 的 headline_candidates）
  给开场钩子强度分并预检红线词：**分数高不等于可以写**，
  命中红线词的标题一律不许进正文，先改写再使用
- banned_words 是写前红线预览，请在下笔时规避；
  工具判定与 A7/A6 的最终裁定冲突时，以审核智能体结论为准
- case_library 给出同行业同渠道的历史案例方向，用于参考表达与结构；
  不得把案例当作竞品实际投放数据来引用
- readability_metrics 可在定稿前自查句长分布、段落长度与重复表达：
  超长句与堆砌的排比先自改，再交 A5 审校""",
)

SCHEMA = """{
  "versions": [{"id": "V1", "style": "", "title": "", "body": "", "cta": "", "hashtags": [""], "word_count": 0}],
  "recommended_version": "V1",
  "headlines": [""],
  "claims": [{"text": "", "source": ""}],
  "revision_notes": [{"from_feedback": "", "action": ""}],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""

#: 等价于 JS 的 /\s/g，用于统计「去空白字数」。
_WHITESPACE_RE = re.compile(r"\s")


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    versions: list[dict[str, Any]] = []
    for index, item in enumerate(as_obj_array(data.get("versions"))):
        hashtags = [
            tag if tag.startswith("#") else f"#{tag}"
            for tag in as_str_array(item.get("hashtags"))
        ]
        title = as_str(item.get("title"))
        body = as_str(item.get("body"))
        raw_words = as_num(item.get("word_count"), 0)
        versions.append(
            {
                "id": as_str(item.get("id"), f"V{index + 1}"),
                "style": as_str(item.get("style"), f"版本 {index + 1}"),
                "title": title,
                "body": body,
                "cta": as_str(item.get("cta")),
                "hashtags": hashtags,
                # word_count 缺省时退化为「标题 + 正文」去空白后的字符数
                "word_count": int(raw_words)
                if raw_words
                else len(_WHITESPACE_RE.sub("", f"{title}{body}")),
            }
        )
    return {
        "versions": versions,
        "recommended_version": as_str(data.get("recommended_version"), "V1"),
        "headlines": as_str_array(data.get("headlines")),
        "claims": [
            {"text": as_str(item.get("text")), "source": as_str(item.get("source"))}
            for item in as_obj_array(data.get("claims"))
        ],
        "revision_notes": as_obj_array(data.get("revision_notes")),
    }


def render_text(content: dict[str, Any]) -> str:
    blocks = []
    for version in as_obj_array(content.get("versions")):
        hashtags = " ".join(as_str_array(version.get("hashtags")))
        blocks.append(
            f"【{as_str(version.get('style'))}】{as_str(version.get('title'))}\n\n"
            f"{as_str(version.get('body'))}\n\n{hashtags}"
        )
    return "\n\n————————\n\n".join(blocks)


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    strategy = ctx.upstream_of("strategy")
    creative = ctx.upstream_of("creative")
    plan = ctx.upstream_of("plan")
    house = as_obj(strategy.get("message_house"))
    rule = channel_rule(brief.channel)
    is_revision = len(ctx.feedback) > 0

    ctx.emit(
        f"按 {len(ctx.feedback)} 条审校意见修订文案" if is_revision else "开始撰写多版本文案"
    )
    tools = run_agent_tools(ctx, META)

    revision_block = ""
    if is_revision:
        numbered = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(ctx.feedback))
        revision_block = f"【本轮必须落实的修订意见】\n{numbered}\n\n"

    user = f"""【创作 Brief】
品牌：{brief.brand}｜产品：{brief.product}｜渠道：{brief.channel}｜调性：{brief.tone}
目标受众：{brief.audience}
关键词：{'、'.join(brief.keywords) or '（未指定）'}
硬性约束：{'；'.join(brief.constraints) or '（未指定）'}

{localization_block(ctx)}【渠道规范】
形态：{rule.format}｜长度：{rule.length_hint}
必备结构：{' → '.join(rule.blocks)}
标签策略：{rule.hashtag_policy}
合规注意：{'；'.join(rule.compliance_notes)}

【核心主张】{as_str(house.get('proposition'))}
【利益点】{'；'.join(as_str_array(house.get('benefits')))}

【选题与大纲】
选定选题：{as_str(plan.get('selected_topic'))}
标题备选：{' / '.join(as_str_array(plan.get('headline_candidates')))}

{memory_block(ctx)}{tools.prompt_block()}{revision_block}请撰写 3 个版本文案，并声明全部关键主张，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A4.copy",
        {
            "brief": brief.model_dump(mode="json"),
            "strategy": strategy,
            "creative": creative,
            "plan": plan,
            "feedback": ctx.feedback,
            "revision": ctx.revision,
            "memory": ctx.memory,
            "tools": tools.context(),
        },
        schema=SCHEMA,
        # 文案要和画面里实际有的东西对得上（颜色/包装/场景），图像随本次调用发送
        images=vision_attachments(ctx, META.id),
    )

    content = normalize(result.data)
    versions = as_obj_array(content["versions"])
    recommended = as_str(content.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None)
    if target is None:
        target = versions[0] if versions else {}

    ctx.emit(
        f"已生成 {len(versions)} 版文案，主推 {recommended}",
        {
            "versions": [
                {"id": as_str(v.get("id")), "style": as_str(v.get("style")), "title": as_str(v.get("title"))}
                for v in versions
            ]
        },
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="copy_draft",
            title=f"文案初稿（第 {ctx.revision + 1} 轮修订）" if is_revision else "文案初稿",
            content=content,
            text=render_text(content),
            tags=["文案", brief.channel, "返工稿" if is_revision else "初稿"],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"已按 {len(ctx.feedback)} 条意见完成修订，输出 {len(versions)} 版"
                if is_revision
                else f"已生成 {len(versions)} 版{brief.channel}文案，主推「{as_str(target.get('title'))}」"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.86 if is_revision else 0.8),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": "A5", "reason": "初稿完成，进入编辑审校"},
        ),
    )


a4_copywriter = AgentDefinition(meta=META, run=run)
