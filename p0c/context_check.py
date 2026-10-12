"""P0-C (part 3): the context builder.

PROJECT.md section 7.1's rules, offline: an input budget derived from the
window, incompressible parts that are never trimmed, evidence that gives way,
and an estimate that says it is an estimate.

No framework, no Runner, no model.

Run: .venv/Scripts/python.exe p0c/context_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.context import (  # noqa: E402
    ESTIMATED_TOKENIZER_ID,
    ContextBudget,
    ContextBuilder,
    ContextLimitExceeded,
    estimate_tokens,
)
from checks.fixtures import evidence as _evidence  # noqa: E402
from checks.harness import Checks, raises  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "context_results.json"


def main() -> int:
    checks = Checks()

    # --- the estimate -------------------------------------------------------
    checks.add("an empty string costs nothing", estimate_tokens("") == 0, "")
    checks.add(
        "CJK text is estimated at one token per character",
        estimate_tokens("研究团队") == 4,
        f"got {estimate_tokens('研究团队')}",
    )
    checks.add(
        "non-CJK text is estimated at one token per four characters, rounded up",
        estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2,
        f"abcd={estimate_tokens('abcd')} abcde={estimate_tokens('abcde')}",
    )
    checks.add(
        "a mixed string sums both parts",
        estimate_tokens("研究ab") == 2 + 1,
        f"got {estimate_tokens('研究ab')}",
    )
    checks.add(
        "Hangul is counted per character like CJK, not as latin text",
        estimate_tokens("안녕하세요") == 5,
        f"got {estimate_tokens('안녕하세요')}",
    )

    # --- the budget ---------------------------------------------------------
    budget = ContextBudget(context_window=100, max_output_tokens=20, safety_margin=10)
    checks.add(
        "the input budget is window minus output reserve minus margin",
        budget.input_budget == 70,
        f"input_budget={budget.input_budget}",
    )
    exhausted = ContextBudget(context_window=100, max_output_tokens=90, safety_margin=20)
    ok, detail = raises(
        lambda: ContextBuilder().build(budget=exhausted, task_instruction="做点事"),
        expected=ContextLimitExceeded,
    )
    checks.add("a non-positive budget is rejected rather than trimming everything away", ok, detail)

    builder = ContextBuilder()

    # --- everything fits ----------------------------------------------------
    instruction = "核对源码版本并给出结论"
    small = [_evidence(1, "接口差异记录"), _evidence(2, "验证步骤")]
    bundle = builder.build(
        budget=budget,
        task_instruction=instruction,
        evidence=small,
        pinned_constraints=("不得编造测试结果",),
        acceptance_ids=("acc-1",),
        spec_ref="spec-1",
        task_ref="task-1",
    )
    checks.add(
        "a bundle that fits keeps every evidence item",
        len(bundle.evidence) == 2 and bundle.dropped_evidence_ids == (),
        f"kept={len(bundle.evidence)} dropped={bundle.dropped_evidence_ids}",
    )
    checks.add(
        "the bundle keeps the incompressible parts verbatim",
        bundle.task_instruction == instruction
        and bundle.pinned_constraints == ("不得编造测试结果",)
        and bundle.acceptance_ids == ("acc-1",)
        and bundle.spec_ref == "spec-1"
        and bundle.task_ref == "task-1",
        "",
    )
    checks.add(
        "evidence order is preserved",
        [item.evidence_id for item in bundle.evidence] == ["ev-1", "ev-2"],
        f"order={[item.evidence_id for item in bundle.evidence]}",
    )
    checks.add(
        "the estimate is labelled as an estimate, not a measurement",
        bundle.tokenizer_id == ESTIMATED_TOKENIZER_ID and bundle.input_token_estimate > 0,
        f"tokenizer_id={bundle.tokenizer_id} estimate={bundle.input_token_estimate}",
    )
    checks.add(
        "the reported estimate stays within the budget",
        bundle.input_token_estimate <= budget.input_budget,
        f"estimate={bundle.input_token_estimate} budget={budget.input_budget}",
    )

    # --- evidence gives way -------------------------------------------------
    tiny = ContextBudget(context_window=60, max_output_tokens=10, safety_margin=0)  # 50 tokens
    many = [_evidence(i, "证据内容" * 5) for i in range(1, 21)]
    bundle = builder.build(budget=tiny, task_instruction="做一个任务", evidence=many)
    checks.add(
        "when the budget is tight, evidence is trimmed and the drops are recorded",
        len(bundle.evidence) < len(many) and len(bundle.evidence) + len(bundle.dropped_evidence_ids) == len(many),
        f"kept={len(bundle.evidence)} dropped={len(bundle.dropped_evidence_ids)}",
    )
    checks.add(
        "the trimmed bundle still fits the budget",
        bundle.input_token_estimate <= tiny.input_budget,
        f"estimate={bundle.input_token_estimate} budget={tiny.input_budget}",
    )
    checks.add(
        "the empty case leaves only the incompressible parts",
        builder.build(budget=tiny, task_instruction="做一个任务").evidence == (),
        "",
    )

    # --- constraints never give way ----------------------------------------
    huge = "约束" * 200
    ok, detail = raises(
        lambda: builder.build(budget=tiny, task_instruction="做一个任务", pinned_constraints=(huge,)),
        expected=ContextLimitExceeded,
    )
    checks.add("incompressible constraints are never trimmed: the build fails instead", ok, detail)

    # --- acceptance conditions are incompressible too ----------------------
    ok, detail = raises(
        lambda: builder.build(budget=tiny, task_instruction="任务", acceptance_ids=("acc-" + "x" * 400,)),
        expected=ContextLimitExceeded,
    )
    checks.add("an oversized acceptance condition is rejected, not stored for free", ok, detail)
    bundle = builder.build(budget=tiny, task_instruction="任务", acceptance_ids=("acc-1",))
    checks.add(
        "acceptance ids contribute to the reported estimate",
        bundle.input_token_estimate > estimate_tokens("任务"),
        f"estimate={bundle.input_token_estimate} instruction_only={estimate_tokens('任务')}",
    )

    # --- the caller-declared fixed overhead ---------------------------------
    ok, detail = raises(
        lambda: builder.build(budget=tiny, task_instruction="任务", fixed_overhead_tokens=999),
        expected=ContextLimitExceeded,
    )
    checks.add("the declared fixed overhead is charged against the budget", ok, detail)
    bundle = builder.build(
        budget=budget, task_instruction="任务", evidence=[_evidence(1, "证据")], fixed_overhead_tokens=20
    )
    checks.add(
        "the reported estimate includes the fixed overhead, not just the assembled parts",
        bundle.fixed_overhead_tokens == 20 and bundle.input_token_estimate >= 20,
        f"estimate={bundle.input_token_estimate}",
    )

    # --- trimming follows relevance order ----------------------------------
    narrow = ContextBudget(context_window=40, max_output_tokens=0, safety_margin=0)
    ordered = [
        _evidence(1, "甲" * 20),
        _evidence(2, "乙" * 30),
        _evidence(3, "丙" * 5),
    ]
    bundle = builder.build(budget=narrow, task_instruction="任务", evidence=ordered)
    checks.add(
        "a smaller, less relevant item is not admitted ahead of a larger, more relevant one",
        [item.evidence_id for item in bundle.evidence] == ["ev-1"]
        and set(bundle.dropped_evidence_ids) == {"ev-2", "ev-3"},
        f"kept={[item.evidence_id for item in bundle.evidence]} dropped={bundle.dropped_evidence_ids}",
    )

    # --- blank constraints --------------------------------------------------
    bundle = builder.build(budget=budget, task_instruction="任务", pinned_constraints=("", "  ", "真实约束"))
    checks.add(
        "blank constraints are filtered rather than echoed as phantom requirements",
        bundle.pinned_constraints == ("真实约束",),
        f"constraints={bundle.pinned_constraints}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="pure context assembly; no framework, no Runner, no model",
        not_covered="history compression belongs to the framework rail (section 7.1, D07)",
    )


if __name__ == "__main__":
    sys.exit(main())
