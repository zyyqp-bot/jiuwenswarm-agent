"""P0-A (part 2): Skill visibility declarations and effective-set composition.

Closes the R05 acceptance point that part 1 left open. Skill visibility is NOT
carried on ``TeamAgentSpec``: ``DeepAgentSpec.skills`` is only a seed that
``AgentConfigurator`` clears at build time, and the authoritative set lives in
per-member and per-team ``skills-visibility.json`` files composed by
``compose_skill_visibility``.

This script exercises that path offline -- no Runner, no model, no network --
against real files in a temporary agent-teams home:

- the documented declaration-file layout is produced by the path helpers
- effective enabled = member.allow UNION team.allow
- effective disabled = member.deny UNION team.deny UNION global_disabled
- an empty allow-list stays empty (the rail reads "empty" as unfiltered,
  not as "no Skills"), so it must never be expanded to the whole library
- professional members do not see each other's Skills
- the file is the authority: a weaker seed never overwrites a stronger grant
- FileSkillVisibilityProvider agrees with the composed sets and refreshes when
  the declaration changes on disk

Baseline: openjiuwen agent-core @ 6df9699e650b3ad9b9835b46ae3c3b7572adb04b
Run:      .venv/Scripts/python.exe p0a/skill_visibility_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from checks.harness import Checks  # noqa: E402
from openjiuwen.agent_teams.paths import (  # noqa: E402
    SKILL_VISIBILITY_FILENAME,
    member_skill_visibility_path,
    team_skill_visibility_path,
)
from openjiuwen.agent_teams.skill.visibility import (  # noqa: E402
    AUTHORITY_EXPLICIT,
    AUTHORITY_SEED,
    SCOPE_MEMBER,
    SCOPE_TEAM,
    FileSkillVisibilityProvider,
    bootstrap_skill_visibility,
    compose_skill_visibility,
    read_skill_visibility,
)

TEAM = "jiuwenswarm_mvp"
TEAM_ALLOW = ["common"]
RESEARCH_ALLOW = ["research", "fact_checking"]
ENGINEERING_ALLOW = ["software_engineering", "code_review"]
ENGINEERING_ONLY = {"software_engineering", "code_review"}
RESEARCH_ONLY = {"research", "fact_checking"}
GLOBAL_DISABLED = ["legacy_skill"]

RESULTS_PATH = Path(__file__).resolve().parent / "skill_visibility_results.json"


def run(checks: Checks) -> dict:
    # --- declaration-file layout -------------------------------------------
    member_path = member_skill_visibility_path(TEAM, "research")
    team_path = team_skill_visibility_path(TEAM)
    checks.add(
        "member declaration path follows the documented layout",
        member_path.name == SKILL_VISIBILITY_FILENAME
        and "research_workspace" in str(member_path)
        and TEAM in str(member_path),
        str(member_path),
    )
    checks.add(
        "team declaration path follows the documented layout",
        team_path.name == SKILL_VISIBILITY_FILENAME and "team-workspace" in str(team_path),
        str(team_path),
    )

    with tempfile.TemporaryDirectory(prefix="p0a-skillvis-") as tmp:
        root = Path(tmp)

        def bootstrap(scope: str, entity: str, allow: list[str], *, authority: int = AUTHORITY_SEED):
            path = root / f"{scope}-{entity}" / SKILL_VISIBILITY_FILENAME
            return path, bootstrap_skill_visibility(
                path,
                scope=scope,
                entity_id=entity,
                allow=allow,
                bootstrapped_from=f"config:p0a.{entity}",
                authority=authority,
            )

        team_file, _ = bootstrap(SCOPE_TEAM, TEAM, TEAM_ALLOW)
        research_file, _ = bootstrap(SCOPE_MEMBER, "research", RESEARCH_ALLOW)
        engineering_file, _ = bootstrap(SCOPE_MEMBER, "engineering", ENGINEERING_ALLOW)

        checks.add(
            "all three declarations are written and parse back",
            all(
                read_skill_visibility(path, scope=scope, entity_id=entity).allow
                for path, scope, entity in (
                    (team_file, SCOPE_TEAM, TEAM),
                    (research_file, SCOPE_MEMBER, "research"),
                    (engineering_file, SCOPE_MEMBER, "engineering"),
                )
            ),
            f"team={team_file.name} research={research_file.name} engineering={engineering_file.name}",
        )

        def effective(member: str, member_file: Path) -> tuple[set[str], set[str]]:
            member_doc = read_skill_visibility(member_file, scope=SCOPE_MEMBER, entity_id=member)
            team_doc = read_skill_visibility(team_file, scope=SCOPE_TEAM, entity_id=TEAM)
            return compose_skill_visibility(member_doc, team_doc, GLOBAL_DISABLED)

        research_enabled, research_disabled = effective("research", research_file)
        engineering_enabled, engineering_disabled = effective("engineering", engineering_file)

        # --- composition rules ---------------------------------------------
        checks.add(
            "enabled = member.allow UNION team.allow (research)",
            research_enabled == set(RESEARCH_ALLOW) | set(TEAM_ALLOW),
            f"enabled={sorted(research_enabled)}",
        )
        checks.add(
            "enabled = member.allow UNION team.allow (engineering)",
            engineering_enabled == set(ENGINEERING_ALLOW) | set(TEAM_ALLOW),
            f"enabled={sorted(engineering_enabled)}",
        )
        checks.add(
            "global_disabled lands in the disabled set",
            set(GLOBAL_DISABLED) <= research_disabled and set(GLOBAL_DISABLED) <= engineering_disabled,
            f"disabled={sorted(research_disabled)}",
        )
        checks.add(
            "professional members cannot see each other's Skills",
            not (research_enabled & ENGINEERING_ONLY) and not (engineering_enabled & RESEARCH_ONLY),
            f"research&engineering_only={sorted(research_enabled & ENGINEERING_ONLY)} "
            f"engineering&research_only={sorted(engineering_enabled & RESEARCH_ONLY)}",
        )
        checks.add(
            "every member keeps a non-empty effective allow-list (PROJECT.md section 9)",
            bool(research_enabled) and bool(engineering_enabled),
            f"research={len(research_enabled)} engineering={len(engineering_enabled)}",
        )

        # --- empty allow-list semantics -------------------------------------
        absent = read_skill_visibility(root / "absent.json", scope=SCOPE_MEMBER, entity_id="nobody")
        empty_enabled, _ = compose_skill_visibility(absent, None, None)
        checks.add(
            "an empty allow-list stays empty (means 'unfiltered', not 'no Skills')",
            empty_enabled == set(),
            f"enabled={sorted(empty_enabled)}",
        )

        # --- file is the authority ------------------------------------------
        # Uses a separate member id: seeding under "research" would rewrite the
        # very declaration the provider checks below read back.
        bootstrap(SCOPE_MEMBER, "probe_member", ["research"], authority=AUTHORITY_EXPLICIT)
        _, reseeded = bootstrap(
            SCOPE_MEMBER, "probe_member", ["research", "fact_checking"], authority=AUTHORITY_SEED
        )
        checks.add(
            "a weaker seed never overwrites a stronger grant",
            reseeded.allow == ["research"],
            f"allow after reseed={reseeded.allow}",
        )

        # --- provider end-to-end + refresh -----------------------------------
        provider = FileSkillVisibilityProvider(
            member_path=research_file,
            member_id="research",
            team_path=team_file,
            team_id=TEAM,
            global_disabled_loader=lambda: GLOBAL_DISABLED,
        )
        provider_enabled, provider_disabled = provider()
        checks.add(
            "FileSkillVisibilityProvider agrees with compose_skill_visibility",
            provider_enabled == research_enabled and provider_disabled == research_disabled,
            f"enabled={sorted(provider_enabled)}",
        )

        bootstrap_skill_visibility(
            research_file,
            scope=SCOPE_MEMBER,
            entity_id="research",
            allow=["research", "fact_checking", "extra_tool"],
            bootstrapped_from="config:p0a.research",
            authority=AUTHORITY_EXPLICIT,
        )
        # Force an mtime change so the provider's stat-based cache invalidates.
        stat = research_file.stat()
        os.utime(research_file, (stat.st_atime, stat.st_mtime + 1))
        refreshed_enabled, _ = provider()
        checks.add(
            "provider re-reads the declaration after an on-disk change",
            "extra_tool" in refreshed_enabled,
            f"enabled={sorted(refreshed_enabled)}",
        )

    return {
        "team_allow": TEAM_ALLOW,
        "research_allow": RESEARCH_ALLOW,
        "engineering_allow": ENGINEERING_ALLOW,
        "global_disabled": GLOBAL_DISABLED,
    }


def main() -> int:
    checks = Checks()
    declarations = run(checks)

    return checks.report(
        RESULTS_PATH,
        scope="offline Skill visibility declaration + composition; no Runner, no model call, no network",
        baseline="openjiuwen agent-core @ 6df9699e650b3ad9b9835b46ae3c3b7572adb04b",
        declarations=declarations,
        not_covered="Skill content version/hash and tool-permission enforcement are P0-C scope",
    )


if __name__ == "__main__":
    sys.exit(main())
