"""Preseeded workspaces: a corpus, a scope and the prompts that frame it.

The engine carries no industry logic; a workspace is the configuration that
turns it into one vertical. The public link exposes only these preseeded
workspaces — it never accepts an upload.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["Workspace", "WORKSPACES", "get_workspace", "workspace_summaries"]


@dataclass(frozen=True)
class Workspace:
    """One vertical instance."""

    name: str
    description: str
    corpus: str
    access_scope: str


WORKSPACES: dict[str, Workspace] = {
    "education-policy": Workspace(
        name="education-policy",
        description="教育政策：AI 教学工具合规评估与引用报告",
        corpus="education-policy",
        access_scope="public",
    ),
    "ai-governance": Workspace(
        name="ai-governance",
        description="AI 治理与数据合规：公开政策法规的研读、引用与冲突报告",
        corpus="ai-governance",
        access_scope="public",
    ),
    "medical-ai-compliance": Workspace(
        name="medical-ai-compliance",
        description="医疗健康政策：诊疗、数据与 AI 辅助器械的合规研读",
        corpus="medical-ai-compliance",
        access_scope="public",
    ),
}


def get_workspace(name: str) -> Workspace | None:
    """Return a preseeded workspace, or ``None``."""
    return WORKSPACES.get(name)


def workspace_summaries() -> list[dict[str, str]]:
    """Return the public view of every workspace."""
    return [
        {"name": item.name, "description": item.description, "corpus": item.corpus}
        for item in WORKSPACES.values()
    ]
