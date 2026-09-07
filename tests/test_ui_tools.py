import json

from demo.tools.ui_tools import DISPLAY_KEY, STATE_KEY, get_tools, ui_table


def test_get_tools_exposes_ui_table():
    names = [t.name for t in get_tools()]
    assert "ui_table" in names


def test_ui_table_builds_a_ui_payload():
    # FunctionTool espone la funzione sottostante come .func
    content = ui_table.func(
        title="Confronto",
        columns=["Tema", "A", "B"],
        rows=[["Copertura", "vuoto", "pieno"]],
    )

    # state_update serializza tool_result in una stringa JSON.
    payload = json.loads(content.additional_properties[DISPLAY_KEY])
    assert payload["component"] == "ui-table"
    assert payload["title"] == "Confronto"
    assert payload["columns"] == ["Tema", "A", "B"]
    assert payload["rows"] == [["Copertura", "vuoto", "pieno"]]


def test_ui_table_merges_into_shared_state():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    # Lo state invece resta un dict.
    assert content.additional_properties[STATE_KEY] == {
        "artifacts": [{"component": "ui-table", "title": "Confronto"}]
    }


def test_ui_table_text_is_for_the_model_not_the_ui():
    content = ui_table.func(title="Confronto", columns=["A"], rows=[["1"]])

    assert "Confronto" in content.text
    assert "rows" not in content.text
