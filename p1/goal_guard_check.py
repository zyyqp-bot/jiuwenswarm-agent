"""P1: reject empty, gibberish and off-topic goals before a run starts.

A run costs a model round and several minutes; a goal like "你好" or
"今天天气怎么样" must not create one. These checks are deterministic so the
guard itself costs nothing and is testable offline.

Run: .venv/Scripts/python.exe p1/goal_guard_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.goal_guard import validate_goal  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "goal_guard_results.json"


def main() -> int:
    checks = Checks()

    accepted = [
        ("education-policy", "学校拟引入一款 AI 作业批改工具，请依据教育政策与 AI 教育指南，评估其合规要求。"),
        ("education-policy", "中小学使用生成式人工智能辅助教学时，教师需要注意哪些要求？"),
        ("ai-governance", "面向公众的生成式 AI 客服上线前，需要完成哪些合规动作？"),
        ("ai-governance", "深度合成服务提供者有哪些标识义务？"),
        ("ai-governance", "check compliance for a public generative AI service"),
        ("medical-ai-compliance", "医院引入 AI 辅助诊断软件，需要满足哪些医疗器械与数据合规要求？"),
        ("medical-ai-compliance", "互联网诊疗中患者病历和人类遗传资源的使用边界是什么？"),
    ]
    for workspace, goal in accepted:
        ok, reason = validate_goal(goal, workspace)
        checks.add(f"accepts a relevant goal ({goal[:12]}…)", ok, reason)

    rejected = [
        ("你好", "too short"),
        ("。。。", "punctuation only"),
        ("aaaaaaaaaa", "repeated characters"),
        ("今天天气怎么样", "off-topic for education"),
        ("给我讲个笑话", "off-topic for both"),
        ("   ", "blank"),
    ]
    for goal, label in rejected:
        ok, reason = validate_goal(goal, "education-policy")
        checks.add(f"rejects {label}", not ok and bool(reason), f"ok={ok} reason={reason!r}")

    off_topic_ai, reason_ai = validate_goal("今天天气怎么样", "ai-governance")
    checks.add("rejects the same off-topic goal in the AI workspace", not off_topic_ai, reason_ai)

    off_topic_med, reason_med = validate_goal("今天天气怎么样", "medical-ai-compliance")
    checks.add("rejects an off-topic goal in the medical workspace", not off_topic_med, reason_med)

    unknown_ok, unknown_reason = validate_goal("学校 AI 合规", "no-such-workspace")
    checks.add(
        "an unknown workspace only gets the basic checks",
        unknown_ok,
        unknown_reason,
    )

    return checks.report(RESULTS_PATH, scope="deterministic goal validation before run creation")


if __name__ == "__main__":
    sys.exit(main())
