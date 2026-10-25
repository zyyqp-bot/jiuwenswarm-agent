"""Assemble and persist the run's deliverable.

The Aggregator owns the verdict (complete / partial / failed / cancelled) and
the claim rendering. This module adds the two things the API needs: the
application-signed acceptance checks that were implicit in the run bridge, and
a deterministic Markdown file a judge can read and export.

Acceptance here is structural, exactly as in the bridge: a task that produced
an accepted result has its declared conditions signed by ``app.structural``.
Semantic review remains a separate path.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.aggregator import Aggregator, FinalResult
from app.conflicts import ConflictCandidate, detect_conflicts
from app.models import (
    AcceptanceCheck,
    AcceptanceResult,
    ClaimKind,
    TaskOutcome,
    TaskResult,
)
from app.plan import PlanTask
from app.run_bridge import APP_VERIFIER

__all__ = ["build_report", "report_markdown", "write_report"]


def build_report(
    *,
    run_id: str,
    results: Sequence[TaskResult],
    plan: Sequence[PlanTask],
    unverified: Iterable[str] = (),
    unavailable: Iterable[str] = (),
    usage: Any = None,
    evidence_by_task: Mapping[str, Iterable[str]] | None = None,
    evidence_details: Mapping[str, Mapping[str, Any]] | None = None,
) -> FinalResult:
    """Merge accepted results into the run's final result.

    Acceptance is structural: a task passes only when it produced a successful
    result with claims, every fact cites evidence, and every cited id is one
    the task actually retrieved. ``evidence_by_task`` is the run snapshot's
    ``evidence`` map; when omitted (unit callers) the membership check is
    skipped rather than invented.
    """
    by_task = {result.task_id: result for result in results}
    retrieved = {
        task_id: set(ids) for task_id, ids in (evidence_by_task or {}).items()
    }
    required: list[str] = []
    acceptance: dict[str, AcceptanceCheck] = {}
    for task in plan:
        result = by_task.get(task.task_id)
        passed, cited = _structural_verdict(
            result, retrieved.get(task.task_id, set()) if evidence_by_task is not None else None
        )
        for acceptance_id in task.acceptance_ids:
            required.append(acceptance_id)
            acceptance[acceptance_id] = AcceptanceCheck(
                acceptance_id=acceptance_id,
                result=AcceptanceResult.PASS if passed else AcceptanceResult.FAIL,
                verifier=APP_VERIFIER,
                evidence_refs=cited,
            )
    merged = Aggregator().merge(
        run_id=run_id,
        results=results,
        required_acceptance_ids=required,
        acceptance=acceptance,
        unverified_claim_texts=unverified,
        unavailable_evidence_ids=unavailable,
        usage=usage,
    )
    conflicts = detect_conflicts(results, evidence_details or {})
    return merged.model_copy(
        update={"conflicts": tuple(asdict(candidate) for candidate in conflicts)}
    )


def _structural_verdict(
    result: TaskResult | None, retrieved: set[str] | None
) -> tuple[bool, tuple[str, ...]]:
    """Decide one task's structural acceptance and the evidence it cited."""
    if result is None or result.outcome is not TaskOutcome.SUCCESS:
        return False, ()
    if not result.claims:
        return False, ()
    cited = tuple(sorted({eid for claim in result.claims for eid in claim.evidence_ids}))
    for claim in result.claims:
        if claim.kind is ClaimKind.FACT and not claim.evidence_ids:
            return False, cited
        if retrieved is not None and any(eid not in retrieved for eid in claim.evidence_ids):
            return False, cited
    return True, cited


def _field(item: Any, name: str) -> Any:
    """Read a conflict field from either its dataclass or its JSON form."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def report_markdown(
    final: FinalResult,
    *,
    task_results: Sequence[TaskResult] = (),
    conflicts: Sequence[ConflictCandidate | Mapping[str, Any]] | None = None,
) -> str:
    """Render the deliverable as deterministic Markdown."""
    items = final.conflicts if conflicts is None else conflicts
    lines: list[str] = [f"# 证据工作台报告 · {final.run_id}", "", "## 结论", ""]
    lines.append(final.answer.strip() or "（无结论）")

    lines.extend(["", "## 冲突候选", ""])
    if items:
        lines.append("检测规则：同一句式、不同来源、不同数值。以下仅并列呈现，不作裁决。")
        for candidate in items:
            pattern = _field(candidate, "pattern") or "（句式）"
            values = _field(candidate, "values") or ()
            lines.extend(["", f"### {pattern}", "", f"- 数值：{' / '.join(values)}"])
            for side in _field(candidate, "sides") or ():
                source = _field(side, "source_uri") or ""
                locator = _field(side, "locator") or ""
                evidence_id = _field(side, "evidence_id") or ""
                claim_text = _field(side, "claim_text") or ""
                passage = _field(side, "passage") or ""
                lines.append(f"- [{_field(side, 'task_id')}] {claim_text}")
                where = source + (f" · {locator}" if locator else "")
                lines.append(f"  - 来源：{where} · `{evidence_id}`")
                if passage:
                    lines.append(f"  - 原文：{passage}")
    else:
        lines.append("- 未发现候选冲突（规则：同一句式、不同来源、不同数值）。")

    lines.extend(["", "## 验收", ""])
    if final.acceptance_checks:
        for check in final.acceptance_checks:
            lines.append(
                f"- {check.acceptance_id}: {check.result.value}（{check.verifier}）"
            )
    else:
        lines.append("- （无验收条件）")

    lines.extend(["", "## 限制", ""])
    if final.limitations:
        lines.extend(f"- {item}" for item in final.limitations)
    else:
        lines.append("- 无")

    lines.extend(["", "## 证据", ""])
    if final.evidence_refs:
        lines.extend(f"- `{evidence_id}`" for evidence_id in final.evidence_refs)
    else:
        lines.append("- （无）")

    if task_results:
        lines.extend(["", "## 分任务明细", ""])
        for result in task_results:
            lines.append(f"### {result.task_id}（{result.outcome.value}，{result.producer}）")
            for claim in result.claims:
                evidence = " ".join(f"`{item}`" for item in claim.evidence_ids) or "—"
                lines.append(f"- [{claim.kind.value}] {claim.text} — {evidence}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(
    directory: Path,
    final: FinalResult,
    *,
    task_results: Sequence[TaskResult] = (),
) -> None:
    """Write ``report.json`` and ``report.md`` atomically into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = final.model_dump(mode="json")
    _atomic_write(directory / "report.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(directory / "report.md", report_markdown(final, task_results=task_results))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
