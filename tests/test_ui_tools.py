import json

from demo.tools.ui_tools import DISPLAY_KEY, STATE_KEY, get_tools, ui_table

def test_get_tools_exposes_ui_table():
    names = [t.name for t in get_tools()]
    assert "ui_table" in names

def test_ui_table_builds_a_ui_payload():

    content = ui_table.func(
        title="Comparison",
        columns=["Topic", "A", "B"],
        rows=[["Coverage", "empty", "full"]],
    )

    payload = json.loads(content.additional_properties[DISPLAY_KEY])
    assert payload["component"] == "ui-table"
    assert payload["title"] == "Comparison"
    assert payload["columns"] == ["Topic", "A", "B"]
    assert payload["rows"] == [["Coverage", "empty", "full"]]

def test_ui_table_merges_into_shared_state():
    content = ui_table.func(title="Comparison", columns=["A"], rows=[["1"]])

    state = content.additional_properties[STATE_KEY]
    assert state["artifacts"][0]["component"] == "ui-table"
    assert state["artifacts"][0]["title"] == "Comparison"

def test_ui_table_text_is_for_the_model_not_the_ui():
    content = ui_table.func(title="Comparison", columns=["A"], rows=[["1"]])

    assert "Comparison" in content.text
    assert "rows" not in content.text

def test_ui_table_assigns_matching_ids_to_result_and_state():
    content = ui_table.func(title="Comparison", columns=["A"], rows=[["1"]])

    payload = json.loads(content.additional_properties[DISPLAY_KEY])
    state = content.additional_properties[STATE_KEY]

    assert payload["id"] == state["artifacts"][0]["id"]
    assert payload["id"].startswith("art_")

def test_ui_table_ids_are_unique_across_calls():
    first = ui_table.func(title="One", columns=["A"], rows=[["1"]])
    second = ui_table.func(title="Two", columns=["A"], rows=[["2"]])

    first_id = json.loads(first.additional_properties[DISPLAY_KEY])["id"]
    second_id = json.loads(second.additional_properties[DISPLAY_KEY])["id"]

    assert first_id != second_id
