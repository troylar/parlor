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
                {"tool": "write_file", "pattern": r"\.env(\n|$)"},
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

    def test_reason_fallback_whitespace_only_content(self) -> None:
        """Whitespace-only content should fall back to fqn for reason."""
        art = Artifact(
            fqn="@test/rule/whitespace",
            type=ArtifactType.RULE,
            namespace="test",
            name="whitespace",
            content="   \n  ",
            source=ArtifactSource.LOCAL,
            metadata={"enforce": "hard", "matches": [{"tool": "bash", "pattern": "danger"}]},
        )
        result = parse_rule(art)
        assert result is not None
        assert result.reason == "@test/rule/whitespace"

    def test_reason_empty_string_in_metadata_uses_fallback(self) -> None:
        """Empty reason string in metadata should fall through to content/fqn fallback."""
        art = Artifact(
            fqn="@test/rule/empty-reason",
            type=ArtifactType.RULE,
            namespace="test",
            name="empty-reason",
            content="Block dangerous commands",
            source=ArtifactSource.LOCAL,
            metadata={"enforce": "hard", "matches": [{"tool": "bash", "pattern": "danger"}], "reason": ""},
        )
        result = parse_rule(art)
        assert result is not None
        assert result.reason == "Block dangerous commands"


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
            matches=(RuleMatch(tool="write_file", pattern=re.compile(r"\.env(\n|$)")),),
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
            matches=[{"tool": "write_file", "pattern": r"\.env(\n|$)"}],
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


class TestRuleEnforcerEdgeCases:
    def test_empty_metadata_logs_warning(self) -> None:
        """Rule with empty metadata should be treated as soft and log a warning."""
        art = Artifact(
            fqn="@test/rule/empty-meta",
            type=ArtifactType.RULE,
            namespace="test",
            name="empty-meta",
            content="Do not do this",
            source=ArtifactSource.LOCAL,
            metadata={},
        )
        assert parse_rule(art) is None

    def test_partial_bad_regex_keeps_good_matches(self) -> None:
        """A rule with some valid and some invalid match patterns keeps the valid ones."""
        art = _make_rule_artifact(
            matches=[
                {"tool": "bash", "pattern": r"git\s+push\s+--force"},
                {"tool": "bash", "pattern": "[invalid"},  # bad regex
            ]
        )
        result = parse_rule(art)
        assert result is not None
        assert len(result.matches) == 1  # only the valid one
        assert result.matches[0].pattern.pattern == r"git\s+push\s+--force"

    def test_all_bad_regex_returns_none(self) -> None:
        art = _make_rule_artifact(matches=[{"tool": "bash", "pattern": "[bad"}])
        assert parse_rule(art) is None

    def test_pattern_too_long_skipped(self) -> None:
        long_pattern = "a" * 501
        art = _make_rule_artifact(
            matches=[
                {"tool": "bash", "pattern": long_pattern},
                {"tool": "bash", "pattern": "danger"},
            ]
        )
        result = parse_rule(art)
        assert result is not None
        assert len(result.matches) == 1  # long one skipped

    def test_match_entry_not_dict_skipped(self) -> None:
        art = _make_rule_artifact(
            matches=[
                "not a dict",  # type: ignore[list-item]
                {"tool": "bash", "pattern": "danger"},
            ]
        )
        result = parse_rule(art)
        assert result is not None
        assert len(result.matches) == 1


