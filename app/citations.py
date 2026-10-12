"""Citation validation: a claim of fact must point at evidence the task had.

PROJECT.md section 6 requires the final aggregation to distinguish what was
verified from what was not, and section 12's acceptance plan measures a
"citation support rate". Both need the same check: for every claim a result
makes, is the evidence it cites something the task actually received?

"Retrievable" is the strict reading on purpose. An evidence id that exists in
the corpus but was never in this task's context is not support -- the member
never saw it, so the claim was not derived from it. Trimming makes this
reachable: the context builder drops evidence when the budget is tight, and a
result that cites what was dropped is citing something it did not have.

This module holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.models import Claim, ClaimKind, distinct_codes, non_blank

__all__ = [
    "CitationIssue",
    "CitationIssueCode",
    "CitationReport",
    "CitationValidator",
]


class CitationIssueCode(str, Enum):
    """What is wrong with a result's citations."""

    BLANK_CLAIM = "blank_claim"
    """The claim carries no text."""

    UNCITED_FACT = "uncited_fact"
    """A claim presented as fact cites no evidence."""

    UNKNOWN_EVIDENCE = "unknown_evidence"
    """A cited evidence id was not among the evidence the task received."""


class CitationIssue(BaseModel):
    """One problem found in a result's claims."""

    model_config = ConfigDict(extra="forbid")

    code: CitationIssueCode
    claim_index: int
    evidence_ids: tuple[str, ...] = ()
    """The evidence ids the issue is about, as data.

    Callers computing a support rate must not have to parse ``detail`` text to
    find which citation failed.
    """

    detail: str = ""


class CitationReport(BaseModel):
    """The validator's verdict."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: tuple[CitationIssue, ...] = ()

    def codes(self) -> list[CitationIssueCode]:
        """Return the distinct issue codes, for compact assertions."""
        return distinct_codes(self.issues)


class CitationValidator:
    """Check a result's claims against the evidence its task was given."""

    def validate(
        self,
        claims: Sequence[Claim],
        *,
        available_evidence_ids: Iterable[str],
    ) -> CitationReport:
        """Return a report describing every problem found, not just the first.

        Raises:
            TypeError: ``available_evidence_ids`` is a single string. A string
                is iterable, so it would otherwise be read as a set of
                characters and reject every valid citation without saying why.
        """
        if isinstance(available_evidence_ids, str):
            raise TypeError("available_evidence_ids must be a collection of ids, not a single string")
        available = set(available_evidence_ids)

        issues: list[CitationIssue] = []

        for index, claim in enumerate(claims):
            if not claim.text.strip():
                issues.append(
                    CitationIssue(code=CitationIssueCode.BLANK_CLAIM, claim_index=index, detail="empty claim text")
                )

            # Blank ids are not citations: a fact carrying only blank entries is
            # uncited, exactly as every other gate in this codebase treats them.
            cited = non_blank(claim.evidence_ids)
            if claim.kind is ClaimKind.FACT and not cited:
                issues.append(
                    CitationIssue(
                        code=CitationIssueCode.UNCITED_FACT,
                        claim_index=index,
                        detail=f"claim presented as fact cites no evidence: {claim.text[:40]!r}",
                    )
                )

            missing = [evidence_id for evidence_id in cited if evidence_id not in available]
            if missing:
                issues.append(
                    CitationIssue(
                        code=CitationIssueCode.UNKNOWN_EVIDENCE,
                        claim_index=index,
                        evidence_ids=tuple(missing),
                        detail="cited evidence was not in this task's context",
                    )
                )

        return CitationReport(valid=not issues, issues=tuple(issues))
