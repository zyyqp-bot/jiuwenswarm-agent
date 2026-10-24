"""Record the tool names the framework actually sends to the model.

The template disables ``sys_operation``, but "the template says so" is not the
same as "the request contains no shell tool". The framework's own
``log_llm_request`` receives the assembled tool list right before the call, so
wrapping it is the narrowest place to observe the truth without changing what
is sent.

The wrapper records names and delegates unchanged. It never raises into the
model call: a probe failure must not break the run.
"""
from __future__ import annotations

from typing import Any, Callable


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        return name
    card = getattr(tool, "card", None)
    if card is not None:
        card_name = getattr(card, "name", None)
        if isinstance(card_name, str) and card_name:
            return card_name
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict):
            fn_name = function.get("name")
            if isinstance(fn_name, str) and fn_name:
                return fn_name
        dict_name = tool.get("name")
        if isinstance(dict_name, str) and dict_name:
            return dict_name
    return ""


def install_request_probe(record: Callable[[list[str]], None]) -> None:
    """Wrap ``react_agent.log_llm_request`` so each request's tool names reach ``record``.

    Idempotent: installing twice keeps one wrapper, so a run cannot record the
    same request twice through two wrappers.
    """
    from openjiuwen.core.single_agent.agents import react_agent

    original = react_agent.log_llm_request
    if getattr(original, "_app_request_probe", False):
        return

    def wrapper(log: Any, messages: Any, tools: Any) -> None:
        try:
            names = [name for name in (_tool_name(tool) for tool in tools or []) if name]
            record(names)
        except Exception:  # noqa: BLE001 - a probe must not break the call
            pass
        return original(log, messages, tools)

    wrapper._app_request_probe = True  # type: ignore[attr-defined]
    react_agent.log_llm_request = wrapper
