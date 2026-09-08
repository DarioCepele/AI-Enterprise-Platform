import pytest

from demo.tools.skill_tools import (
    SKILLS_DIR,
    build_skill_tools,
    list_skills,
    parse_skill,
)

SKILL_TEXT = """---
name: comparison
description: Confronta piu' elementi lungo dimensioni comuni.
---

# Confronto

Individua le dimensioni, poi chiama ui_table.
"""

def test_parse_skill_splits_frontmatter_from_body():
    parsed = parse_skill(SKILL_TEXT)

    assert parsed["name"] == "comparison"
    assert parsed["description"].startswith("Confronta")
    assert "chiama ui_table" in parsed["body"]
    assert "---" not in parsed["body"]

def test_parse_skill_rejects_a_file_without_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill("# Solo markdown\n")

def test_parse_skill_rejects_frontmatter_without_a_name():
    text = "---\ndescription: senza nome\n---\n\ncorpo\n"

    with pytest.raises(ValueError, match="name"):
        parse_skill(text)

def test_the_repo_ships_the_comparison_skill():
    names = [s["name"] for s in list_skills()]

    assert "comparison" in names

def test_every_shipped_skill_parses():

    for skill in list_skills():
        assert skill["name"]
        assert skill["description"]

def test_load_skill_returns_the_body_to_the_model(tmp_path):
    skill_dir = tmp_path / "esempio"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(root=tmp_path)

    content = load_skill.func(name="comparison")

    assert "chiama ui_table" in content.text

def test_load_skill_names_the_alternatives_when_it_fails(tmp_path):
    skill_dir = tmp_path / "esempio"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(root=tmp_path)

    content = load_skill.func(name="inesistente")

    assert "inesistente" in content.text
    assert "comparison" in content.text

def test_skills_dir_exists_in_the_package():
    assert SKILLS_DIR.is_dir()
