"""Process settings for the online workbench.

Everything the service reads from the environment lives here, so the API and
the supervisor agree on names and defaults. Secrets are never logged and never
serialized into a snapshot.

The public deployment is a single container: ``production`` refuses to start
without a real session secret, while local development generates an ephemeral
one so a fresh checkout runs without ceremony.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Settings"]

MIN_PRODUCTION_SECRET = 32


@dataclass(frozen=True)
class Settings:
    """Everything the service needs from its environment."""

    data_dir: Path
    users: dict[str, str]
    session_secret: str
    environment: str
    model_base_url: str
    model_api_key: str
    model_name: str
    max_concurrent_runs: int
    max_concurrent_per_session: int
    max_runs_per_day: int
    run_deadline_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ``)."""
        values = os.environ if env is None else env
        environment = str(values.get("WORKBENCH_ENV", "development")).strip().lower()
        if environment not in ("development", "production"):
            raise RuntimeError("WORKBENCH_ENV must be 'development' or 'production'")

        secret = str(values.get("SESSION_SECRET", "")).strip()
        if environment == "production":
            if len(secret) < MIN_PRODUCTION_SECRET:
                raise RuntimeError(
                    f"SESSION_SECRET must be at least {MIN_PRODUCTION_SECRET} characters in production"
                )
        elif not secret:
            secret = secrets.token_urlsafe(32)

        return cls(
            data_dir=Path(values.get("WORKBENCH_DATA_DIR", "./data")).expanduser().resolve(),
            users=_parse_users(values.get("WORKBENCH_USERS", "")),
            session_secret=secret,
            environment=environment,
            model_base_url=str(values.get("MODEL_BASE_URL", "")).strip(),
            model_api_key=str(values.get("MODEL_API_KEY", "")).strip(),
            model_name=str(values.get("MODEL_NAME", "")).strip(),
            max_concurrent_runs=_int_env(values, "WORKBENCH_MAX_CONCURRENT_RUNS", 2),
            max_concurrent_per_session=_int_env(
                values, "WORKBENCH_MAX_CONCURRENT_PER_SESSION", 1
            ),
            max_runs_per_day=_int_env(values, "WORKBENCH_MAX_RUNS_PER_DAY", 30),
            run_deadline_seconds=_int_env(values, "WORKBENCH_ROUND_DEADLINE_SECONDS", 300),
        )

    def model_credentials(self) -> dict[str, str]:
        """Return the credential mapping ``build_workbench_config`` expects."""
        return {
            "MODEL_BASE_URL": self.model_base_url,
            "MODEL_API_KEY": self.model_api_key,
            "MODEL_NAME": self.model_name,
        }


def _int_env(values: Mapping[str, str], key: str, default: int) -> int:
    raw = str(values.get(key, "")).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be an integer, got {raw!r}") from None
    if parsed < 1:
        raise RuntimeError(f"{key} must be positive, got {parsed}")
    return parsed


def _parse_users(raw: str) -> dict[str, str]:
    """Parse ``user:password;user:password`` into a mapping.

    A malformed entry is a startup error: silently dropping an account would
    leave the public link with fewer logins than the operator thinks it has.
    """
    users: dict[str, str] = {}
    for entry in str(raw or "").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise RuntimeError(
                "WORKBENCH_USERS entries must be 'user:password' separated by ';'"
            )
        user, password = entry.split(":", 1)
        user, password = user.strip(), password.strip()
        if not user or not password:
            raise RuntimeError("WORKBENCH_USERS entries need a non-empty user and password")
        if user in users:
            raise RuntimeError(f"WORKBENCH_USERS defines {user!r} more than once")
        users[user] = password
    return users
