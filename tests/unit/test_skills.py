"""Tests for the CLI skills system."""

from __future__ import annotations

import tempfile
from pathlib import Path

from anteroom.cli.skills import (
    _BUILTIN_COMMANDS,
    MAX_PROMPT_SIZE,
    MAX_SKILLS,
    SkillRegistry,
    _expand_args,
    _load_skills_from_dir,
    _validate_skill_name,
    load_skills,
)


class TestLoadSkills:
    def test_load_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "greet.yaml").write_text(
                "name: greet\ndescription: Say hello\nprompt: Say hello to the user\n"
            )
            result = load_skills(tmpdir)
            assert len(result.skills) == 1
            assert result.skills[0].name == "greet"
            assert result.skills[0].description == "Say hello"

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_skills(tmpdir)
            assert len(result.skills) == 0

    def test_skip_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text("not: valid: yaml: [[[")
            result = load_skills(tmpdir)
            assert len(result.skills) == 0
            assert len(result.warnings) >= 1

    def test_skip_no_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "empty.yaml").write_text("name: empty\ndescription: No prompt\n")
            result = load_skills(tmpdir)
            assert len(result.skills) == 0
            assert any("missing 'prompt'" in w for w in result.warnings)


class TestValidateSkillName:
    def test_valid_name(self) -> None:
        name, warning = _validate_skill_name("commit", "commit")
        assert name == "commit"
        assert warning is None

    def test_valid_name_with_hyphens_underscores(self) -> None:
        name, warning = _validate_skill_name("my-skill_v2", "file")
        assert name == "my-skill_v2"
        assert warning is None

    def test_empty_name_defaults_to_stem(self) -> None:
        name, warning = _validate_skill_name("", "fallback")
        assert name == "fallback"
        assert warning is None

    def test_whitespace_name_defaults_to_stem(self) -> None:
        name, warning = _validate_skill_name("   ", "fallback")
        assert name == "fallback"
        assert warning is None

    def test_whitespace_trimmed(self) -> None:
        name, warning = _validate_skill_name("  test  ", "file")
        assert name == "test"
        assert warning is None

    def test_rejects_special_chars(self) -> None:
        name, warning = _validate_skill_name("test/bad", "file")
        assert name == ""
        assert warning is not None
        assert "invalid skill name" in warning

    def test_rejects_newline(self) -> None:
        name, warning = _validate_skill_name("test\nmalicious", "file")
        assert name == ""
        assert warning is not None

    def test_rejects_uppercase(self) -> None:
        name, warning = _validate_skill_name("MySkill", "file")
        assert name == ""
        assert warning is not None

    def test_rejects_starting_with_hyphen(self) -> None:
        name, warning = _validate_skill_name("-bad", "file")
        assert name == ""
        assert warning is not None


class TestExpandArgs:
    def test_placeholder_replacement(self) -> None:
        result = _expand_args("Process {args} now", "my input")
        assert result == "Process my input now"

    def test_multiple_placeholders(self) -> None:
        result = _expand_args("First: {args}, Second: {args}", "data")
        assert result == "First: data, Second: data"

    def test_fallback_append(self) -> None:
        result = _expand_args("Do something", "extra context")
        assert result == "Do something\n\nAdditional context: extra context"

    def test_empty_args_noop(self) -> None:
        prompt = "Do something"
        result = _expand_args(prompt, "")
        assert result == "Do something"

    def test_whitespace_only_args_noop(self) -> None:
        prompt = "Do something"
        result = _expand_args(prompt, "   ")
        assert result == "Do something"


