"""Push trust on the receiving side: tokens, windows, allowlists."""

from __future__ import annotations

import pytest

from platform_core.push import (
    MissingSecret,
    PushURLPolicy,
    sign,
    sign_windowed,
    summary_of,
    verify,
    verify_windowed,
)

SECRET = "a-test-secret-long-enough"


def test_every_part_is_signed():
    token = sign(SECRET, "tenant-a", "thread-1", "knowledge")
    assert verify(SECRET, token, "tenant-a", "thread-1", "knowledge")
    assert not verify(SECRET, token, "tenant-b", "thread-1", "knowledge")
    assert not verify(SECRET, token, "tenant-a", "thread-2", "knowledge")
    assert not verify(SECRET, token, "tenant-a", "thread-1", "analysis")


def test_parts_cannot_be_shifted_across_the_separator():
    # "a:b" + "c" must not sign the same as "a" + "b:c".
    assert sign(SECRET, "a:b", "c") != sign(SECRET, "a", "b:c")


def test_no_secret_no_tokens():
    with pytest.raises(MissingSecret):
        sign("", "thread")
    assert verify("", "anything", "thread") is False


def test_a_token_of_the_previous_window_still_works():
    now = 1_800_000_000.0
    previous = sign_windowed(SECRET, "t1", window_seconds=3600, at=now - 3600)
    assert verify_windowed(SECRET, previous, "t1", window_seconds=3600, at=now)


def test_a_token_of_a_past_window_is_refused():
    now = 1_800_000_000.0
    old = sign_windowed(SECRET, "t1", window_seconds=3600, at=now - 3 * 3600)
    assert not verify_windowed(SECRET, old, "t1", window_seconds=3600, at=now)


def test_a_notification_is_read_without_trusting_its_shape():
    notification = {
        "task": {
            "id": "task-99",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"text": "Goroutines are lightweight."}]}],
        }
    }
    assert summary_of(notification) == (
        "task-99",
        "TASK_STATE_COMPLETED",
        "Goroutines are lightweight.",
    )
    assert summary_of({}) == ("", "", "")


def test_a_status_message_is_the_text_when_there_is_no_artifact():
    notification = {
        "statusUpdate": {
            "taskId": "t",
            "status": {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": {"parts": [{"text": "Which language?"}]},
            },
        }
    }
    assert summary_of(notification) == (
        "t",
        "TASK_STATE_INPUT_REQUIRED",
        "Which language?",
    )


POLICY = PushURLPolicy.from_text(
    "http://master-agent:8000/a2a/push/, http://process-service:8300/a2a/push"
)


@pytest.mark.parametrize(
    "url",
    [
        "http://master-agent:8000/a2a/push/default/t1/knowledge",
        "http://process-service:8300/a2a/push/default/i/s",
    ],
)
def test_the_named_receivers_are_allowed(url):
    assert POLICY.allows(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://process-service:8300/processes/example-approval/instances",
        "http://master-agent:8000/a2a/push/../../logs",
        "http://master-agent:8001/a2a/push/x",
        "https://master-agent:8000/a2a/push/x",
        "http://evil.example/a2a/push/x",
        "http://process-service:8300/a2a/pushover",
        "http://user@master-agent:8000/a2a/push/x",
        "file:///a2a/push/x",
    ],
)
def test_everything_else_is_refused(url):
    assert not POLICY.allows(url)


def test_no_prefixes_means_no_push():
    assert not PushURLPolicy().allows("http://master-agent:8000/a2a/push/x")
