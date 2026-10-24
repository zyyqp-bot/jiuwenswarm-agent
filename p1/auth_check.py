"""P2: settings, password check and the signed session cookie.

The public link is only as safe as these three pieces:

- a preseeded account is verified in constant time;
- the cookie is signed, carries a per-login session id, and expires;
- production refuses to start with a missing or short secret.

Offline: no network, no server, no model.

Run: .venv/Scripts/python.exe p1/auth_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import Session, SessionCodec, credentials_ok  # noqa: E402
from app.settings import Settings  # noqa: E402
from checks.harness import Checks  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "auth_results.json"

USERS = {"judge": "correct horse battery staple"}


def _dev_env(**overrides: str) -> dict[str, str]:
    env = {
        "WORKBENCH_ENV": "development",
        "WORKBENCH_USERS": "judge:correct horse battery staple",
        "MODEL_BASE_URL": "http://127.0.0.1:9/v1",
        "MODEL_API_KEY": "test-key",
        "MODEL_NAME": "test-model",
    }
    env.update(overrides)
    return env


def _codec(clock=None, *, max_age: int = 60) -> SessionCodec:
    return SessionCodec("s" * 40, max_age_seconds=max_age, clock=clock)


def main() -> int:
    checks = Checks()

    # -- passwords ----------------------------------------------------------
    checks.add(
        "the correct password is accepted",
        credentials_ok(USERS, "judge", "correct horse battery staple"),
        "correct credentials",
    )
    checks.add(
        "a wrong password is rejected",
        not credentials_ok(USERS, "judge", "wrong"),
        "wrong password",
    )
    checks.add(
        "an unknown user is rejected",
        not credentials_ok(USERS, "nobody", "correct horse battery staple"),
        "unknown user",
    )

    # -- cookie round trip --------------------------------------------------
    codec = _codec()
    session = Session(user="judge", sid="sid-one")
    token = codec.encode(session)
    decoded = codec.decode(token)
    checks.add(
        "a signed cookie round-trips the user and session",
        decoded == session,
        f"decoded={decoded}",
    )

    other = Session(user="judge", sid="sid-two")
    checks.add(
        "two logins get different session ids",
        codec.decode(codec.encode(other)) == other and token != codec.encode(other),
        "two distinct sessions",
    )

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    checks.add(
        "a tampered cookie is rejected",
        codec.decode(tampered) is None,
        f"tampered={tampered[-8:]}",
    )
    checks.add(
        "a garbage cookie is rejected",
        codec.decode("not-a-token") is None,
        "garbage",
    )

    # -- expiry -------------------------------------------------------------
    issued_at = 1000.0
    issued = _codec(clock=lambda: issued_at, max_age=5).encode(session)
    checks.add(
        "a cookie is still valid at the age boundary",
        _codec(clock=lambda: issued_at + 5, max_age=5).decode(issued) == session,
        "age == max_age",
    )
    checks.add(
        "an expired cookie is rejected",
        _codec(clock=lambda: issued_at + 6, max_age=5).decode(issued) is None,
        "age > max_age",
    )

    # -- settings -----------------------------------------------------------
    production_missing = ""
    try:
        Settings.from_env({"WORKBENCH_ENV": "production", "WORKBENCH_USERS": "judge:x"})
    except RuntimeError as exc:
        production_missing = str(exc)
    checks.add(
        "production refuses a missing session secret",
        "SESSION_SECRET" in production_missing,
        production_missing or "no error",
    )

    production_short = ""
    try:
        Settings.from_env(
            {"WORKBENCH_ENV": "production", "SESSION_SECRET": "short", "WORKBENCH_USERS": "judge:x"}
        )
    except RuntimeError as exc:
        production_short = str(exc)
    checks.add(
        "production refuses a short session secret",
        "32" in production_short,
        production_short or "no error",
    )

    # A copied .env.example must not boot: the secret and passwords in it are
    # public, and a forged cookie only needs the secret.
    placeholder_secret = ""
    try:
        Settings.from_env(
            {
                "WORKBENCH_ENV": "production",
                "SESSION_SECRET": "replace-with-at-least-32-random-characters",
                "WORKBENCH_USERS": "judge1:replace-me",
                "MODEL_BASE_URL": "https://example.test/v1",
                "MODEL_API_KEY": "replace-with-provider-key",
                "MODEL_NAME": "test-model",
            }
        )
    except RuntimeError as exc:
        placeholder_secret = str(exc)
    checks.add(
        "production refuses the template session secret",
        "placeholder" in placeholder_secret.lower(),
        placeholder_secret or "no error",
    )

    placeholder_user = ""
    try:
        Settings.from_env(
            {
                "WORKBENCH_ENV": "production",
                "SESSION_SECRET": "a" * 40,
                "WORKBENCH_USERS": "judge1:replace-me",
                "MODEL_BASE_URL": "https://example.test/v1",
                "MODEL_API_KEY": "real-looking-key",
                "MODEL_NAME": "test-model",
            }
        )
    except RuntimeError as exc:
        placeholder_user = str(exc)
    checks.add(
        "production refuses placeholder account passwords",
        "placeholder" in placeholder_user.lower(),
        placeholder_user or "no error",
    )

    missing_model = ""
    try:
        Settings.from_env(
            {
                "WORKBENCH_ENV": "production",
                "SESSION_SECRET": "a" * 40,
                "WORKBENCH_USERS": "judge1:secret-one",
            }
        )
    except RuntimeError as exc:
        missing_model = str(exc)
    checks.add(
        "production refuses to start without model credentials",
        "MODEL" in missing_model,
        missing_model or "no error",
    )

    usable = Settings.from_env(
        {
            "WORKBENCH_ENV": "production",
            "SESSION_SECRET": "a" * 40,
            "WORKBENCH_USERS": "judge1:secret-one",
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "provider-key",
            "MODEL_NAME": "test-model",
        }
    )
    checks.add(
        "a fully configured production process starts",
        usable.environment == "production" and list(usable.users) == ["judge1"],
        f"users={list(usable.users)}",
    )

    first = Settings.from_env(_dev_env())
    second = Settings.from_env(_dev_env())
    checks.add(
        "development generates an ephemeral secret per process",
        len(first.session_secret) >= 32 and first.session_secret != second.session_secret,
        f"len={len(first.session_secret)} distinct={first.session_secret != second.session_secret}",
    )
    checks.add(
        "the preseeded users are parsed",
        first.users == USERS,
        f"users={first.users}",
    )

    malformed = ""
    try:
        Settings.from_env(_dev_env(WORKBENCH_USERS="judge:one;broken"))
    except RuntimeError as exc:
        malformed = str(exc)
    checks.add(
        "a malformed user entry is rejected",
        "WORKBENCH_USERS" in malformed,
        malformed or "no error",
    )

    checks.add(
        "the model credentials are exposed as the supervisor expects",
        first.model_credentials()
        == {
            "MODEL_BASE_URL": "http://127.0.0.1:9/v1",
            "MODEL_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
        },
        f"credentials={list(first.model_credentials())}",
    )
    checks.add(
        "limits and deadlines have usable defaults",
        first.max_concurrent_runs >= 1
        and first.max_runs_per_day >= 1
        and first.run_deadline_seconds >= 60,
        f"concurrency={first.max_concurrent_runs} daily={first.max_runs_per_day} "
        f"deadline={first.run_deadline_seconds}",
    )

    return checks.report(
        RESULTS_PATH,
        scope="settings and auth unit checks only; no server, no network, no model call",
    )


if __name__ == "__main__":
    sys.exit(main())
