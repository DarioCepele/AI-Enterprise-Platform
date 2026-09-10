"""Vision: describing what a handful of video frames show.

Behind a small, substitutable interface -- the same shape `chat_clients`
already gives the master agent's own conversation (a real client and a
`FakeStreamingChatClient` for tests) -- so `tools/video_tools.py` never has to
know whether the description in front of it came from a real model or a
canned fake.

Why a separate module instead of routing frames through the agent's own
`BaseChatClient`: that client is owned by `Agent` (built once in
`agents/master.py`, wrapped in middleware, driving the run loop) and is not
handed to tool factories the way `build_process_tools`/`build_memory_tools`
receive a service URL -- reaching into it from a tool would mean threading a
new parameter through `build_master_agent` into every tool builder for one
tool's sake. Calling the same OpenAI-compatible endpoint directly (the one
`OpenAIChatCompletionClient` already talks to, per `config.Settings.base_url`/
`api_key`/`model`) gets the same reuse -- one model, one provider, one set of
credentials -- without that plumbing, at the cost of one extra HTTP client
class rather than a second provider abstraction.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 60.0

DEFAULT_PROMPT = (
    "Describe briefly and concretely what these video frames show: setting, "
    "people, objects, on-screen text, notable colors."
)


class VisionClient(Protocol):
    """Describes what a handful of images show, in reply to a question."""

    async def describe_frames(self, images: Sequence[bytes], question: str) -> str:
        """Returns a short description of `images` (each JPEG-encoded)."""
        ...


class HttpVisionClient:
    """Calls a chat-completions endpoint with image content, OpenAI-style.

    Reuses `config.Settings`' own `base_url`/`api_key`/`model` by default --
    the same endpoint `OpenAIChatCompletionClient` talks to for the
    conversation itself -- so a vision-capable model configured once serves
    both. Any argument can be overridden, mainly so a test does not have to
    reach into `Settings` to point this somewhere else.
    """

    def __init__(self, base_url: str = "", api_key: str = "", model: str = "") -> None:
        if not (base_url and model):
            from .config import get_settings

            settings = get_settings()
            base_url = base_url or settings.base_url
            api_key = api_key or settings.api_key
            model = model or settings.model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def describe_frames(self, images: Sequence[bytes], question: str) -> str:
        if not images:
            return ""
        prompt = question.strip() or DEFAULT_PROMPT
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json={"model": self._model, "messages": [{"role": "user", "content": content}]},
            )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content", "")).strip()


class FakeVisionClient:
    """Deterministic description, without a model: the vision equivalent of
    `chat_clients.fake.FakeStreamingChatClient`.

    Every call is recorded in `calls` (the images it received, and the
    question), so a test can assert frames actually reached it -- not only
    that its canned `description` made it into the final answer.
    """

    def __init__(self, description: str = "a video frame") -> None:
        self.description = description
        self.calls: list[tuple[list[bytes], str]] = []

    async def describe_frames(self, images: Sequence[bytes], question: str) -> str:
        self.calls.append((list(images), question))
        return self.description
