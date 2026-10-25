"""P1: candidate conflicts are deterministic, source-aware and never resolved.

The detector's whole claim is deliberately narrow: same sentence skeleton with
the value masked, different values, different sources. Anything else — same
source, same value, a different skeleton — is not a candidate. The check pins
the narrow rule so the panel cannot quietly become a semantic contradiction
detector that hallucinates conflicts.

Run: .venv/Scripts/python.exe p1/conflicts_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.aggregator import FinalOutcome, FinalResult  # noqa: E402
from app.conflicts import ConflictCandidate, detect_conflicts  # noqa: E402
from app.models import Claim, ClaimKind, TaskOutcome, TaskResult  # noqa: E402
from app.report import report_markdown  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "conflicts_results.json"


def _result(task_id: str, *claims: tuple[str, ClaimKind, tuple[str, ...]]) -> TaskResult:
    return TaskResult(
        run_id="run-1",
        task_id=task_id,
        execution_id=f"ex-{task_id}",
        producer=task_id,
        outcome=TaskOutcome.SUCCESS,
        claims=tuple(
            Claim(text=text, kind=kind, evidence_ids=evidence_ids)
            for text, kind, evidence_ids in claims
        ),
    )


def _details(**entries: tuple[str, str, str]) -> dict[str, dict[str, str]]:
    return {
        evidence_id: {
            "evidence_id": evidence_id,
            "source_uri": source,
            "locator": locator,
            "text": passage,
            "source_revision": "r1",
        }
        for evidence_id, (source, locator, passage) in entries.items()
    }


def _claim(value: str) -> str:
    return f"算法服务提供者应当在{value}内办理备案。"


def main() -> int:
    checks = Checks()

    # --- a real difference across sources is one candidate ------------------
    details = _details(
        **{
            "ev-a": ("algo.md", "section:24", "算法推荐服务提供者应当在十个工作日内办理备案。"),
            "ev-b": ("synthetic.md", "section:19", "合成服务提供者应当在三十个工作日内办理备案。"),
        }
    )
    results = [
        _result("research", (_claim("十个工作日"), ClaimKind.FACT, ("ev-a",))),
        _result("engineering", (_claim("三十个工作日"), ClaimKind.FACT, ("ev-b",))),
    ]
    candidates = detect_conflicts(results, details)
    checks.add(
        "same skeleton with different values across sources is a candidate",
        len(candidates) == 1
        and candidates[0].values == ("10个工作日", "30个工作日")
        and {side.source_uri for side in candidates[0].sides} == {"algo.md", "synthetic.md"},
        f"candidates={[(c.pattern, c.values) for c in candidates]}",
    )
    checks.add(
        "the candidate keeps the passage and locator for both sides",
        len(candidates) == 1
        and {side.locator for side in candidates[0].sides} == {"section:24", "section:19"}
        and all("提供者" in side.passage for side in candidates[0].sides),
        f"sides={[(s.locator, s.passage[:12]) for s in candidates[0].sides] if candidates else []}",
    )

    # --- same value is not a conflict --------------------------------------
    same_value = [
        _result("research", (_claim("十个工作日"), ClaimKind.FACT, ("ev-a",))),
        _result("engineering", (_claim("10个工作日"), ClaimKind.FACT, ("ev-b",))),
    ]
    # evidence-b still says 30, but the claim is what is compared: 十 and 10
    # are the same value after canonicalization.
    checks.add(
        "the same value written two ways is not a conflict",
        detect_conflicts(same_value, details) == [],
        f"candidates={detect_conflicts(same_value, details)}",
    )

    # --- one source cannot conflict with itself ----------------------------
    one_source = [
        _result("research", (_claim("十个工作日"), ClaimKind.FACT, ("ev-a",))),
        _result("engineering", (_claim("三十个工作日"), ClaimKind.FACT, ("ev-a",))),
    ]
    checks.add(
        "two claims from the same source are not a candidate",
        detect_conflicts(one_source, details) == [],
        f"candidates={detect_conflicts(one_source, details)}",
    )

    # --- non-facts are never compared --------------------------------------
    inferences = [
        _result("research", (_claim("十个工作日"), ClaimKind.INFERENCE, ("ev-a",))),
        _result("engineering", (_claim("三十个工作日"), ClaimKind.INFERENCE, ("ev-b",))),
    ]
    checks.add(
        "inference and assumption claims are not compared",
        detect_conflicts(inferences, details) == [],
        f"candidates={detect_conflicts(inferences, details)}",
    )

    # --- a claim with no evidence detail has no source ---------------------
    no_detail = [
        _result("research", (_claim("十个工作日"), ClaimKind.FACT, ("ev-missing",))),
        _result("engineering", (_claim("三十个工作日"), ClaimKind.FACT, ("ev-b",))),
    ]
    checks.add(
        "an evidence id without a recorded source is skipped",
        detect_conflicts(no_detail, details) == [],
        f"candidates={detect_conflicts(no_detail, details)}",
    )

    # --- article numbers are not values ------------------------------------
    article = [
        _result("research", ("第十条要求提供服务。", ClaimKind.FACT, ("ev-a",))),
        _result("engineering", ("第十一条要求提供服务。", ClaimKind.FACT, ("ev-b",))),
    ]
    checks.add(
        "article numbers are not treated as conflicting values",
        detect_conflicts(article, details) == [],
        f"candidates={detect_conflicts(article, details)}",
    )

    # --- the report shows both sides, or says it found none ----------------
    final = FinalResult(run_id="run-1", outcome=FinalOutcome.PARTIAL, answer="结论")
    rendered = report_markdown(final, conflicts=tuple(candidates))
    checks.add(
        "the report lists both sides with their sources",
        "## 冲突候选" in rendered
        and "algo.md" in rendered
        and "synthetic.md" in rendered
        and "10个工作日" in rendered
        and "30个工作日" in rendered,
        f"rendered={rendered[-400:]!r}",
    )
    empty = report_markdown(final, conflicts=())
    checks.add(
        "an empty result states that nothing was found, with the rule",
        "## 冲突候选" in empty
        and "未发现候选冲突" in empty
        and "同一句式" in empty,
        f"rendered={empty[-300:]!r}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="deterministic candidate-conflict detection and report rendering",
    )


if __name__ == "__main__":
    sys.exit(main())
