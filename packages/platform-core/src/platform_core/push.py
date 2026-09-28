"""Trust for A2A push notifications, on the side that receives them.

A webhook that accepted any caller would let anyone write into a conversation
or wake a waiting process step. The receiver therefore signs, at registration
time, exactly the correlation it will accept back -- scope, thread, agent;
instance and step -- and verifies the signature when the notification lands.
Signed and not stored: any replica verifies what any other replica issued,
with nothing to remember.

A token can also carry a time window, so that one captured in transit stops
working within two windows. Nothing here has a default secret: a receiver with
no secret configured issues no tokens and accepts none.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

HEADER = "X-A2A-Notification-Token"

TERMINAL = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
    }
)
NEEDS_INPUT = "TASK_STATE_INPUT_REQUIRED"


class MissingSecret(RuntimeError):
    """Raised instead of signing with an empty key."""


def _key(secret: str) -> bytes:
    if not secret:
        raise MissingSecret("no push secret configured: webhooks are disabled")
    return secret.encode()


def sign(secret: str, *parts: str) -> str:
    """An HMAC-SHA256 over the parts, in order. Any part changing changes it."""
    payload = "\x1f".join(parts).encode()
    return hmac.new(_key(secret), payload, hashlib.sha256).hexdigest()


def verify(secret: str, received: str | None, *parts: str) -> bool:
    if not received or not secret:
        return False
    return hmac.compare_digest(sign(secret, *parts), received)


def sign_windowed(
    secret: str, *parts: str, window_seconds: int, at: float | None = None
) -> str:
    """Like `sign`, bound to the time window the moment falls in."""
    window = int((at if at is not None else time.time()) // window_seconds)
    return sign(secret, *parts, str(window))


def verify_windowed(
    secret: str,
    received: str | None,
    *parts: str,
    window_seconds: int,
    at: float | None = None,
) -> bool:
    """Accepts the current window and the one before it.

    A task registers its webhook when it starts and calls back when it ends:
    without one window of slack, a task slower than the rounding of the clock
    would deliver a token that was valid when it was made and is not any more.
    """
    if not received or not secret:
        return False
    now = at if at is not None else time.time()
    return any(
        hmac.compare_digest(
            sign_windowed(
                secret,
                *parts,
                window_seconds=window_seconds,
                at=now - offset * window_seconds,
            ),
            received,
        )
        for offset in (0, 1)
    )


def summary_of(notification: Mapping[str, Any]) -> tuple[str, str, str]:
    """Reads (task_id, state, text) from a notification without trusting its shape.

    A2A servers notify one event at a time, in camelCase: sometimes a whole
    task, sometimes a status update, sometimes an artifact. Everything is
    accepted here and the caller decides what to ignore.
    """
    task = notification.get("task") or {}
    status_update = (
        notification.get("statusUpdate") or notification.get("status_update") or {}
    )
    artifact_update = (
        notification.get("artifactUpdate") or notification.get("artifact_update") or {}
    )

    task_id = str(
        task.get("id")
        or status_update.get("taskId")
        or status_update.get("task_id")
        or artifact_update.get("taskId")
        or artifact_update.get("task_id")
        or ""
    )
    state = str(
        (task.get("status") or {}).get("state")
        or (status_update.get("status") or {}).get("state")
        or ""
    )

    artifacts = list(task.get("artifacts") or [])
    if artifact_update.get("artifact"):
        artifacts.append(artifact_update["artifact"])
    parts = [
        part["text"]
        for artifact in artifacts
        for part in artifact.get("parts") or []
        if isinstance(part.get("text"), str)
    ]
    if not parts:
        message = (status_update.get("status") or {}).get("message") or {}
        parts = [
            part["text"]
            for part in message.get("parts") or []
            if isinstance(part.get("text"), str)
        ]
    return task_id, state, "".join(parts).strip()


def _normalized_path(path: str) -> str:
    """RFC 3986 dot-segment removal, so `/a2a/push/../admin` cannot pass as a prefix."""
    output: list[str] = []
    for segment in path.split("/"):
        if segment == "..":
            if len(output) > 1:
                output.pop()
        elif segment != ".":
            output.append(segment)
    return "/".join(output) or "/"


@dataclass(frozen=True)
class PushURLPolicy:
    """Where an A2A server may send notifications: an allowlist of URL prefixes.

    The URL of a push notification comes from whoever sent the task, so an
    agent that posted wherever it was told would be anyone's proxy into the
    internal network. Only the receivers the operator names are reachable;
    an empty list means push notifications are off.
    """

    prefixes: tuple[str, ...] = ()

    @classmethod
    def from_text(cls, text: str) -> PushURLPolicy:
        return cls(tuple(part.strip() for part in text.split(",") if part.strip()))

    def allows(self, url: str) -> bool:
        try:
            target = httpx.URL(url)
        except httpx.InvalidURL:
            return False
        if target.userinfo or target.scheme not in {"http", "https"}:
            return False
        path = _normalized_path(target.path)
        for prefix in self.prefixes:
            try:
                allowed = httpx.URL(prefix)
            except httpx.InvalidURL:
                continue
            same_origin = (
                target.scheme == allowed.scheme
                and (target.host or "").lower() == (allowed.host or "").lower()
                and target.port == allowed.port
            )
            if same_origin and _within(path, _normalized_path(allowed.path)):
                return True
        return False


def _within(path: str, prefix: str) -> bool:
    """`/a2a/push` admits `/a2a/push/x`, not `/a2a/pushover`."""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")
