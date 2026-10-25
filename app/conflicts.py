"""Candidate conflicts across sources — never a verdict.

The spec's MVP boundary is narrow on purpose: only claims that

* are labelled ``fact``,
* come from different sources (``source_uri`` differs),
* share the same sentence skeleton once values are masked, and
* state different values

become **candidates**. The panel shows both passages and their locations; it
does not decide which one is right, and it never merges them into an average.

Anything semantically richer needs its own evaluation and is out of scope: a
detector that guesses at meaning would invent conflicts in a corpus of public
regulations, which is exactly the failure the report must avoid.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models import ClaimKind, TaskResult

__all__ = ["ConflictCandidate", "ConflictSide", "detect_conflicts"]


_UNITS = (
    "个工作日",
    "工作日",
    "小时",
    "万元",
    "年",
    "月",
    "日",
    "天",
    "次",
    "条",
    "款",
    "项",
    "章",
    "个",
    "元",
    "倍",
    "人",
    "%",
)
_UNIT_PATTERN = "|".join(re.escape(unit) for unit in _UNITS)
_CN_DIGITS = "零一二三四五六七八九十百千万亿两"
_VALUE_RE = re.compile(
    rf"(?:\d+(?:\.\d+)?(?:\s*(?:{_UNIT_PATTERN}))?|[{_CN_DIGITS}]+(?:{_UNIT_PATTERN}))"
)
_NUM_RE = re.compile(rf"^(\d+(?:\.\d+)?|[{_CN_DIGITS}]+)(.*)$")
_PUNCT_RE = re.compile(r"[\s，。；：、（）()《》「」“”‘’！？!,.;:]+")

_CN_DIGIT_VALUES = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNIT_VALUES = {"十": 10, "百": 100, "千": 1000}

MIN_PATTERN_CHARS = 4


@dataclass(frozen=True)
class ConflictSide:
    """One claim's passage behind one conflicting value."""

    task_id: str
    claim_text: str
    evidence_id: str
    source_uri: str
    locator: str
    passage: str


@dataclass(frozen=True)
class ConflictCandidate:
    """A pattern two sources state differently."""

    pattern: str
    values: tuple[str, ...]
    sides: tuple[ConflictSide, ...]


def _cn_to_int(text: str) -> int | None:
    """Parse the common Chinese numeral forms (0–9999)."""
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in _CN_DIGIT_VALUES:
            number = _CN_DIGIT_VALUES[char]
        elif char in _CN_UNIT_VALUES:
            section += (number or 1) * _CN_UNIT_VALUES[char]
            number = 0
        elif char == "万":
            total += (section + number) * 10000
            section = 0
            number = 0
        elif char == "亿":
            total += (section + number) * 100000000
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _canonical_value(raw: str) -> str:
    """``十个工作日`` and ``10个工作日`` are the same value."""
    text = re.sub(r"\s+", "", raw)
    match = _NUM_RE.match(text)
    if match is None:
        return text
    numeral, unit = match.group(1), match.group(2)
    if numeral[0].isdigit():
        return f"{numeral}{unit}"
    parsed = _cn_to_int(numeral)
    return f"{parsed}{unit}" if parsed is not None else text


def _mask(text: str) -> tuple[str, tuple[str, ...]]:
    """Return the punctuation-free skeleton and the canonical values.

    A value immediately after ``第`` is an article number (``第十条``), not a
    quantity; masking it would make unrelated sections look like a conflict.
    """
    parts: list[str] = []
    values: list[str] = []
    cursor = 0
    for match in _VALUE_RE.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        if before == "第":
            continue
        parts.append(text[cursor : match.start()])
        parts.append("#")
        values.append(_canonical_value(match.group(0)))
        cursor = match.end()
    parts.append(text[cursor:])
    pattern = _PUNCT_RE.sub("", "".join(parts))
    return pattern, tuple(values)


def detect_conflicts(
    results: Sequence[TaskResult],
    evidence_details: Mapping[str, Mapping[str, Any]],
) -> list[ConflictCandidate]:
    """Return deterministic candidate conflicts, sorted for stable output."""
    groups: dict[str, list[tuple[ConflictSide, tuple[str, ...]]]] = {}
    for result in results:
        for claim in result.claims:
            if claim.kind is not ClaimKind.FACT:
                continue
            pattern, values = _mask(claim.text)
            if len(pattern) < MIN_PATTERN_CHARS or not values:
                continue
            for evidence_id in claim.evidence_ids:
                detail = evidence_details.get(evidence_id)
                if not detail:
                    # No recorded source means no way to prove the sides are
                    # independent; skipping is the honest choice.
                    continue
                source_uri = str(detail.get("source_uri") or "")
                if not source_uri:
                    continue
                side = ConflictSide(
                    task_id=result.task_id,
                    claim_text=claim.text,
                    evidence_id=evidence_id,
                    source_uri=source_uri,
                    locator=str(detail.get("locator") or ""),
                    passage=str(detail.get("text") or ""),
                )
                groups.setdefault(pattern, []).append((side, values))

    candidates: list[ConflictCandidate] = []
    for pattern, entries in groups.items():
        if not _has_cross_source_difference(entries):
            continue
        sides = tuple(
            sorted(
                {entry_side for entry_side, _ in entries},
                key=lambda side: (
                    side.source_uri,
                    side.task_id,
                    side.evidence_id,
                    side.claim_text,
                ),
            )
        )
        values = tuple(sorted({value for _, side_values in entries for value in side_values}))
        candidates.append(ConflictCandidate(pattern=pattern, values=values, sides=sides))
    return sorted(candidates, key=lambda candidate: (candidate.pattern, candidate.values))


def _has_cross_source_difference(
    entries: Sequence[tuple[ConflictSide, tuple[str, ...]]],
) -> bool:
    """Two sides, different sources, different value sets."""
    for index, (left, left_values) in enumerate(entries):
        for right, right_values in entries[index + 1 :]:
            if left.source_uri == right.source_uri:
                continue
            if set(left_values) != set(right_values):
                return True
    return False
