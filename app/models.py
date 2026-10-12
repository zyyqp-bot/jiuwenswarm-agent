"""Application data models shared across the MVP modules.

These are the application-layer types from PROJECT.md section 5. They are not
framework SDK types: everything here is Pydantic v2 with ``extra="forbid"`` so
an unknown field is an error rather than a silently ignored one.
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AcceptanceCheck",
    "AcceptanceResult",
    "Claim",
    "ClaimKind",
    "Evidence",
    "TaskOutcome",
    "TaskResult",
    "distinct_codes",
    "non_blank",
    "usable_artifacts",
]


class ClaimKind(str, Enum):
    """How a claim is grounded.

    PROJECT.md requires unverified conclusions not to be presented as fact, so
    the kind is what decides whether evidence is mandatory: a FACT without
    retrievable evidence is a defect, while an INFERENCE or ASSUMPTION is
    allowed to stand unsupported as long as it is labelled as such.
    """

    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"


class Claim(BaseModel):
    """One statement in a task result, with the evidence behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    kind: ClaimKind
    evidence_ids: tuple[str, ...] = ()


class TaskOutcome(str, Enum):
    """Business outcome of one task execution.

    Distinct from the framework's task status: a framework task may be
    ``completed`` while its business outcome is PARTIAL or ERROR.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


class AcceptanceResult(str, Enum):
    """Verdict of one acceptance check."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class TaskResult(BaseModel):
    """The immutable result of one task execution.

    Frozen, and ``artifact_refs`` is a tuple rather than a list, so a caller
    holding a submitted result cannot retune its outcome or append to its
    artifacts behind the gates that already evaluated it.

    ``run_id`` is carried alongside ``task_id``/``execution_id`` because
    execution ids are only unique per ``(run, task)`` in the ledger: without
    the run, a result from another run that happens to reuse the same execution
    id string would look current.

    ``producer`` records which member produced the result. It lives on the
    result rather than being passed to a gate, so "who wrote this" cannot be
    chosen by the caller when it is checked against a reviewer's identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    task_id: str
    execution_id: str
    producer: str
    outcome: TaskOutcome
    artifact_refs: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    """The statements the result makes, each labelled with how it is grounded.

    They live on the result rather than beside it so a submission carries its
    own claims into citation validation; PROJECT.md section 5 specifies the
    field, and section 12's citation support rate is computed over them.
    """


class AcceptanceCheck(BaseModel):
    """One acceptance condition evaluated against a task result.

    ``verifier`` records who judged it. Structural acceptance is decided by
    program, semantic acceptance by a reviewer or the leader; either way the
    check is a first-class record rather than a boolean on the result, and the
    verifier's identity is part of that record so a producer cannot sign off on
    its own work.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    acceptance_id: str
    result: AcceptanceResult
    verifier: str
    evidence_refs: tuple[str, ...] = ()


class Evidence(BaseModel):
    """One retrieved passage, traceable back to the source it came from.

    Every claim in a task result must be able to point at one of these, so a
    passage carries its provenance (``source_uri`` / ``source_revision`` /
    ``locator`` / ``content_hash``) alongside the text. ``retrieval_method``
    records how it was found: PROJECT.md section 7.2 requires a short-query
    fallback to be recorded rather than passed off as BM25, since the two
    produce incomparable ``raw_score`` values.

    ``raw_score`` is a retrieval score, not a confidence and not comparable
    across methods.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    corpus: str
    source_uri: str
    source_revision: str
    doc_id: str
    chunk_id: str
    content_hash: str
    locator: str
    text: str
    retrieval_method: str
    raw_score: float
    fused_rank: int
    access_scope: str


def non_blank(values: Iterable[str]) -> list[str]:
    """Return the entries that actually name something.

    Blank entries never satisfy a requirement: they are not a deliverable, not a
    reviewer, and not an acceptance condition. Every gate that asks "is there at
    least one of these" filters through here.
    """
    return [value for value in values if value.strip()]


def distinct_codes(issues: Iterable[Any]) -> list[Any]:
    """Return the distinct ``code`` values of a validator's issues, in order.

    Every validator report exposes the same compact view of its issues, so the
    shape lives here rather than being reimplemented per report.
    """
    return list(dict.fromkeys(issue.code for issue in issues))


def usable_artifacts(refs: Iterable[str]) -> list[str]:
    """Return the artifact references that actually name something.

    A blank reference is not a deliverable, so it must not satisfy a gate that
    requires a valid artifact (PROJECT.md section 6).
    """
    return non_blank(refs)
