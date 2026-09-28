import logging

from master_agent.logging_bridge import MAX_LOG_EVENTS, LogCollector


def test_collects_application_logs():
    with LogCollector() as collector:
        logging.getLogger("master_agent.tools").info("plan written")

    page = collector.since("")

    assert len(page["entries"]) == 1
    assert page["entries"][0]["level"] == "INFO"
    assert page["entries"][0]["source"] == "tools"
    assert page["entries"][0]["message"] == "plan written"
    assert page["entries"][0]["ts"].endswith("+00:00")
    assert page["dropped"] == 0

def test_library_logs_never_reach_the_stream():

    with LogCollector() as collector:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=secret")
        logging.getLogger("openai").warning("retry")
        logging.getLogger("uvicorn.access").info("GET /agui")

    assert collector.since("")["entries"] == []

def test_the_cursor_advances_and_does_not_repeat_entries():
    with LogCollector() as collector:
        logging.getLogger("master_agent.a").info("one")
        first = collector.since("")
        logging.getLogger("master_agent.a").info("two")
        second = collector.since(first["cursor"])

    assert [e["message"] for e in first["entries"]] == ["one"]
    assert [e["message"] for e in second["entries"]] == ["two"]
    assert int(second["cursor"]) > int(first["cursor"])

def test_reading_twice_from_the_same_cursor_is_idempotent():

    with LogCollector() as collector:
        logging.getLogger("master_agent.a").info("one")

    assert collector.since("")["entries"] == collector.since("")["entries"]

def test_a_non_numeric_cursor_is_treated_as_the_start():
    with LogCollector() as collector:
        logging.getLogger("master_agent.a").info("one")

    assert collector.since("not-a-number")["entries"] == collector.since("")["entries"]

def test_the_buffer_is_capped_and_reports_what_it_dropped():
    with LogCollector() as collector:
        for i in range(MAX_LOG_EVENTS + 50):
            logging.getLogger("master_agent.noise").info("line %d", i)

    page = collector.since("")

    assert len(page["entries"]) == MAX_LOG_EVENTS
    assert page["dropped"] == 50

    assert page["entries"][-1]["message"] == f"line {MAX_LOG_EVENTS + 49}"

def test_detach_stops_collecting():
    collector = LogCollector()
    collector.attach()
    collector.detach()
    logging.getLogger("master_agent.a").info("after detaching")

    assert collector.since("")["entries"] == []

def test_attaching_twice_does_not_double_every_line():
    collector = LogCollector()
    collector.attach()
    collector.attach()
    logging.getLogger("master_agent.a").info("only once")
    collector.detach()

    assert len(collector.since("")["entries"]) == 1

def test_exceptions_arrive_as_text_not_as_objects():
    with LogCollector() as collector:
        try:
            raise RuntimeError("the tool blew up")
        except RuntimeError:
            logging.getLogger("master_agent.tools").exception("call failed")

    entry = collector.since("")["entries"][0]

    assert entry["level"] == "ERROR"
    assert "the tool blew up" in entry["message"]
    assert "Traceback" in entry["message"]

def test_detach_restores_the_previous_level():
    logger = logging.getLogger("master_agent")
    before = logger.level

    collector = LogCollector()
    collector.attach()
    collector.detach()

    assert logger.level == before
