"""Tests for anteroom.services.rule_enforcer — hard rule enforcement at tool layer."""

from __future__ import annotations

from anteroom.services.artifacts import Artifact, ArtifactSource, ArtifactType
from anteroom.services.rule_enforcer import (
    ParsedRule,
    RuleEnforcer,
    RuleMatch,
    check_rule,
    parse_rule,
)


def _make_rule_artifact(
    name: str = "no-force-push",
    enforce: str = "hard",
    matches: list[dict] | None = None,
    reason: str = "Policy violation",
    content: str = "Do not force push",
) -> Artifact:
    if matches is None:
        matches = [{"tool": "bash", "pattern": r"git\s+push\s+--force"}]
    return Artifact(
        fqn=f"@test/rule/{name}",
        type=ArtifactType.RULE,
        namespace="test",
        name=name,
        content=content,
        source=ArtifactSource.TEAM,
        metadata={"enforce": enforce, "matches": matches, "reason": reason},
    )


class TestParseRule:
    def test_hard_rule_parsed(self) -> None:
        art = _make_rule_artifact()
        result = parse_rule(art)
        assert result is not None
        assert result.fqn == "@test/rule/no-force-push"
        assert result.reason == "Policy violation"
        assert len(result.matches) == 1

    def test_soft_rule_returns_none(self) -> None:
        art = _make_rule_artifact(enforce="soft")
        assert parse_rule(art) is None

    def test_no_enforce_returns_none(self) -> None:
        art = Artifact(
            fqn="@test/rule/no-enforce",
            type=ArtifactType.RULE,
            namespace="test",
            name="no-enforce",
            content="Just a guideline",
            source=ArtifactSource.LOCAL,
            metadata={},
        )
        assert parse_rule(art) is None

    def test_non_rule_artifact_returns_none(self) -> None:
        art = Artifact(
            fqn="@test/skill/my-skill",
            type=ArtifactType.SKILL,
            namespace="test",
            name="my-skill",
            content="skill content",
            source=ArtifactSource.LOCAL,
            metadata={"enforce": "hard", "matches": [{"tool": "bash", "pattern": ".*"}]},
        )
        assert parse_rule(art) is None

    def test_empty_matches_returns_none(self) -> None:
        art = _make_rule_artifact(matches=[])
        assert parse_rule(art) is None

    def test_invalid_regex_skipped(self) -> None:
        art = _make_rule_artifact(matches=[{"tool": "bash", "pattern": "[invalid"}])
        assert parse_rule(art) is None

    def test_multiple_matches(self) -> None:
        art = _make_rule_artifact(
            matches=[
                {"tool": "bash", "pattern": r"git\s+push\s+--force"},
                {"tool": "write_file", "pattern": r"\.env$"},
            ]
        )
        result = parse_rule(art)
        assert result is not None
        assert len(result.matches) == 2

    def test_wildcard_tool(self) -> None:
        art = _make_rule_artifact(matches=[{"tool": "*", "pattern": "secret"}])
        result = parse_rule(art)
        assert result is not None
        assert result.matches[0].tool == "*"

    def test_reason_fallback_to_content(self) -> None:
        art = Artifact(
            fqn="@test/rule/fallback",
            type=ArtifactType.RULE,
            namespace="test",
            name="fallback",
            content="Never do this dangerous thing",
            source=ArtifactSource.LOCAL,
            metadata={"enforce": "hard", "matches": [{"tool": "bash", "pattern": "danger"}]},
        )
        result = parse_rule(art)
        assert result is not None
        assert "Never do this dangerous thing" in result.reason


class TestCheckRule:
    def test_bash_command_matches(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no force push",
            matches=(RuleMatch(tool="bash", pattern=re.compile(r"git\s+push\s+--force")),),
        )
        assert check_rule(rule, "bash", {"command": "git push --force origin main"})

    def test_bash_command_no_match(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no force push",
            matches=(RuleMatch(tool="bash", pattern=re.compile(r"git\s+push\s+--force")),),
        )
        assert not check_rule(rule, "bash", {"command": "git push origin main"})

    def test_wrong_tool_no_match(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no force push",
            matches=(RuleMatch(tool="bash", pattern=re.compile(r"git\s+push\s+--force")),),
        )
        assert not check_rule(rule, "write_file", {"path": "git push --force"})

    def test_wildcard_tool_matches_any(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no secrets",
            matches=(RuleMatch(tool="*", pattern=re.compile(r"secret")),),
        )
        assert check_rule(rule, "bash", {"command": "echo secret"})
        assert check_rule(rule, "write_file", {"path": "/tmp/secret.txt"})

    def test_write_file_path_match(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no .env writes",
            matches=(RuleMatch(tool="write_file", pattern=re.compile(r"\.env$")),),
        )
        assert check_rule(rule, "write_file", {"path": "/app/.env"})
        assert not check_rule(rule, "write_file", {"path": "/app/config.yaml"})


class TestRuleEnforcer:
    def test_no_rules_no_block(self) -> None:
        enforcer = RuleEnforcer()
        blocked, reason, fqn = enforcer.check_tool_call("bash", {"command": "rm -rf /"})
        assert not blocked

    def test_hard_rule_blocks(self) -> None:
        enforcer = RuleEnforcer()
        art = _make_rule_artifact()
        enforcer.load_rules([art])
        assert enforcer.rule_count == 1
        blocked, reason, fqn = enforcer.check_tool_call("bash", {"command": "git push --force main"})
        assert blocked
        assert "Policy violation" in reason
        assert fqn == "@test/rule/no-force-push"

    def test_non_matching_call_allowed(self) -> None:
        enforcer = RuleEnforcer()
        art = _make_rule_artifact()
        enforcer.load_rules([art])
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git status"})
        assert not blocked

    def test_soft_rules_ignored(self) -> None:
        enforcer = RuleEnforcer()
        soft = _make_rule_artifact(enforce="soft")
        enforcer.load_rules([soft])
        assert enforcer.rule_count == 0

    def test_reload_replaces_rules(self) -> None:
        enforcer = RuleEnforcer()
        art1 = _make_rule_artifact(name="rule1")
        enforcer.load_rules([art1])
        assert enforcer.rule_count == 1

        art2 = _make_rule_artifact(name="rule2", matches=[{"tool": "bash", "pattern": "rm -rf"}])
        enforcer.load_rules([art2])
        assert enforcer.rule_count == 1
        # Old rule no longer applies
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force"})
        assert not blocked
        # New rule applies
        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "rm -rf /"})
        assert blocked

    def test_multiple_rules(self) -> None:
        enforcer = RuleEnforcer()
        r1 = _make_rule_artifact(name="no-force-push")
        r2 = _make_rule_artifact(
            name="no-env-write",
            matches=[{"tool": "write_file", "pattern": r"\.env$"}],
            reason="No .env writes",
        )
        enforcer.load_rules([r1, r2])
        assert enforcer.rule_count == 2

        blocked, _, _ = enforcer.check_tool_call("bash", {"command": "git push --force"})
        assert blocked
        blocked, _, _ = enforcer.check_tool_call("write_file", {"path": "/app/.env"})
        assert blocked
        blocked, _, _ = enforcer.check_tool_call("read_file", {"path": "/app/.env"})
        assert not blocked