class TestSkillRegistry:
    def _make_registry(self, tmpdir: str) -> SkillRegistry:
        skills_dir = Path(tmpdir) / ".anteroom" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "commit.yaml").write_text(
            "name: commit\ndescription: Commit changes\nprompt: Make a git commit\n"
        )
        (skills_dir / "review.yaml").write_text("name: review\ndescription: Review code\nprompt: Review the code\n")
        reg = SkillRegistry()
        reg.load(tmpdir)
        return reg

    def test_has_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            assert reg.has_skill("commit")
            assert reg.has_skill("review")
            assert not reg.has_skill("nonexistent")

    def test_get_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            skill = reg.get("commit")
            assert skill is not None
            assert skill.name == "commit"
            assert skill.prompt == "Make a git commit"

    def test_list_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            skills = reg.list_skills()
            names = [s.name for s in skills]
            assert "commit" in names
            assert "review" in names

    def test_resolve_input_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/commit")
            assert is_skill
            assert "git commit" in prompt.lower()

    def test_resolve_input_skill_with_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/commit fix the bug")
            assert is_skill
            assert "fix the bug" in prompt

    def test_resolve_input_args_interpolation(self) -> None:
        """When prompt contains {args}, it should be replaced inline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "greet.yaml").write_text(
                "name: greet\ndescription: Greet\nprompt: |\n  Hello {args}, welcome!\n"
            )
            reg = SkillRegistry()
            reg.load(tmpdir)
            is_skill, prompt = reg.resolve_input("/greet world")
            assert is_skill
            assert "Hello world, welcome!" in prompt
            assert "Additional context" not in prompt

    def test_resolve_input_args_fallback_append(self) -> None:
        """When prompt has no {args}, args are appended as Additional context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/commit fix it")
            assert is_skill
            assert "Additional context: fix it" in prompt

    def test_resolve_input_not_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/unknown_thing")
            assert not is_skill
            assert prompt == "/unknown_thing"

    def test_resolve_non_slash_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("hello world")
            assert not is_skill

    def test_resolve_input_slash_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/")
            assert not is_skill
            assert prompt == "/"

    def test_resolve_input_slash_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/ ")
            assert not is_skill

    def test_default_skills_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            skills = reg.list_skills()
            names = [s.name for s in skills]
            assert "commit" not in names
            assert "review" not in names
            assert "explain" not in names
            assert "artifact-check" not in names
            assert "a-help" in names
            assert "new-skill" in names

    def test_load_warnings_for_invalid_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text("not: valid: yaml: [[[")
            (skills_dir / "noprompt.yaml").write_text("name: noprompt\ndescription: No prompt\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert len(reg.load_warnings) >= 2
            warning_text = " ".join(reg.load_warnings)
            assert "bad.yaml" in warning_text
            assert "noprompt.yaml" in warning_text

    def test_load_warnings_include_yaml_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "broken.yaml").write_text("name: broken\nprompt: value: bad: here\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            yaml_warnings = [w for w in reg.load_warnings if "broken.yaml" in w]
            assert len(yaml_warnings) >= 1
            assert "line" in yaml_warnings[0].lower()

    def test_no_warnings_for_valid_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "good.yaml").write_text("name: good\ndescription: Works\nprompt: Do something\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            # Filter out collision warnings (user 'good' doesn't collide with any default)
            assert len(reg.load_warnings) == 0

    def test_load_warnings_collision_info(self) -> None:
        """When a user skill overrides a built-in, a warning is emitted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            content = "name: create-eval\ndescription: My eval\nprompt: Custom eval\n"
            (skills_dir / "create-eval.yaml").write_text(content)
            reg = SkillRegistry()
            reg.load(tmpdir)
            collision_warnings = [w for w in reg.load_warnings if "overrides" in w]
            assert len(collision_warnings) == 1
            assert "create-eval" in collision_warnings[0]

    def test_empty_skill_name_defaults_to_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "deploy.yaml").write_text('name: ""\ndescription: Deploy\nprompt: Deploy now\n')
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("deploy")

    def test_skill_name_whitespace_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "test.yaml").write_text('name: "  test  "\ndescription: Test\nprompt: Run tests\n')
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("test")
            assert not reg.has_skill("  test  ")

    def test_skill_name_rejects_special_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text('name: "test/bad"\ndescription: Bad\nprompt: Do bad things\n')
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert not reg.has_skill("test/bad")
            assert any("invalid skill name" in w for w in reg.load_warnings)

    def test_duplicate_skill_names_last_wins(self) -> None:
        """Project skills override global skills with the same name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "commit.yaml").write_text(
                "name: commit\ndescription: Custom commit\nprompt: My custom commit\n"
            )
            reg = SkillRegistry()
            reg.load(tmpdir)
            skill = reg.get("commit")
            assert skill is not None
            assert skill.prompt == "My custom commit"

    def test_max_skills_limit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            for i in range(MAX_SKILLS + 10):
                (skills_dir / f"skill{i:04d}.yaml").write_text(
                    f"name: skill{i:04d}\ndescription: Skill {i}\nprompt: Do thing {i}\n"
                )
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert any("limit" in w.lower() for w in reg.load_warnings)

    def test_reload_picks_up_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "first.yaml").write_text("name: first\ndescription: First\nprompt: First skill\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("first")
            assert not reg.has_skill("second")

            (skills_dir / "second.yaml").write_text("name: second\ndescription: Second\nprompt: Second skill\n")
            reg.reload(tmpdir)
            assert reg.has_skill("first")
            assert reg.has_skill("second")

    def test_reload_clears_old_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "temp.yaml").write_text("name: temp\ndescription: Temp\nprompt: Temp skill\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("temp")

            (skills_dir / "temp.yaml").unlink()
            reg.reload(tmpdir)
            assert not reg.has_skill("temp")

    def test_yaml_mapping_values_error_hint(self) -> None:
        """YAML files with unquoted colons produce helpful error hints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            # Unquoted colons in description + bad indentation on prompt triggers
            # "mapping values are not allowed here"
            (skills_dir / "broken.yaml").write_text(
                "description: Start work on a story: read it\n prompt: |\n  Do something\n"
            )
            reg = SkillRegistry()
            reg.load(tmpdir)
            yaml_warnings = [w for w in reg.load_warnings if "broken.yaml" in w]
            assert len(yaml_warnings) >= 1
            assert "mapping values" in yaml_warnings[0].lower() or "line" in yaml_warnings[0].lower()
            assert "hint" in yaml_warnings[0].lower()

    def test_get_skill_descriptions_returns_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            descs = reg.get_skill_descriptions()
            names = [name for name, _ in descs]
            assert "commit" in names  # from user-level fixture, not default
            assert "review" in names  # from user-level fixture, not default
            for name, desc in descs:
                assert isinstance(name, str)
                assert isinstance(desc, str)

    def test_get_skill_descriptions_empty_registry(self) -> None:
        reg = SkillRegistry()
        assert reg.get_skill_descriptions() == []

    def test_get_invoke_skill_definition_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            defn = reg.get_invoke_skill_definition()
            assert defn is not None
            assert defn["type"] == "function"
            func = defn["function"]
            assert func["name"] == "invoke_skill"
            params = func["parameters"]
            assert "skill_name" in params["properties"]
            assert "args" in params["properties"]
            assert params["properties"]["skill_name"]["type"] == "string"

    def test_get_invoke_skill_definition_enum_matches_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            defn = reg.get_invoke_skill_definition()
            assert defn is not None
            enum_values = defn["function"]["parameters"]["properties"]["skill_name"]["enum"]
            skill_names = [s.name for s in reg.list_skills()]
            assert sorted(enum_values) == sorted(skill_names)

    def test_get_invoke_skill_definition_empty_registry(self) -> None:
        reg = SkillRegistry()
        assert reg.get_invoke_skill_definition() is None

    def test_invalid_format_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "scalar.yaml").write_text("just a string\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert any("invalid format" in w for w in reg.load_warnings)

    def test_case_insensitive_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            assert reg.get("COMMIT") is not None
            assert reg.get("Commit") is not None
            assert reg.get("commit") is not None

    def test_case_insensitive_has_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            assert reg.has_skill("COMMIT")
            assert reg.has_skill("Review")

    def test_case_insensitive_resolve_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            is_skill, prompt = reg.resolve_input("/COMMIT")
            assert is_skill
            assert "git commit" in prompt.lower()

    def test_load_returns_sorted_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            skills = reg.load(tmpdir)
            names = [s.name for s in skills]
            assert names == sorted(names)

    def test_empty_file_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "empty.yaml").write_text("")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert any("empty file" in w for w in reg.load_warnings)

    def test_non_string_name_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text("name: 123\nprompt: do something\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert any("'name' must be a string" in w for w in reg.load_warnings)

    def test_non_string_prompt_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text("name: bad\nprompt:\n  - step1\n  - step2\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert any("'prompt' must be a string" in w for w in reg.load_warnings)

    def test_prompt_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            big_prompt = "x" * (MAX_PROMPT_SIZE + 1)
            (skills_dir / "huge.yaml").write_text(f"name: huge\nprompt: |\n  {big_prompt}\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert not reg.has_skill("huge")
            assert any("exceeds" in w for w in reg.load_warnings)

    def test_missing_description_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "nodesc.yaml").write_text("name: nodesc\nprompt: do something\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("nodesc")
            assert any("no description" in w for w in reg.load_warnings)


class TestBuiltinCommandBlocklist:
    def test_builtin_commands_rejected(self) -> None:
        for cmd in ["quit", "exit", "help", "skills", "tools"]:
            name, warning = _validate_skill_name(cmd, cmd)
            assert name == "", f"Built-in command '{cmd}' should be rejected"
            assert warning is not None
            assert "conflicts" in warning

    def test_non_builtin_accepted(self) -> None:
        for name in ["commit", "deploy", "review"]:
            result_name, warning = _validate_skill_name(name, name)
            assert result_name == name
            assert warning is None

    def test_builtin_set_is_nonempty(self) -> None:
        assert len(_BUILTIN_COMMANDS) > 20


class TestExpandArgsCodeFences:
    def test_placeholder_inside_code_fence_not_replaced(self) -> None:
        prompt = "Do this:\n```\necho {args}\n```\n"
        result = _expand_args(prompt, "hello")
        assert "echo {args}" in result
        assert "Additional context: hello" in result

    def test_placeholder_outside_code_fence_replaced(self) -> None:
        prompt = "Process {args} now.\n```\necho {args}\n```\n"
        result = _expand_args(prompt, "data")
        assert "Process data now." in result
        assert "echo {args}" in result
        assert "Additional context" not in result

    def test_multiple_code_fences(self) -> None:
        prompt = "A {args} B\n```\n{args}\n```\nC {args} D\n```\n{args}\n```\n"
        result = _expand_args(prompt, "X")
        assert "A X B" in result
        assert "C X D" in result
        # Code blocks should be untouched
        code_count = result.count("{args}")
        assert code_count == 2

    def test_no_placeholder_anywhere_appends(self) -> None:
        prompt = "Do something useful.\n```\ncode here\n```\n"
        result = _expand_args(prompt, "context")
        assert "Additional context: context" in result

    def test_only_code_fence_placeholder_treated_as_no_placeholder(self) -> None:
        prompt = "Instructions:\n```yaml\nprompt: Analyze {args}\n```\n"
        result = _expand_args(prompt, "my data")
        assert "{args}" in result
        assert "Additional context: my data" in result


class TestSkillDirs:
    def test_collects_all_matching_dirs_at_first_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".anteroom" / "skills").mkdir(parents=True)
            (Path(tmpdir) / ".claude" / "skills").mkdir(parents=True)
            from anteroom.cli.skills import _skill_dirs

            dirs = _skill_dirs(tmpdir)
            dir_strs = [str(d) for d in dirs]
            assert any(".anteroom" in d for d in dir_strs)
            assert any(".claude" in d for d in dir_strs)

    def test_parlor_dir_also_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".parlor" / "skills").mkdir(parents=True)
            from anteroom.cli.skills import _skill_dirs

            dirs = _skill_dirs(tmpdir)
            dir_strs = [str(d) for d in dirs]
            assert any(".parlor" in d for d in dir_strs)


class TestYmlExtension:
    """Tests for .yml file support."""

    def test_yml_files_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "greet.yml").write_text("name: greet\nprompt: Hello\n")
            result = _load_skills_from_dir(skills_dir, "project")
            assert len(result.skills) == 1
            assert result.skills[0].name == "greet"

    def test_yaml_and_yml_both_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "alpha.yaml").write_text("name: alpha\nprompt: A\n")
            (skills_dir / "beta.yml").write_text("name: beta\nprompt: B\n")
            result = _load_skills_from_dir(skills_dir, "project")
            names = {s.name for s in result.skills}
            assert names == {"alpha", "beta"}

    def test_yml_via_load_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "deploy.yml").write_text("name: deploy\nprompt: Ship it\n")
            result = load_skills(tmpdir)
            names = {s.name for s in result.skills}
            assert "deploy" in names

    def test_yml_via_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "deploy.yml").write_text("name: deploy\nprompt: Ship it\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("deploy")


class TestDefaultPackSkills:
    """Tests for pack-related built-in skills."""

    def test_new_pack_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("new-pack")
            skill = reg.get("new-pack")
            assert skill is not None
            assert "pack" in skill.description.lower()

    def test_pack_create_no_longer_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert not reg.has_skill("pack-create")

    def test_pack_lint_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("pack-lint")
            skill = reg.get("pack-lint")
            assert skill is not None
            assert "validate" in skill.description.lower() or "lint" in skill.description.lower()

    def test_pack_publish_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("pack-publish")
            skill = reg.get("pack-publish")
            assert skill is not None
            assert "git" in skill.description.lower() or "shar" in skill.description.lower()

    def test_pack_doctor_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("pack-doctor")
            skill = reg.get("pack-doctor")
            assert skill is not None
            assert "diagnos" in skill.description.lower() or "doctor" in skill.description.lower()

    def test_pack_update_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert reg.has_skill("pack-update")
            skill = reg.get("pack-update")
            assert skill is not None
            assert "update" in skill.description.lower() or "pull" in skill.description.lower()

    def test_pack_skill_names_not_reserved(self) -> None:
        """Pack skill names should not conflict with reserved command names."""
        for name in ["new-pack", "pack-lint", "pack-publish", "pack-doctor", "pack-update"]:
            result_name, warning = _validate_skill_name(name, name)
            assert result_name == name, f"Skill name '{name}' should be valid"
            assert warning is None, f"Skill name '{name}' should not produce a warning"

    def test_all_default_skills_count(self) -> None:
        """Verify the expected number of built-in default skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            names = [s.name for s in reg.list_skills()]
            expected = {
                "create-eval",
                "new-pack",
                "new-skill",
                "pack-lint",
                "pack-publish",
                "pack-doctor",
                "pack-update",
                "a-help",
                "new-space",
                "space-doctor",
                "space-lint",
                "space-setup",
                "space-edit",
            }
            for skill_name in expected:
                assert skill_name in names, f"Expected default skill '{skill_name}' not found"


class TestSearchedDirs:
    """Tests for searched directory diagnostic output."""

    def test_searched_dirs_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            (skills_dir / "greet.yaml").write_text("name: greet\nprompt: Hi\n")
            result = _load_skills_from_dir(skills_dir, "project")
            assert len(result.searched_dirs) == 1
            assert result.searched_dirs[0].exists is True
            assert result.searched_dirs[0].skill_count == 1
            assert result.searched_dirs[0].source == "project"

    def test_searched_dirs_nonexistent(self) -> None:
        result = _load_skills_from_dir(Path("/nonexistent/path"), "global")
        assert len(result.searched_dirs) == 1
        assert result.searched_dirs[0].exists is False
        assert result.searched_dirs[0].skill_count == 0

    def test_searched_dirs_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _load_skills_from_dir(Path(tmpdir), "project")
            assert len(result.searched_dirs) == 1
            assert result.searched_dirs[0].exists is True
            assert result.searched_dirs[0].skill_count == 0

    def test_load_skills_propagates_searched_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "test.yaml").write_text("name: test-skill\nprompt: Test\n")
            result = load_skills(tmpdir)
            # Should have global dir + project dir
            assert len(result.searched_dirs) >= 2
            sources = {sd.source for sd in result.searched_dirs}
            assert "global" in sources
            assert "project" in sources

    def test_registry_searched_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "test.yaml").write_text("name: test-skill\nprompt: Test\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            # Should have default + global + project
            assert len(reg.searched_dirs) >= 3
            sources = {sd.source for sd in reg.searched_dirs}
            assert "default" in sources
            assert "global" in sources
            assert "project" in sources
