"""Pure context-curation tests without a database."""
from __future__ import annotations

from memory_service.curation import CLEARED, ContextPolicy, curate


def msg(role: str, content: str = "x", **extra) -> dict:
    return {"role": role, "content": content, **extra}


def turn(user: str = "question") -> list[dict]:
    """Build a complete agent turn."""
    return [
        msg("user", user),
        msg("reasoning", "", encrypted_value="[molto lungo]"),
        msg("assistant", "", toolCalls=[{"id": "c1"}]),
        msg("tool", "tool result"),
        msg("assistant", "answer"),
    ]


def test_nothing_to_curate_is_left_alone():
    messages = [msg("user", "ciao"), msg("assistant", "ok")]

    curated, report = curate(messages, ContextPolicy())

    assert curated == messages
    assert report.as_dict() == {
        "kept": 2,
        "reasoning_removed": 0,
        "results_emptied": 0,
        "messages_dropped": 0,
        "summarized": 0,
    }


def test_reasoning_of_past_turns_does_not_come_back():
    curated, report = curate(turn(), ContextPolicy())

    assert [m["role"] for m in curated] == ["user", "assistant", "tool", "assistant"]
    assert report.reasoning_removed == 1


def test_reasoning_can_be_kept_when_asked():
    curated, report = curate(turn(), ContextPolicy(drop_reasoning=False))

    assert any(m["role"] == "reasoning" for m in curated)
    assert report.reasoning_removed == 0


def test_old_tool_results_are_emptied_but_the_call_stays():
    messages = [msg("tool", f"risultato {i}") for i in range(6)]

    curated, report = curate(messages, ContextPolicy(keep_tool_results=2))

    assert [m["content"] for m in curated] == [CLEARED] * 4 + ["risultato 4", "risultato 5"]
    assert len(curated) == 6
    assert report.results_emptied == 4


def test_the_most_recent_results_survive_whole():
    messages = [msg("tool", "old"), msg("tool", "recent")]

    curated, _ = curate(messages, ContextPolicy(keep_tool_results=1))

    assert curated[-1]["content"] == "recent"


def test_the_window_cuts_on_a_turn_boundary():
    messages = turn("primo") + turn("secondo") + turn("terzo")

    curated, report = curate(
        messages, ContextPolicy(drop_reasoning=False, keep_tool_results=99, max_messages=7)
    )

    assert curated[0]["role"] == "user"
    assert curated[0]["content"] == "secondo"
    assert report.messages_dropped == 5


def test_a_conversation_without_turn_boundaries_is_kept_whole():
    messages = [msg("assistant", str(i)) for i in range(10)]

    curated, report = curate(messages, ContextPolicy(max_messages=3))

    assert len(curated) == 10
    assert report.messages_dropped == 0


def test_the_last_user_message_is_never_dropped():
    messages = turn("old") + [msg("user", "last")]

    curated, _ = curate(messages, ContextPolicy(max_messages=1))

    assert curated[-1]["content"] == "last"


def test_the_original_messages_are_not_modified():
    messages = [msg("tool", "risultato"), msg("tool", "other"), msg("tool", "terzo")]

    curate(messages, ContextPolicy(keep_tool_results=1))

    assert [m["content"] for m in messages] == ["risultato", "other", "terzo"]


def test_curation_counts_what_survived():
    curated, report = curate(turn() + turn(), ContextPolicy(keep_tool_results=1))

    assert report.kept == len(curated)
    assert report.reasoning_removed == 2
    assert report.results_emptied == 1


def test_the_summary_goes_on_top_only_when_something_was_dropped():
    from memory_service.curation import summary_message

    summary = summary_message("si parlava di Python e Go", covers_to_seq=12)
    messages = turn("primo") + turn("secondo") + turn("terzo")

    con_taglio, report = curate(messages, ContextPolicy(max_messages=7), summary)
    senza_taglio, whole_report = curate(turn(), ContextPolicy(max_messages=60), summary)

    assert con_taglio[0]["role"] == "system"
    assert "Python e Go" in con_taglio[0]["content"]
    assert report.summarized is True
    assert all(m["role"] != "system" for m in senza_taglio)
    assert whole_report.summarized is False


def test_a_dropped_prefix_without_a_summary_is_declared_as_such():
    messages = turn("primo") + turn("secondo") + turn("terzo")

    _, report = curate(messages, ContextPolicy(max_messages=7))

    assert report.messages_dropped > 0
    assert report.summarized is False
