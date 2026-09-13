"""知识层：渠道规范、行业洞察与案例库、广告法合规词库。

当前为内置静态知识，生产环境应替换为 A11 记忆体 + 向量数据库的 RAG 检索。
"""

from .compliance import auto_rewrite, check_brand_voice, scan_compliance
from .industry import CASE_LIBRARY, CHANNEL_RULES, INDUSTRY_PROFILES, cases_for, channel_rule, industry_profile

__all__ = [
    "auto_rewrite",
    "check_brand_voice",
    "scan_compliance",
    "CASE_LIBRARY",
    "CHANNEL_RULES",
    "INDUSTRY_PROFILES",
    "cases_for",
    "channel_rule",
    "industry_profile",
]