class TestStringifyWriteFileContent:
    """Verify _stringify_arguments includes file content for write/edit tools."""

    def test_write_file_includes_content(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no eval",
            matches=(RuleMatch(tool="write_file", pattern=re.compile(r"\beval\s*\(")),),
        )
        assert check_rule(rule, "write_file", {"path": "app.py", "content": "result = eval(user_input)"})

    def test_write_file_content_no_match(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no eval",
            matches=(RuleMatch(tool="write_file", pattern=re.compile(r"\beval\s*\(")),),
        )
        assert not check_rule(rule, "write_file", {"path": "app.py", "content": "result = safe_parse(data)"})

    def test_edit_file_includes_new_text(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no sql concat",
            matches=(RuleMatch(tool="edit_file", pattern=re.compile(r'\.execute\s*\(\s*f["\']')),),
        )
        assert check_rule(
            rule,
            "edit_file",
            {"path": "db.py", "old_text": "safe()", "new_text": 'conn.execute(f"SELECT * FROM {table}")'},
        )

    def test_edit_file_old_text_not_matched(self) -> None:
        """Only new_text is checked, not old_text."""
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no eval",
            matches=(RuleMatch(tool="edit_file", pattern=re.compile(r"\beval\s*\(")),),
        )
        # old_text has eval but new_text does not — should NOT block
        assert not check_rule(
            rule,
            "edit_file",
            {"path": "app.py", "old_text": "eval(x)", "new_text": "safe_parse(x)"},
        )

    def test_write_file_path_still_matches(self) -> None:
        """Path-matching patterns work with path\\ncontent format."""
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no .env writes",
            matches=(RuleMatch(tool="write_file", pattern=re.compile(r"\.env(\n|$)")),),
        )
        assert check_rule(rule, "write_file", {"path": "/app/.env", "content": "API_KEY=abc"})
        # Also works without content
        assert check_rule(rule, "write_file", {"path": "/app/.env"})

    def test_write_file_hardcoded_secret_in_content(self) -> None:
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="no hardcoded secrets",
            matches=(
                RuleMatch(
                    tool="write_file",
                    pattern=re.compile(r'(api_key|secret_key|password|token)\s*=\s*["\'][^"\']{8,}'),
                ),
            ),
        )
        assert check_rule(
            rule,
            "write_file",
            {"path": "config.py", "content": 'api_key = "supersecret123"'},
        )

    def test_read_file_still_path_only(self) -> None:
        """read_file should only match against path, not other args."""
        import re

        rule = ParsedRule(
            fqn="@test/rule/r",
            reason="test",
            matches=(RuleMatch(tool="read_file", pattern=re.compile(r"secret")),),
        )
        assert check_rule(rule, "read_file", {"path": "/tmp/secret.txt"})
        assert not check_rule(rule, "read_file", {"path": "/tmp/safe.txt"})


class TestYamlLoadRulePattern:
    """Verify the yaml.load rule blocks unsafe loaders but allows SafeLoader."""

    def _make_yaml_rule(self) -> ParsedRule:
        import re

        return ParsedRule(
            fqn="@test/rule/no-eval",
            reason="no unsafe yaml.load",
            matches=(RuleMatch(tool="bash", pattern=re.compile(r"yaml\.load\s*\((?!.*SafeLoader)")),),
        )

    def test_blocks_no_loader(self) -> None:
        rule = self._make_yaml_rule()
        assert check_rule(rule, "bash", {"command": "python -c 'yaml.load(data)'"})

    def test_blocks_unsafe_loader(self) -> None:
        rule = self._make_yaml_rule()
        assert check_rule(rule, "bash", {"command": "python -c 'yaml.load(data, Loader=yaml.Loader)'"})

    def test_blocks_unsafeloader(self) -> None:
        rule = self._make_yaml_rule()
        assert check_rule(rule, "bash", {"command": "python -c 'yaml.load(data, Loader=UnsafeLoader)'"})

    def test_allows_safe_loader(self) -> None:
        rule = self._make_yaml_rule()
        assert not check_rule(rule, "bash", {"command": "python -c 'yaml.load(data, Loader=yaml.SafeLoader)'"})

    def test_allows_safe_load_function(self) -> None:
        """yaml.safe_load() is a different function and should not be matched."""
        rule = self._make_yaml_rule()
        assert not check_rule(rule, "bash", {"command": "python -c 'yaml.safe_load(data)'"})
