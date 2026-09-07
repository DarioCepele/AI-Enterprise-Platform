import pytest

from demo.tools.plan_tools import PlanStore, build_plan_tools
from demo.tools.ui_tools import STATE_KEY

STEPS = [
    {
        "id": 1,
        "title": "Carica la skill",
        "detail": "Skill di confronto.",
        "source": "skill:comparison#1",
    },
    {
        "id": 2,
        "title": "Produci la tabella",
        "detail": "Confronto tabellare.",
        "source": "ui_table",
    },
]


def test_write_creates_pending_steps_and_starts_the_plan():
    store = PlanStore()
    plan = store.write(STEPS)

    assert plan["status"] == "in_progress"
    assert [s["status"] for s in plan["steps"]] == ["pending", "pending"]
    assert plan["steps"][0]["title"] == "Carica la skill"
    assert plan["steps"][0]["started_at"] is None


def test_set_status_touches_only_the_named_step():
    store = PlanStore()
    store.write(STEPS)
    plan = store.set_status(1, "in_progress", None)

    assert plan["steps"][0]["status"] == "in_progress"
    assert plan["steps"][0]["started_at"] is not None
    assert plan["steps"][1]["status"] == "pending"


def test_plan_completes_when_every_step_completes():
    store = PlanStore()
    store.write(STEPS)
    store.set_status(1, "completed", None)
    plan = store.set_status(2, "completed", None)

    assert plan["status"] == "completed"
    assert plan["steps"][1]["ended_at"] is not None


def test_a_failed_step_fails_the_plan():
    store = PlanStore()
    store.write(STEPS)
    store.set_status(1, "completed", None)
    plan = store.set_status(2, "failed", "il tool non ha risposto")

    assert plan["status"] == "failed"
    assert plan["steps"][1]["note"] == "il tool non ha risposto"


def test_a_failed_step_requires_a_reason():
    store = PlanStore()
    store.write(STEPS)

    with pytest.raises(ValueError, match="failed"):
        store.set_status(1, "failed", None)


def test_unknown_step_is_rejected_loudly():
    store = PlanStore()
    store.write(STEPS)

    with pytest.raises(ValueError, match="passo 99"):
        store.set_status(99, "completed", None)


def test_unknown_status_is_rejected_loudly():
    store = PlanStore()
    store.write(STEPS)

    with pytest.raises(ValueError, match="quasi"):
        store.set_status(1, "quasi", None)


def test_snapshot_is_a_copy_not_a_live_reference():
    store = PlanStore()
    store.write(STEPS)
    taken = store.snapshot()
    store.set_status(1, "completed", None)

    # Chi ha preso lo snapshot lo serializza dopo: non deve vederlo cambiare.
    assert taken["steps"][0]["status"] == "pending"


def test_tools_write_the_plan_into_shared_state():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    content = todo_write.func(steps=STEPS)
    state = content.additional_properties[STATE_KEY]

    assert "plan" in state
    # Mai `artifacts` insieme a `plan`: state_update sostituisce le chiavi di
    # primo livello, e passarle insieme cancellerebbe gli artefatti gia' emessi.
    assert "artifacts" not in state
    assert state["plan"]["steps"][0]["id"] == 1


def test_set_status_tool_reemits_the_whole_plan():
    store = PlanStore()
    todo_write, todo_set_status = build_plan_tools(store)
    todo_write.func(steps=STEPS)

    content = todo_set_status.func(step_id=1, status="completed", note=None)
    plan = content.additional_properties[STATE_KEY]["plan"]

    # Riemesso intero, non solo il passo cambiato.
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["status"] == "completed"


def test_plan_tool_text_is_short_and_for_the_model():
    store = PlanStore()
    todo_write, _ = build_plan_tools(store)

    content = todo_write.func(steps=STEPS)

    assert "2" in content.text
    assert "started_at" not in content.text
