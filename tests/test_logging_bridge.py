import logging

from demo.logging_bridge import MAX_LOG_EVENTS, LogCollector


def test_collects_application_logs():
    with LogCollector() as collector:
        logging.getLogger("demo.tools").info("piano scritto")

    page = collector.since(0)

    assert len(page["entries"]) == 1
    assert page["entries"][0]["level"] == "INFO"
    assert page["entries"][0]["source"] == "tools"
    assert page["entries"][0]["message"] == "piano scritto"
    assert page["entries"][0]["ts"].endswith("+00:00")
    assert page["dropped"] == 0


def test_library_logs_never_reach_the_stream():
    # httpx e openai loggano URL con la chiave API dentro: se questo test
    # sparisce, la chiave finisce nel browser di chi apre la pagina.
    with LogCollector() as collector:
        logging.getLogger("httpx").info("POST https://api.example/v1?key=segreto")
        logging.getLogger("openai").warning("retry")
        logging.getLogger("uvicorn.access").info("GET /agui")

    assert collector.since(0)["entries"] == []


def test_the_cursor_advances_and_does_not_repeat_entries():
    with LogCollector() as collector:
        logging.getLogger("demo.a").info("uno")
        first = collector.since(0)
        logging.getLogger("demo.a").info("due")
        second = collector.since(first["cursor"])

    assert [e["message"] for e in first["entries"]] == ["uno"]
    assert [e["message"] for e in second["entries"]] == ["due"]
    assert second["cursor"] > first["cursor"]


def test_reading_twice_from_the_same_cursor_is_idempotent():
    # Il client puo' ritentare dopo un errore di rete: non deve perdere righe.
    with LogCollector() as collector:
        logging.getLogger("demo.a").info("uno")

    assert collector.since(0)["entries"] == collector.since(0)["entries"]


def test_the_buffer_is_capped_and_reports_what_it_dropped():
    with LogCollector() as collector:
        for i in range(MAX_LOG_EVENTS + 50):
            logging.getLogger("demo.rumore").info("riga %d", i)

    page = collector.since(0)

    assert len(page["entries"]) == MAX_LOG_EVENTS
    assert page["dropped"] == 50
    # Le righe tenute sono le ultime, non le prime.
    assert page["entries"][-1]["message"] == f"riga {MAX_LOG_EVENTS + 49}"


def test_detach_stops_collecting():
    collector = LogCollector()
    collector.attach()
    collector.detach()
    logging.getLogger("demo.a").info("dopo il distacco")

    assert collector.since(0)["entries"] == []


def test_attaching_twice_does_not_double_every_line():
    collector = LogCollector()
    collector.attach()
    collector.attach()
    logging.getLogger("demo.a").info("una volta sola")
    collector.detach()

    assert len(collector.since(0)["entries"]) == 1


def test_exceptions_arrive_as_text_not_as_objects():
    with LogCollector() as collector:
        try:
            raise RuntimeError("il tool e' esploso")
        except RuntimeError:
            logging.getLogger("demo.tools").exception("chiamata fallita")

    entry = collector.since(0)["entries"][0]

    assert entry["level"] == "ERROR"
    assert "il tool e' esploso" in entry["message"]
    assert "Traceback" in entry["message"]


def test_detach_restores_the_previous_level():
    logger = logging.getLogger("demo")
    before = logger.level

    collector = LogCollector()
    collector.attach()
    collector.detach()

    assert logger.level == before
