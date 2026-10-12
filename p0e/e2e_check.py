"""P0-E: end-to-end acceptance, offline.

PROJECT.md section 12 lists ten acceptance scenarios and notes that module
assertions do not cover them: each module is verified alone, never in sequence.
This script runs the module chain end to end on synthetic inputs -- no
framework runtime, no model, no network -- so the *contracts between* modules
are exercised, not just each module's internals.

The orchestration below exists only to drive the scenarios. The production
orchestration is RuntimeAdapter's job and needs a live Runner (P1).

Each task writes to both the in-memory ledger (what the gates read) and the
durable repository (what recovery reads), so the two paths are exercised
together rather than one standing in for the other.

Two things this harness is careful *not* to do:

- it does not claim provider-measured usage. No provider was contacted, so
  every settlement is recorded as ``unknown`` rather than as a measured
  success the budget module exists to prevent;
- it does not assert on values that cannot move. A check that reads the
  compiled spec proves nothing about runtime behaviour, so such a check is
  either made meaningful or labelled for what it is.

What this does not prove: anything needing a real model, a real embedding
provider, the framework's context rail, or team memory. Those are reported as
not-run rather than quietly skipped.

Run: .venv/Scripts/python.exe p0e/e2e_check.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "p0b"))

from app.aggregator import Aggregator, FinalOutcome, UNVERIFIED_MARKER, Usage  # noqa: E402
from app.budget import BudgetExceeded, BudgetManager  # noqa: E402
from app.citations import CitationValidator  # noqa: E402
from app.completion import CompletionGate  # noqa: E402
from app.config_adapter import compile_team_spec  # noqa: E402
from app.context import ContextBudget, ContextBuilder  # noqa: E402
from app.dependency import DependencyGate  # noqa: E402
from app.events import EventProjection  # noqa: E402
from app.execution import ExecutionLedger, StaleExecutionError  # noqa: E402
from app.knowledge import KnowledgeIndex  # noqa: E402
from app.models import (  # noqa: E402
    AcceptanceCheck,
    AcceptanceResult,
    Claim,
    ClaimKind,
    TaskOutcome,
    TaskResult,
)
from app.plan import PlanIssueCode, PlanTask, PlanValidator  # noqa: E402
from app.repository import RunRepository  # noqa: E402
from checks.harness import Checks  # noqa: E402
from compile_spec_check import build_config  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "e2e_results.json"

RUN = "run-1"
SCOPE = "project:alpha"
OTHER_SCOPE = "project:beta"
RESEARCH = "task-research"
ENGINEERING = "task-engineering"

RESEARCH_DOC = "研究团队核对源码版本与接口差异，所有结论必须能够追溯到具体的提交与文件位置。"
ENGINEERING_DOC = "工程团队负责架构设计与可执行验证，提出接口草案与回滚方案。"

# The harness never contacts a provider, so this is what a settlement is
# allowed to claim: an estimate, never a measurement.
SYNTHETIC_QUALITY = "unknown"


def _acceptance(task_id: str, passed: bool = True) -> AcceptanceCheck:
    return AcceptanceCheck(
        acceptance_id=f"acc-{task_id}",
        result=AcceptanceResult.PASS if passed else AcceptanceResult.FAIL,
        verifier="program:structural",
    )


class Harness:
    """Drives the module chain for one scenario run."""

    def __init__(self, repo: RunRepository, index: KnowledgeIndex, spec) -> None:
        self.repo = repo
        self.index = index
        self.spec = spec
        self.ledger = ExecutionLedger()
        self.budget = BudgetManager(max_model_calls=12, max_tokens=8000, finalization_reserve_tokens=500)
        self.dependencies = DependencyGate(self.ledger)
        self.completion = CompletionGate(self.ledger)
        self.results: dict[str, TaskResult] = {}
        self.unavailable_evidence: list[str] = []

    async def run_task(
        self,
        *,
        task_id: str,
        role: str,
        query: str,
        execution_id: str,
        depends_on: tuple[str, ...] = (),
        requires_success: bool = True,
    ) -> tuple[TaskResult | None, str]:
        """Run one task through the chain. Returns ``(result, note)``.

        A result is only submitted when its citations actually resolve: the
        chain's whole point is that an unsupported claim must not reach the
        deliverable, so the harness enforces it rather than merely noting it.
        """
        decision = self.dependencies.evaluate(
            run_id=RUN,
            task_id=task_id,
            depends_on=depends_on,
            requires_success=requires_success,
            results=self.results,
        )
        if not decision.allowed:
            return None, f"blocked: {decision.reason.value} at {decision.blocked_by}"

        try:
            reservation = self.budget.reserve(calls=1, tokens=200)
        except BudgetExceeded as exc:
            return None, f"budget refused: {exc}"

        try:
            hits = await self.index.search(query, limit=5, authorized_scopes=[SCOPE])
        except Exception:
            # A call that never ran must not be charged.
            self.budget.release(reservation.reservation_id)
            raise
        self.budget.settle(reservation.reservation_id, tokens=150, quality=SYNTHETIC_QUALITY)

        self.ledger.open(RUN, task_id, execution_id)
        await self.repo.open_execution(RUN, task_id, execution_id)

        bundle = ContextBuilder().build(
            budget=ContextBudget(context_window=4000, max_output_tokens=500, safety_margin=100),
            task_instruction=query,
            evidence=hits,
            pinned_constraints=("不得编造测试结果",),
            acceptance_ids=(f"acc-{task_id}",),
            task_ref=task_id,
        )
        self.unavailable_evidence.extend(bundle.dropped_evidence_ids)

        result = TaskResult(
            run_id=RUN,
            task_id=task_id,
            execution_id=execution_id,
            producer=role,
            outcome=TaskOutcome.SUCCESS,
            artifact_refs=(f"artifact://{task_id}",),
            claims=(
                Claim(
                    text=f"{role} 的结论",
                    kind=ClaimKind.FACT,
                    evidence_ids=tuple(hit.evidence_id for hit in bundle.evidence),
                ),
            ),
        )

        citation = CitationValidator().validate(
            result.claims, available_evidence_ids={hit.evidence_id for hit in bundle.evidence}
        )
        if not citation.valid:
            return None, f"refused: citations did not resolve ({citation.codes()})"

        await self.repo.submit_result(result)
        self.results[task_id] = result
        return result, "citations all resolved"

    async def cancel_task(self, *, task_id: str, role: str, execution_id: str) -> TaskResult:
        """Record a task that was cancelled: the framework edge resolves anyway."""
        self.ledger.open(RUN, task_id, execution_id)
        await self.repo.open_execution(RUN, task_id, execution_id)
        result = TaskResult(
            run_id=RUN,
            task_id=task_id,
            execution_id=execution_id,
            producer=role,
            outcome=TaskOutcome.ERROR,
            artifact_refs=(),
            claims=(),
        )
        await self.repo.submit_result(result)
        self.results[task_id] = result
        return result


def _plan(*, research_reviewers: tuple[str, ...] = ("review-team",)) -> list[PlanTask]:
    return [
        PlanTask(
            task_id=RESEARCH,
            role="teammate",
            instruction="核对源码版本",
            acceptance_ids=(f"acc-{RESEARCH}",),
            reviewers=research_reviewers,
        ),
        PlanTask(
            task_id=ENGINEERING,
            role="teammate",
            instruction="给出方案",
            acceptance_ids=(f"acc-{ENGINEERING}",),
            depends_on=(RESEARCH,),
            reviewers=("review-team",),
        ),
    ]


async def _new_harness(tmp: Path, name: str) -> tuple[Harness, KnowledgeIndex, RunRepository]:
    repo = await RunRepository.open(tmp / f"{name}.sqlite")
    index = await KnowledgeIndex.open(tmp / f"{name}-corpus.sqlite", corpus="docs", access_scope=SCOPE)
    await index.add_document(source_uri="docs/research.md", source_revision="r1", text=RESEARCH_DOC)
    await index.add_document(source_uri="docs/engineering.md", source_revision="r1", text=ENGINEERING_DOC)
    await repo.register_run(RUN, session_id="sess-1", team_name="jiuwenswarm_mvp")
    return Harness(repo, index, compile_team_spec(build_config())), index, repo


async def _scenario_dependency_chain(checks: Checks, tmp: Path) -> None:
    """Scenarios 1 and 2: a research -> engineering request, and parallelism."""
    harness, index, repo = await _new_harness(tmp, "chain")

    report = PlanValidator().validate(_plan())
    checks.add("scenario 1: the research -> engineering plan validates", report.valid, f"{report.codes()}")

    research, note = await harness.run_task(
        task_id=RESEARCH, role="research", query="源码版本", execution_id="exec-research-1"
    )
    checks.add(
        "scenario 1: research produces a result whose claims cite the evidence it received",
        research is not None and bool(research.claims) and bool(research.claims[0].evidence_ids),
        note,
    )
    if research is None:
        await index.close()
        await repo.close()
        return

    engineering, note = await harness.run_task(
        task_id=ENGINEERING,
        role="engineering",
        query="架构设计",
        execution_id="exec-engineering-1",
        depends_on=(RESEARCH,),
    )
    checks.add(
        "scenario 1: the dependent task runs once its prerequisite has a valid artifact",
        engineering is not None and engineering.artifact_refs == (f"artifact://{ENGINEERING}",),
        note,
    )
    if engineering is None:
        await index.close()
        await repo.close()
        return

    independent, note = await harness.run_task(
        task_id="task-independent", role="research", query="架构设计", execution_id="exec-independent-1"
    )
    checks.add("scenario 2: an independent task needs no prerequisite", independent is not None, note)

    checks.add(
        "scenario 2: the harness never took a spawn path (config roster unchanged)",
        len([harness.spec.leader, *harness.spec.predefined_members]) == 3,
        "config-level only; runtime roster maintenance needs a live Runner (P1)",
    )

    acceptance = {f"acc-{RESEARCH}": _acceptance(RESEARCH), f"acc-{ENGINEERING}": _acceptance(ENGINEERING)}
    for task_id, result in ((RESEARCH, research), (ENGINEERING, engineering)):
        decision = harness.completion.evaluate(
            run_id=RUN,
            task_id=task_id,
            execution_id=result.execution_id,
            result=result,
            reviewers=("review-team",),
            require_reviewer=True,
            required_acceptance_ids=(f"acc-{task_id}",),
            acceptance=acceptance,
        )
        checks.add(f"scenario 1: {task_id} passes the completion gate", decision.allowed, decision.detail or "")

    final = Aggregator().merge(
        run_id=RUN,
        results=[research, engineering],
        required_acceptance_ids=(f"acc-{RESEARCH}", f"acc-{ENGINEERING}"),
        acceptance=acceptance,
        unavailable_evidence_ids=harness.unavailable_evidence,
        usage=Usage(model_calls=2, tokens=300, quality=SYNTHETIC_QUALITY),
    )
    checks.add(
        "scenario 1: the run delivers complete with evidence and artifacts attached",
        final.outcome is FinalOutcome.COMPLETE
        and bool(final.evidence_refs)
        and bool(final.artifact_refs)
        and final.limitations == (),
        f"outcome={final.outcome.value} evidence={len(final.evidence_refs)} artifacts={final.artifact_refs}",
    )

    stored = await repo.results_for_run(RUN)
    checks.add(
        "scenario 8: the durable record holds every accepted result",
        set(stored) == {RESEARCH, ENGINEERING, "task-independent"},
        f"stored={sorted(stored)}",
    )
    checks.add(
        "the deliverable's usage is not claimed as provider-measured (no provider was contacted)",
        final.usage is not None and final.usage.quality == SYNTHETIC_QUALITY,
        f"quality={final.usage.quality if final.usage else None}",
    )

    await index.close()
    await repo.close()


async def _scenario_cancelled_prerequisite(checks: Checks, tmp: Path) -> None:
    """Scenario 5: a cancelled prerequisite must not trigger downstream work."""
    harness, index, repo = await _new_harness(tmp, "cancelled")

    await harness.cancel_task(task_id=RESEARCH, role="research", execution_id="exec-research-cancel")

    blocked, note = await harness.run_task(
        task_id=ENGINEERING,
        role="engineering",
        query="架构设计",
        execution_id="exec-engineering-1",
        depends_on=(RESEARCH,),
    )
    checks.add(
        "scenario 5: the dependent task is blocked by the gate even though the edge resolved",
        blocked is None and "dependency_failed" in note,
        note,
    )

    decision = harness.completion.evaluate(
        run_id=RUN,
        task_id=RESEARCH,
        execution_id="exec-research-cancel",
        result=harness.results[RESEARCH],
        reviewers=("review-team",),
        require_reviewer=True,
        required_acceptance_ids=(f"acc-{RESEARCH}",),
        acceptance={f"acc-{RESEARCH}": _acceptance(RESEARCH)},
    )
    checks.add(
        "scenario 5: the cancelled prerequisite cannot be accepted as complete",
        not decision.allowed,
        decision.reason.value if decision.reason else "",
    )

    await index.close()
    await repo.close()


async def _scenario_plan_and_completion_guards(checks: Checks, tmp: Path) -> None:
    """Scenario 6: a key task without a reviewer, and an unacceptable result."""
    report = PlanValidator().validate(_plan(research_reviewers=()))
    checks.add(
        "scenario 6: a key task without a reviewer is refused by the plan validator",
        not report.valid and PlanIssueCode.MISSING_REVIEWER in report.codes(),
        f"{report.codes()}",
    )

    harness, index, repo = await _new_harness(tmp, "guards")
    research, note = await harness.run_task(
        task_id=RESEARCH, role="research", query="源码版本", execution_id="exec-research-1"
    )
    if research is None:
        checks.add("scenario 6: the prerequisite ran", False, note)
        await index.close()
        await repo.close()
        return

    decision = harness.completion.evaluate(
        run_id=RUN,
        task_id=RESEARCH,
        execution_id="exec-research-1",
        result=research.model_copy(update={"outcome": TaskOutcome.PARTIAL}),
        reviewers=("review-team",),
        require_reviewer=True,
        required_acceptance_ids=(f"acc-{RESEARCH}",),
        acceptance={f"acc-{RESEARCH}": _acceptance(RESEARCH)},
    )
    checks.add(
        "scenario 6: an unsuccessful result cannot complete the task",
        not decision.allowed,
        decision.reason.value if decision.reason else "",
    )

    await index.close()
    await repo.close()


async def _scenario_scope_isolation(checks: Checks, tmp: Path) -> None:
    """Scenario 4: the same query in another scope yields nothing."""
    index = await KnowledgeIndex.open(tmp / "scope-corpus.sqlite", corpus="docs", access_scope=SCOPE)
    await index.add_document(source_uri="docs/research.md", source_revision="r1", text=RESEARCH_DOC)

    alpha = await index.search("源码版本", limit=5, authorized_scopes=[SCOPE])
    beta = await index.search("源码版本", limit=5, authorized_scopes=[OTHER_SCOPE])
    checks.add("scenario 4: the authorised scope finds the material", len(alpha) == 1, f"hits={len(alpha)}")
    checks.add(
        "scenario 4: another scope finds nothing, so no unauthorised text can reach a model",
        beta == [],
        f"hits={len(beta)}",
    )
    await index.close()


async def _scenario_replay_and_late_results(checks: Checks, tmp: Path) -> None:
    """Scenario 8: duplicates, a restart, and a late result from an old execution."""
    harness, index, repo = await _new_harness(tmp, "replay")

    projection = EventProjection()
    payload = {"task_id": RESEARCH, "status": "in_progress"}
    projection.observe(run_id=RUN, event_type="task.updated", entity_id=RESEARCH, payload=payload)
    checks.add(
        "scenario 8: a repeated event is not registered twice",
        projection.observe(run_id=RUN, event_type="task.updated", entity_id=RESEARCH, payload=payload) is None,
        "",
    )

    research, note = await harness.run_task(
        task_id=RESEARCH, role="research", query="源码版本", execution_id="exec-research-1"
    )
    await index.close()
    await repo.close()
    if research is None:
        checks.add("scenario 8: the task ran", False, note)
        return

    reopened = await RunRepository.open(tmp / "replay.sqlite")
    checks.add(
        "scenario 8: the accepted result survives a restart",
        (await reopened.latest_result(RUN, RESEARCH)) is not None,
        "",
    )

    await reopened.open_execution(RUN, RESEARCH, "exec-research-2")
    checks.add(
        "scenario 8: a re-dispatched task does not read as complete off the old result",
        await reopened.latest_result(RUN, RESEARCH) is None,
        "",
    )
    stale_rejected = False
    try:
        await reopened.submit_result(research)
    except StaleExecutionError:
        stale_rejected = True
    checks.add("scenario 8: a late result from the superseded execution is refused", stale_rejected, "")
    checks.add(
        "scenario 8: the superseded execution stays in the history, so the trail is auditable",
        [record.execution_id for record in await reopened.execution_history(RUN, RESEARCH)]
        == ["exec-research-1", "exec-research-2"],
        "",
    )
    await reopened.close()


async def _scenario_aggregator_contracts(checks: Checks, tmp: Path) -> None:
    """The citation -> aggregator and trim -> aggregator contracts, end to end."""
    harness, index, repo = await _new_harness(tmp, "contracts")
    research, note = await harness.run_task(
        task_id=RESEARCH, role="research", query="源码版本", execution_id="exec-research-1"
    )
    if research is None:
        checks.add("the contract scenario's prerequisite ran", False, note)
        await index.close()
        await repo.close()
        return

    # A claim citing evidence the task never received must be refused by the
    # citation validator -- and the harness refuses to submit such a result.
    fabricated = Claim(text="无依据的断言", kind=ClaimKind.FACT, evidence_ids=("ev-nonexistent",))
    citation = CitationValidator().validate((fabricated,), available_evidence_ids={"ev-1"})
    checks.add(
        "a result whose claim cites evidence the task never had is refused before submission",
        not citation.valid and "unknown_evidence" in citation.codes(),
        f"{citation.codes()}",
    )

    # What the aggregator must do with such a claim if it ever arrives anyway.
    contaminated = research.model_copy(update={"claims": research.claims + (fabricated,)})
    final = Aggregator().merge(
        run_id=RUN,
        results=[contaminated],
        unverified_claim_texts=("无依据的断言",),
        unavailable_evidence_ids=("ev-trimmed",),
    )
    checks.add(
        "an unsupported claim reaches the deliverable relabelled, not as a fact",
        UNVERIFIED_MARKER in final.answer and "[fact] 无依据的断言" not in final.answer,
        final.answer.replace("\n", " | "),
    )
    checks.add(
        "its fabricated evidence stays out of the deliverable's evidence list",
        "ev-nonexistent" not in final.evidence_refs,
        f"evidence_refs={final.evidence_refs}",
    )
    checks.add(
        "evidence trimmed away by the context budget is reported as a limitation",
        any("ev-trimmed" in line for line in final.limitations),
        f"{final.limitations}",
    )

    await index.close()
    await repo.close()


async def _scenario_budget_exhaustion(checks: Checks, tmp: Path) -> None:
    """Scenario 9: running out of budget stops work and yields a bounded partial."""
    harness, index, repo = await _new_harness(tmp, "budget")
    # Keep the finalization reserve: it is the rule this module exists for.
    harness.budget = BudgetManager(max_model_calls=1, max_tokens=1000, finalization_reserve_tokens=500)

    first, note = await harness.run_task(
        task_id=RESEARCH, role="research", query="源码版本", execution_id="exec-research-1"
    )
    checks.add("scenario 9: the first task fits the budget", first is not None, note)

    second, note = await harness.run_task(
        task_id=ENGINEERING,
        role="engineering",
        query="架构设计",
        execution_id="exec-engineering-1",
        depends_on=(RESEARCH,),
    )
    checks.add(
        "scenario 9: once the budget is gone, no further work starts",
        second is None and "budget refused" in note,
        note,
    )

    if first is None:
        await index.close()
        await repo.close()
        return

    checks.add(
        "scenario 9: the finalization reserve survived the ordinary work",
        harness.budget.finalization_tokens_remaining == 500,
        f"finalization_remaining={harness.budget.finalization_tokens_remaining}",
    )

    final = Aggregator().merge(
        run_id=RUN,
        results=[first],
        required_acceptance_ids=(f"acc-{RESEARCH}", f"acc-{ENGINEERING}"),
        # engineering never ran, so it has no verdict at all -- an unrun task
        # cannot have passed its acceptance
        acceptance={f"acc-{RESEARCH}": _acceptance(RESEARCH)},
        usage=Usage(model_calls=1, tokens=150, quality=SYNTHETIC_QUALITY),
    )
    checks.add(
        "scenario 9: the run delivers a bounded partial rather than claiming success",
        final.outcome is FinalOutcome.PARTIAL
        and any(f"acc-{ENGINEERING}" in line for line in final.limitations),
        f"outcome={final.outcome.value} limitations={final.limitations}",
    )

    await index.close()
    await repo.close()


SCENARIOS = (
    ("dependency chain (1, 2)", _scenario_dependency_chain),
    ("cancelled prerequisite (5)", _scenario_cancelled_prerequisite),
    ("plan and completion guards (6)", _scenario_plan_and_completion_guards),
    ("scope isolation (4)", _scenario_scope_isolation),
    ("replay and late results (8)", _scenario_replay_and_late_results),
    ("aggregator contracts", _scenario_aggregator_contracts),
    ("budget exhaustion (9)", _scenario_budget_exhaustion),
)


async def main() -> int:
    checks = Checks()
    with tempfile.TemporaryDirectory(prefix="p0e-") as tmp:
        root = Path(tmp)
        for label, scenario in SCENARIOS:
            try:
                await scenario(checks, root)
            except Exception as exc:
                # One scenario blowing up must not cost the remaining ones.
                checks.add(f"{label}: the scenario completed", False, f"{type(exc).__name__}: {exc}")

    return checks.report(
        RESULTS_PATH,
        scope="offline end-to-end acceptance; no framework runtime, no model, no network",
        not_run=[
            "scenario 3 (embedding failure -> keyword_only): needs an embedding provider",
            "scenario 7 (long tool chain, compression recall): needs the framework context rail",
            "scenario 10 (memory writers): team and member memory are not wired",
            "scenario 2's runtime half: keeping the roster at three during execution needs a live Runner",
        ],
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
