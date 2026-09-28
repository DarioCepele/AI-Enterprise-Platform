import pytest

from master_agent.tools.skill_tools import (
    SKILLS_DIR,
    build_skill_tools,
    list_skills,
    parse_skill,
)

SKILL_TEXT = """---
name: comparison
description: Compares several items along common dimensions.
---

# Comparison

Identify the dimensions, then call ui_table.
"""


def test_parse_skill_splits_frontmatter_from_body():
    parsed = parse_skill(SKILL_TEXT)

    assert parsed["name"] == "comparison"
    assert parsed["description"].startswith("Compares")
    assert "call ui_table" in parsed["body"]
    assert "---" not in parsed["body"]


def test_parse_skill_rejects_a_file_without_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill("# Only markdown\n")


def test_parse_skill_rejects_frontmatter_without_a_name():
    text = "---\ndescription: without a name\n---\n\nbody\n"

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
    skill_dir = tmp_path / "example"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(tmp_path)

    content = load_skill.func(name="comparison")

    assert "call ui_table" in content.text


def test_load_skill_names_the_alternatives_when_it_fails(tmp_path):
    skill_dir = tmp_path / "example"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    (load_skill,) = build_skill_tools(tmp_path)

    content = load_skill.func(name="missing")

    assert "missing" in content.text
    assert "comparison" in content.text


def test_skills_dir_exists_in_the_package():
    assert SKILLS_DIR.is_dir()


def test_the_frontmatter_is_yaml_not_a_line_format(tmp_path):
    folder = tmp_path / "reporting"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\n"
        "name: reporting\n"
        "description: >\n"
        "  Writes a report,\n"
        "  folded over two lines.\n"
        "license: Apache-2.0\n"
        "metadata:\n"
        "  owner: finance\n"
        "---\n\n# Reporting\n\nWrite it.\n",
        encoding="utf-8",
    )

    [skill] = list_skills(tmp_path)

    assert skill["description"] == "Writes a report, folded over two lines."
    assert skill["metadata"] == {"owner": "finance"}
    assert skill["license"] == "Apache-2.0"


def test_a_fork_adds_its_own_folder_of_skills(tmp_path):
    folder = tmp_path / "pricing"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: pricing\ndescription: Prices things.\n---\n\nbody\n",
        encoding="utf-8",
    )

    names = [s["name"] for s in list_skills([SKILLS_DIR, tmp_path])]

    assert names == sorted(["comparison", "pricing"])


def test_the_same_skill_twice_is_an_error_not_a_silent_winner(tmp_path):
    folder = tmp_path / "comparison"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: comparison\ndescription: Again.\n---\n\nbody\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="defined twice"):
        list_skills([SKILLS_DIR, tmp_path])
