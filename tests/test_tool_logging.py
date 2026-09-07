from demo.logging_bridge import LogCollector
from demo.tools.plan_tools import PlanStore, build_plan_tools
from demo.tools.skill_tools import build_skill_tools
from demo.tools.ui_tools import ui_table

STEPS = [
    {"id": 1, "title": "Primo", "detail": "d", "source": "ui_table"},
    {"id": 2, "title": "Secondo", "detail": "d", "source": "ui_table"},
]


def test_writing_a_plan_is_logged():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    with LogCollector() as collector:
        todo_write.func(steps=STEPS)

    entries = collector.since(0)["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "tools.plan_tools"
    assert entries[0]["level"] == "INFO"
    assert "2" in entries[0]["message"]


def test_every_step_transition_is_logged():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    with LogCollector() as collector:
        todo_set_status.func(step_id=1, status="in_progress", note=None)
        todo_set_status.func(step_id=1, status="completed", note=None)

    messages = [e["message"] for e in collector.since(0)["entries"]]
    assert len(messages) == 2
    assert "in_progress" in messages[0]
    assert "completed" in messages[1]


def test_a_failed_step_is_logged_as_an_error_with_its_reason():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    with LogCollector() as collector:
        todo_set_status.func(step_id=1, status="failed", note="il tool non risponde")

    entry = collector.since(0)["entries"][0]
    assert entry["level"] == "ERROR"
    assert "il tool non risponde" in entry["message"]


def test_loading_a_skill_is_logged_with_its_name():
    (load_skill,) = build_skill_tools()

    with LogCollector() as collector:
        load_skill.func(name="comparison")

    entry = collector.since(0)["entries"][0]
    assert entry["source"] == "tools.skill_tools"
    assert "comparison" in entry["message"]


def test_asking_for_an_unknown_skill_is_logged_as_a_warning():
    (load_skill,) = build_skill_tools()

    with LogCollector() as collector:
        load_skill.func(name="inesistente")

    entry = collector.since(0)["entries"][0]
    assert entry["level"] == "WARNING"
    assert "inesistente" in entry["message"]


def test_producing_a_table_is_logged_with_its_shape():
    with LogCollector() as collector:
        ui_table.func(title="Confronto", columns=["A", "B"], rows=[["1", "2"]])

    entry = collector.since(0)["entries"][0]
    assert entry["source"] == "tools.ui_tools"
    assert "Confronto" in entry["message"]
    assert "2 colonne" in entry["message"]
    assert "1 righe" in entry["message"]


def test_tool_logs_never_carry_the_whole_payload():
    with LogCollector() as collector:
        ui_table.func(
            title="Confronto",
            columns=["A", "B"],
            rows=[["testo molto lungo " * 20, "altro testo lungo " * 20]],
        )

    entry = collector.since(0)["entries"][0]
    assert len(entry["message"]) < 200
    assert "testo molto lungo" not in entry["message"]
    assert "altro testo lungo" not in entry["message"]
