"""The member-facing retrieval tool.

This is the only place that decides what a task "actually received": the ids
returned by this call are recorded against the task, and the citation
validator checks facts against that set. A member cannot cite a passage it
never retrieved, and cannot retrieve for a task assigned to someone else.

The index is opened lazily on the first call, so the provider factory stays
synchronous and a run without a corpus simply has no tool.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

from app.hooks import get_bridge
from app.knowledge import KnowledgeIndex
from app.run_bridge import RunBridge

_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "The native task id this retrieval belongs to.",
        },
        "query": {
            "type": "string",
            "description": "Terms to search for; quote exact phrases when needed.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum passages to return (1-20).",
        },
    },
    "required": ["task_id", "query"],
}
MAX_LIMIT = 20


class RetrievalTool(TeamTool):
    """Search the run's authorised corpus for the caller's task."""

    def __init__(
        self,
        bridge: RunBridge,
        index: KnowledgeIndex | None = None,
        *,
        producer: str,
        corpus: str,
        access_scope: str,
        db_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            ToolCard(
                id="app.retrieve",
                name="retrieve",
                description=(
                    "Retrieve evidence passages from this workspace's corpus. "
                    "Facts in submit_task_result must cite ids returned here."
                ),
            )
        )
        self.bridge = bridge
        self.producer = producer
        self.corpus = corpus
        self.access_scope = access_scope
        self._index = index
        self._db_path = Path(db_path) if db_path is not None else None
        self.card.input_params = _SCHEMA

    async def _open_index(self) -> KnowledgeIndex:
        if self._index is None:
            if self._db_path is None:
                raise RuntimeError("retrieval tool has neither an index nor a database path")
            self._index = await KnowledgeIndex.open(
                self._db_path, corpus=self.corpus, access_scope=self.access_scope
            )
        return self._index

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        task_id = str(inputs.get("task_id") or "").strip()
        query = str(inputs.get("query") or "").strip()
        if not task_id or not query:
            return ToolOutput(
                success=False, error="'task_id' and 'query' are required"
            )

        assignee = self.bridge.assignee_for(task_id)
        if assignee is None:
            return ToolOutput(
                success=False, error=f"task {task_id!r} is not in the plan for this run"
            )
        if assignee != self.producer:
            return ToolOutput(
                success=False,
                error=(
                    f"{self.producer!r} is not the assignee of {task_id!r} "
                    f"(assigned to {assignee!r})"
                ),
            )

        try:
            limit = int(inputs.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        limit = max(1, min(limit, MAX_LIMIT))

        try:
            index = await self._open_index()
            evidence = await index.search(
                query, limit=limit, authorized_scopes=(self.access_scope,)
            )
        except Exception as exc:  # noqa: BLE001 - returned to the model, not raised
            return ToolOutput(
                success=False, error=f"retrieval failed: {type(exc).__name__}: {exc}"
            )

        evidence_ids = [item.evidence_id for item in evidence]
        self.bridge.add_evidence(task_id, evidence_ids)
        return ToolOutput(
            success=True,
            data={
                "evidence_ids": evidence_ids,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "text": item.text,
                        "source_uri": item.source_uri,
                        "source_revision": item.source_revision,
                        "locator": item.locator,
                    }
                    for item in evidence
                ],
                "retrieval_method": evidence[0].retrieval_method if evidence else "",
            },
        )


def make_retrieval_tool(params: dict[str, Any], context: Any) -> RetrievalTool:
    """Provider factory: bind the member identity and the corpus location."""
    producer = getattr(context, "member_name", None)
    if not producer:
        raise RuntimeError("retrieve needs a BuildContext.member_name")
    return RetrievalTool(
        get_bridge(params["run_id"]),
        producer=str(producer),
        corpus=str(params["corpus"]),
        access_scope=str(params["access_scope"]),
        db_path=params["db_path"],
    )
