"""Context builder: assemble a task's input within a model's input budget.

PROJECT.md section 7.1 fixes the rules this module implements:

- input budget = ``context_window - max_output_tokens - safety_margin``, and it
  must account for the system prompt, skills, tool schemas, history and tool
  results -- not just the parts this builder assembles, so the caller passes the
  known fixed overhead in;
- the current goal, the user's hard constraints, the acceptance conditions and
  the task input are **incompressible** -- they are never the thing that gets
  trimmed;
- evidence is what gives way when the budget is tight, and evidence is dropped
  in relevance order: once one item does not fit, the less relevant items behind
  it are not smuggled in ahead of it;
- without a real tokenizer the count is a **conservative estimate** and is
  labelled as one; it is never presented as an exact count.

History compression is deliberately not handled here. Section 7.1 and D07 give
that to the framework's own context rail, so the application does not summarise
the same history twice. Recomputing the budget *after* that rail compresses is
therefore the caller's step, and is tracked as outstanding work in PROJECT.md.

It holds no framework objects, starts no runtime and calls no model.
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.models import Evidence, non_blank

__all__ = [
    "CJK_RANGES",
    "ESTIMATED_TOKENIZER_ID",
    "ContextBudget",
    "ContextBundle",
    "ContextBuilder",
    "ContextLimitExceeded",
    "estimate_tokens",
]

ESTIMATED_TOKENIZER_ID = "estimate:cjk-1-other-0.25"
"""Recorded on every bundle built without a real tokenizer.

A count labelled with this id is an upper-ish bound, not a measurement.
"""

CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul jamo
    (0x2E80, 0x2EFF),  # CJK radicals
    (0x3000, 0x30FF),  # CJK punctuation, kana
    (0x3130, 0x318F),  # Hangul compatibility jamo
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xA960, 0xA97F),  # Hangul jamo extended A
    (0xAC00, 0xD7A3),  # Hangul syllables
    (0xD7B0, 0xD7FF),  # Hangul jamo extended B
    (0xF900, 0xFAFF),  # compatibility ideographs
    (0xFF00, 0xFFEF),  # fullwidth forms
    (0x20000, 0x2FA1F),  # CJK extensions B onward
)
"""CJK and Hangul ranges counted at one token per character.

Hangul belongs here: Korean syllable blocks tokenise like CJK, and counting
them as "other" would under-estimate by roughly four times -- the optimistic
direction the estimate exists to avoid.
"""


class ContextLimitExceeded(RuntimeError):
    """The incompressible part of the context does not fit the input budget.

    Raised rather than trimming, because the parts that would have to go are
    the ones PROJECT.md section 7.1 declares incompressible. The caller's
    recourse is to split the task, not to drop the user's constraints.
    """


def estimate_tokens(text: str) -> int:
    """Estimate a token count without a real tokenizer.

    CJK and Hangul characters count as one token each and everything else as
    one token per four characters, rounded up. This is deliberately pessimistic
    for non-CJK text: an over-estimate leaves headroom, an under-estimate
    silently overruns the window.
    """
    wide = sum(1 for character in text if any(low <= ord(character) <= high for low, high in CJK_RANGES))
    other = len(text) - wide
    return wide + (other + 3) // 4


class ContextBudget(BaseModel):
    """The budget for one model call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_window: int
    max_output_tokens: int
    safety_margin: int = 0

    @property
    def input_budget(self) -> int:
        """Tokens available to the request: window minus output reserve and margin."""
        return self.context_window - self.max_output_tokens - self.safety_margin


class ContextBundle(BaseModel):
    """The assembled input for one task, with what was left out.

    ``dropped_evidence_ids`` is part of the bundle rather than a log line: a
    result built on a trimmed context has to be able to say what it did not
    see.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_ref: str | None = None
    task_ref: str | None = None
    task_instruction: str
    pinned_constraints: tuple[str, ...] = ()
    acceptance_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    dropped_evidence_ids: tuple[str, ...] = ()
    fixed_overhead_tokens: int = 0
    input_token_estimate: int
    tokenizer_id: str = Field(default=ESTIMATED_TOKENIZER_ID)


def _render(evidence: Evidence) -> str:
    """Render one evidence item as it will appear in the request."""
    return f"[{evidence.evidence_id}] {evidence.source_uri}@{evidence.source_revision} {evidence.text}"


class ContextBuilder:
    """Assemble a context bundle from a task's incompressible parts and evidence."""

    def build(
        self,
        *,
        budget: ContextBudget,
        task_instruction: str,
        evidence: Sequence[Evidence] = (),
        pinned_constraints: Sequence[str] = (),
        acceptance_ids: Sequence[str] = (),
        spec_ref: str | None = None,
        task_ref: str | None = None,
        fixed_overhead_tokens: int = 0,
    ) -> ContextBundle:
        """Return a bundle whose estimate fits ``budget.input_budget``.

        ``fixed_overhead_tokens`` is the cost of everything the request carries
        that this builder does not assemble -- system prompt, skill text, tool
        schemas, history, tool results. Section 7.1 requires the budget to
        account for them, and the builder cannot measure them, so the caller
        supplies the number.

        Raises:
            ContextLimitExceeded: the overhead plus the incompressible parts
                exceed the budget. Evidence is trimmed instead; constraints and
                acceptance conditions never are.
        """
        if budget.input_budget <= 0:
            raise ContextLimitExceeded(
                f"input budget is {budget.input_budget}: window {budget.context_window} "
                f"minus output {budget.max_output_tokens} minus margin {budget.safety_margin}"
            )

        constraints = non_blank(pinned_constraints)
        acceptance = non_blank(acceptance_ids)
        # Acceptance ids are incompressible too: they are text the request has
        # to carry, so they belong in the pinned estimate rather than being
        # stored in the bundle for free.
        pinned = [task_instruction, *constraints, *acceptance]
        pinned_estimate = sum(estimate_tokens(part) for part in pinned)

        remaining = budget.input_budget - fixed_overhead_tokens - pinned_estimate
        if remaining < 0:
            raise ContextLimitExceeded(
                f"the fixed overhead ({fixed_overhead_tokens}) plus the incompressible context "
                f"({pinned_estimate}) needs {fixed_overhead_tokens + pinned_estimate} tokens, "
                f"budget is {budget.input_budget}; split the task"
            )

        kept: list[Evidence] = []
        dropped: list[str] = []
        exhausted = False
        for item in evidence:
            # Relevance order is the only order there is: once an item does not
            # fit, the less relevant items behind it must not be admitted ahead
            # of it just because they are smaller.
            if exhausted or estimate_tokens(_render(item)) > remaining:
                exhausted = True
                dropped.append(item.evidence_id)
                continue
            kept.append(item)
            remaining -= estimate_tokens(_render(item))

        return ContextBundle(
            spec_ref=spec_ref,
            task_ref=task_ref,
            task_instruction=task_instruction,
            pinned_constraints=tuple(constraints),
            acceptance_ids=tuple(acceptance),
            evidence=tuple(kept),
            dropped_evidence_ids=tuple(dropped),
            fixed_overhead_tokens=fixed_overhead_tokens,
            input_token_estimate=budget.input_budget - remaining,
            tokenizer_id=ESTIMATED_TOKENIZER_ID,
        )
