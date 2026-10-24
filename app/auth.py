"""Signed-cookie sessions and the preseeded account check.

The public link is protected by one shared preseeded account, but every login
gets its own session id. Runs are scoped to that id, so two judges using the
same account still cannot see each other's work.

The cookie carries ``user``, ``sid`` and ``iat`` and is signed with
``itsdangerous``; expiry is checked against an injectable clock so the boundary
is testable without sleeping.
"""
from __future__ import annotations

import hmac
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from itsdangerous import BadData, URLSafeSerializer

__all__ = ["COOKIE_NAME", "Session", "SessionCodec", "credentials_ok"]

COOKIE_NAME = "wb_session"
_SALT = "workbench-session"
# A cookie timestamped further in the future than this is treated as forged:
# otherwise a skewed signer could mint a session that outlives the policy.
_FUTURE_SKEW_SECONDS = 300


@dataclass(frozen=True)
class Session:
    """One browser login."""

    user: str
    sid: str


class SessionCodec:
    """Sign and verify session cookies."""

    def __init__(
        self,
        secret: str,
        *,
        max_age_seconds: int = 604800,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("a session secret is required")
        self._serializer = URLSafeSerializer(secret, salt=_SALT)
        self._max_age = int(max_age_seconds)
        self._clock = clock if clock is not None else time.time

    def encode(self, session: Session) -> str:
        """Return a signed cookie value for ``session``."""
        return self._serializer.dumps(
            {"user": session.user, "sid": session.sid, "iat": int(self._clock())}
        )

    def decode(self, token: str) -> Session | None:
        """Return the session a valid token carries, or ``None``."""
        if not token:
            return None
        try:
            payload = self._serializer.loads(token)
        except BadData:
            return None
        if not isinstance(payload, dict):
            return None
        user, sid, issued_at = payload.get("user"), payload.get("sid"), payload.get("iat")
        if not isinstance(user, str) or not user:
            return None
        if not isinstance(sid, str) or not sid:
            return None
        if not isinstance(issued_at, int):
            return None
        now = self._clock()
        if issued_at > now + _FUTURE_SKEW_SECONDS:
            return None
        if now - issued_at > self._max_age:
            return None
        return Session(user=user, sid=sid)


def credentials_ok(users: Mapping[str, str], user: str, password: str) -> bool:
    """Return whether ``user``/``password`` match a preseeded account.

    The comparison is constant-time. An unknown user still performs one
    comparison against a fixed dummy so the fast path does not reveal whether
    the username exists.
    """
    expected = users.get(user)
    supplied = password.encode("utf-8", errors="replace")
    if expected is None:
        hmac.compare_digest(supplied, b"\x00" * 32)
        return False
    return hmac.compare_digest(expected.encode("utf-8"), supplied)
