"""Reject empty, gibberish and off-topic goals before a run is created.

A run costs a model round and several minutes. A UI that accepts "你好" and
still produces a report looks broken, so the guard is deterministic: length,
recognisable characters, repeated characters, and a workspace keyword. It
does not try to understand the question — the planner still does that.

Unknown workspaces get only the basic checks: the guard must not invent a
domain it has no vocabulary for.
"""
from __future__ import annotations

import re

__all__ = ["validate_goal", "MIN_GOAL_CHARS"]

MIN_GOAL_CHARS = 4

# Deliberately broad: a false rejection costs a user more than a false accept,
# because an accepted odd goal still ends with an honest report.
_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "education-policy": (
        "教育", "学校", "教师", "教学", "学生", "作业", "课程", "招生", "学位",
        "学前", "义务", "职业", "未成年", "政策", "法规", "指南", "合规",
        "人工智能", "ai", "数据", "个人信息", "网络保护", "评价", "教辅",
        "课堂", "教材", "校园", "培训", "考试",
    ),
    "ai-governance": (
        "人工智能", "ai", "算法", "生成式", "深度合成", "标识", "数据",
        "个人信息", "合规", "备案", "安全评估", "政策", "法规", "治理",
        "隐私", "内容", "大模型", "服务提供者", "客服", "模型",
    ),
}

_LATIN_TERMS = ("ai", "compliance", "policy", "education", "school", "teacher",
                "student", "model", "algorithm", "privacy", "data", "governance")

_WORD_RE = re.compile(r"[A-Za-z一-鿿]")


def _basic_reason(text: str) -> str | None:
    if len(text) < MIN_GOAL_CHARS:
        return f"请至少输入 {MIN_GOAL_CHARS} 个字描述你想研读的问题。"
    if not _WORD_RE.search(text):
        return "目标里没有可识别的文字，请用中文或英文描述问题。"
    most_common = max(text.count(char) for char in set(text))
    if len(text) >= 4 and most_common / len(text) >= 0.7:
        return "目标看起来是重复字符，请描述一个具体问题。"
    return None


def validate_goal(goal: str, workspace: str) -> tuple[bool, str]:
    """Return ``(accepted, reason)``; reason is empty when accepted."""
    text = " ".join((goal or "").split())
    basic = _basic_reason(text)
    if basic is not None:
        return False, basic

    terms = _DOMAIN_TERMS.get(workspace)
    if terms is None:
        return True, ""

    lowered = text.lower()
    if any(term in lowered for term in terms) or any(term in lowered for term in _LATIN_TERMS):
        return True, ""
    return False, (
        f"目标与当前工作区（{workspace}）的主题不相关。"
        "请描述该领域的政策、法规或合规问题；也可以换一个工作区。"
    )
