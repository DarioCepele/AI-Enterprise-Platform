"""Le notifiche push dei sottoagenti: correlazione e fiducia."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from demo.a2a.push import HEADER, riassunto, token_per, token_valido, url_webhook
from demo.chat_clients.fake import FakeStreamingChatClient
from demo.agents.master import build_master_agent
from demo.server.app import create_app

NOTIFICA = {
    "task": {
        "id": "task-99",
        "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"name": "scheda", "parts": [{"text": "Le goroutine sono leggere."}]}],
    }
}


def test_the_token_signs_the_thread_not_the_task():
    # Il webhook si registra prima che il task esista: un token sul task id
    # non si potrebbe calcolare in anticipo.
    assert token_per("t1") == token_per("t1")
    assert token_per("t1") != token_per("t2")


def test_a_token_of_another_thread_is_not_valid():
    assert token_valido("t1", token_per("t1")) is True
    assert token_valido("t1", token_per("t2")) is False
    assert token_valido("t1", None) is False


def test_the_url_carries_the_correlation():
    url = url_webhook("http://master:8000", "tenant-a", "t1")

    assert url == "http://master:8000/a2a/push/tenant-a/t1"


def test_a_notification_is_read_without_trusting_its_shape():
    assert riassunto(NOTIFICA) == ("task-99", "TASK_STATE_COMPLETED", "Le goroutine sono leggere.")
    assert riassunto({}) == ("", "", "")
    assert riassunto({"task": {"id": "x"}}) == ("x", "", "")


@pytest.fixture
def app():
    return create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"]))
    )


async def invia(app, thread_id: str, token: str | None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/a2a/push/tenant-a/{thread_id}",
            json=NOTIFICA,
            headers={HEADER: token} if token else {},
        )


@pytest.mark.asyncio
async def test_a_signed_notification_is_accepted(app, caplog):
    with caplog.at_level(logging.INFO, logger="demo.server.app"):
        response = await invia(app, "t1", token_per("t1"))

    assert response.status_code == 200
    assert "ha concluso il task task-99" in caplog.text


@pytest.mark.asyncio
async def test_an_unsigned_notification_is_refused(app, caplog):
    # Un webhook aperto e' un modo per far scrivere a chiunque nella memoria
    # di una conversazione.
    with caplog.at_level(logging.WARNING, logger="demo.server.app"):
        response = await invia(app, "t1", None)

    assert response.status_code == 403
    assert "token non valido" in caplog.text


@pytest.mark.asyncio
async def test_a_notification_signed_for_another_thread_is_refused(app):
    response = await invia(app, "t1", token_per("t2"))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_without_a_memory_service_the_outcome_stays_in_the_logs(app, caplog, monkeypatch):
    monkeypatch.setenv("DEMO_MEMORY_SERVICE_URL", "")

    with caplog.at_level(logging.WARNING, logger="demo.server.app"):
        response = await invia(app, "t1", token_per("t1"))

    assert response.status_code == 200
    assert "resta nei log" in caplog.text


@pytest.mark.asyncio
async def test_the_outcome_is_written_into_the_thread_memory(app, monkeypatch):
    ricevute: list[tuple[str, dict]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        import json

        ricevute.append((str(request.url), json.loads(request.content)))
        return httpx.Response(201, json={"seq": 1})

    monkeypatch.setenv("DEMO_MEMORY_SERVICE_URL", "http://memoria")
    originale = httpx.AsyncClient

    def finto(*args, **kwargs):
        # Solo il client verso la memoria: patchare tutti intercetterebbe anche
        # il transport ASGI con cui il test parla con l'app.
        if kwargs.get("base_url") == "http://memoria":
            kwargs["transport"] = httpx.MockTransport(transport)
        return originale(*args, **kwargs)

    monkeypatch.setattr("demo.server.app.httpx.AsyncClient", finto)

    response = await invia(app, "t1", token_per("t1"))
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert ricevute, "nessuna scrittura in memoria"
    url, corpo = ricevute[0]
    assert url.endswith("/threads/t1/messages")
    assert "Le goroutine sono leggere." in corpo["content"]


AVANZAMENTO = {"statusUpdate": {"taskId": "task-99", "status": {"state": "TASK_STATE_WORKING"}}}


@pytest.mark.asyncio
async def test_progress_notifications_are_ignored(app, caplog):
    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.INFO, logger="demo.server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/a2a/push/tenant-a/t1",
                json=AVANZAMENTO,
                headers={HEADER: token_per("t1")},
            )

    # Il sottoagente notifica ogni evento: scrivere in memoria a ogni
    # avanzamento riempirebbe la conversazione di rumore.
    assert response.json() == {"stato": "avanzamento ignorato"}
    assert "ha concluso" not in caplog.text
