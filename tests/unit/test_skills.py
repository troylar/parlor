"""Tests for the CLI skills system."""

from __future__ import annotations

import tempfile
from pathlib import Path

from anteroom.cli.skills import SkillRegistry, load_skills


class TestLoadSkills:
    def test_load_from_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "greet.yaml").write_text(
                "name: greet\ndescription: Say hello\nprompt: Say hello to the user\n"
            )
            skills = load_skills(tmpdir)
            assert len(skills) == 1
            assert skills[0].name == "greet"
            assert skills[0].description == "Say hello"

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = load_skills(tmpdir)
            assert len(skills) == 0

    def test_skip_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "bad.yaml").write_text("not: valid: yaml: [[[")
            skills = load_skills(tmpdir)
            # Should not crash, just skip
            assert len(skills) == 0

    def test_skip_no_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "empty.yaml").write_text("name: empty\ndescription: No prompt\n")
            skills = load_skills(tmpdir)
            assert len(skills) == 0


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
            # Should include both user skills and defaults
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

    def test_default_skills_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = SkillRegistry()
            reg.load(tmpdir)
            # Default skills should be loaded
            skills = reg.list_skills()
            names = [s.name for s in skills]
            assert "commit" in names
            assert "review" in names
            assert "explain" in names
            assert "docs" in names

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

    def test_no_warnings_for_valid_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / ".anteroom" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "good.yaml").write_text("name: good\ndescription: Works\nprompt: Do something\n")
            reg = SkillRegistry()
            reg.load(tmpdir)
            assert len(reg.load_warnings) == 0

    def test_get_skill_descriptions_returns_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = self._make_registry(tmpdir)
            descs = reg.get_skill_descriptions()
            names = [name for name, _ in descs]
            # Includes user skills and default skills
            assert "commit" in names
            assert "review" in names
            # Each entry is (name, description)
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
