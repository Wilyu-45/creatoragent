"""A6 事实核查智能体的工具集。

对应《plan.md》2.2.3 的「搜索 Server」内置替代——**默认配置下本系统不联网**，
因此不提供假装查证的「搜索」工具，而是提供：

* ``claims_scanner``——从草稿中抽取数值型/最高级断言（可测量的文本特征）；
* ``source_crosscheck``——把作者声明的主张与正文断言做启发式对照；
* ``memory_crosscheck``——从 A11 记忆库召回品牌事实供交叉参考（非权威来源）。

真实联网检索由 ``app/tools/web_research.py`` 统一提供（``web_search`` /
``page_fetch``，默认关闭、配置后按真实结果注入）——它同时服务 A1/A2/A10，
故不放在本模块，避免同一能力出现两份实现。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.memory import memory_store
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

_WHITESPACE_RE = re.compile(r"\s")

#: 数值型断言模式：百分比 / 倍数 / 时长 / 金额 / 大数 / 排名序数
_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("百分比", re.compile(r"[\d.]+\s*[%％]")),
    ("倍数对比", re.compile(r"[\d.]+\s*(?:倍|[xX×])")),
    ("时长承诺", re.compile(r"[\d.]+\s*(?:天|小时|分钟|秒|周|个月|年)(?:内|搞定|见效|完成)?")),
    ("金额", re.compile(r"[\d.]+\s*(?:元|块钱|万元|万)")),
    ("大数样本", re.compile(r"[\d.]+\s*(?:万|亿)(?:人|名|位|次|条)?")),
    ("排名/序数", re.compile(r"(?:排名第一|行业第一|全国第一|全球第一|销量第一|首家|首个|独家|唯一)")),
    ("绝对化", re.compile(r"(?:最高级|最佳|最好|最强|最优|最先进|最便宜|最划算|顶级|顶尖|极致|完美|100%|百分百)")),
)


def _recommended_target(ctx: "AgentRunContext") -> dict[str, Any]:
    from ..llm.json_utils import as_obj, as_obj_array, as_str

    draft = ctx.upstream_of("draft")
    recommended = as_str(draft.get("recommended_version"), "V1")
    versions = as_obj_array(draft.get("versions"))
    return next((v for v in versions if as_str(v.get("id")) == recommended), {})


def claims_scanner(ctx: "AgentRunContext") -> ToolOutcome:
    """抽取正文中的数值型与最高级断言——无来源者按 A6 规则视为高风险。"""
    from ..llm.json_utils import as_str

    target = _recommended_target(ctx)
    text = f"{as_str(target.get('title'))}\n{as_str(target.get('body'))}"
    if not _WHITESPACE_RE.sub("", text):
        return ToolOutcome(summary="上游暂无文案草稿，跳过断言抽取", data={})

    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for kind, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 10)
            end = min(len(text), match.end() + 10)
            snippet = _WHITESPACE_RE.sub("", text[start:end])
            key = f"{kind}::{snippet}"
            if key in seen:
                continue
            seen.add(key)
            found.append({"kind": kind, "snippet": snippet})
    found = found[:12]

    lines = [
        f"{index}. [{item['kind']}] …{item['snippet']}…"
        for index, item in enumerate(found, start=1)
    ]
    lines.append(
        "（来源：正则抽取的文本特征——数值型断言若无来源，一律按规则标记为高风险）"
        if found
        else "（未检出数值型断言与绝对化用语）"
    )
    return ToolOutcome(
        summary=f"抽取 {len(found)} 处待核断言",
        detail="\n".join(lines),
        data={"claims": found},
    )


def source_crosscheck(ctx: "AgentRunContext") -> ToolOutcome:
    """作者声明的主张与正文断言的启发式对照（子串匹配，非语义判定）。"""
    from ..llm.json_utils import as_obj_array, as_str

    draft = ctx.upstream_of("draft")
    claims = as_obj_array(draft.get("claims"))
    with_source = [c for c in claims if as_str(c.get("source"))]
    without_source = [c for c in claims if not as_str(c.get("source"))]
    lines = [
        f"作者声明主张 {len(claims)} 条：有来源 {len(with_source)} 条、"
        f"无来源 {len(without_source)} 条",
    ]
    for claim in without_source[:3]:
        lines.append(f"- 无来源声明：「{as_str(claim.get('text'))[:50]}」")
    lines.append("（来源：草稿 claims 字段——启发式对照，不能替代逐条核查）")
    return ToolOutcome(
        summary=f"主张来源对照：{len(with_source)} 有源 / {len(without_source)} 无源",
        detail="\n".join(lines),
        data={
            "declared": len(claims),
            "with_source": len(with_source),
            "without_source": len(without_source),
        },
    )


def memory_crosscheck(ctx: "AgentRunContext") -> ToolOutcome:
    """从记忆库召回本品牌历史事实，供交叉参考（明确标注非权威来源）。"""
    from ..llm.json_utils import as_str

    brief = ctx.brief
    target = _recommended_target(ctx)
    query = " ".join(
        part
        for part in (brief.brand, brief.product, as_str(target.get("title")))
        if part
    )
    hits = memory_store.retrieve(
        query or brief.brand,
        brand=brief.brand,
        channel=brief.channel,
        industry=brief.industry,
        top_k=3,
        exclude_task=ctx.task_id,
        tenant=ctx.tenant,
        language=brief.language,
    )
    if not hits:
        return ToolOutcome(summary="知识库中暂无可交叉参考的品牌事实", data={"hits": []})
    dicts = [hit.to_dict() for hit in hits]
    lines = [
        f"{index}. [{item.get('kind_label')}] {item.get('title')}：{str(item.get('content'))[:60]}"
        for index, item in enumerate(dicts, start=1)
    ]
    lines.append(
        "（来源：A11 记忆库——历史沉淀仅供参考，不能替代对独立事实的核查）"
    )
    return ToolOutcome(
        summary=f"召回 {len(dicts)} 条品牌事实供交叉参考",
        detail="\n".join(lines),
        data={"hits": [{"title": i.get("title"), "kind": i.get("kind")} for i in dicts]},
    )


TOOLS: list[Tool] = [
    Tool(
        name="claims_scanner",
        description="抽取正文中的数值型/最高级断言（无来源者视为高风险）",
        agent_ids=("A6",),
        handler=claims_scanner,
    ),
    Tool(
        name="source_crosscheck",
        description="作者声明主张与来源的对照",
        agent_ids=("A6",),
        handler=source_crosscheck,
    ),
    Tool(
        name="memory_crosscheck",
        description="从记忆库召回品牌事实供交叉参考",
        agent_ids=("A6",),
        handler=memory_crosscheck,
    ),
]

__all__ = ["TOOLS", "claims_scanner", "source_crosscheck", "memory_crosscheck"]
