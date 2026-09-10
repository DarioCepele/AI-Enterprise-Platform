"""Make video/audio attachment URLs visible as plain text.

AG-UI lets a user turn carry a video or audio part (``VideoInputContent`` /
``AudioInputContent`` in ``ag_ui.core.types``, aliased as ``VideoInputPart`` /
``AudioInputPart``) referencing the media by URL
(``InputContentUrlSource.value``) or by inline base64
(``InputContentDataSource.value``).

``agent_framework_ag_ui`` turns such a part into an
``agent_framework.Content.from_uri(uri=url, media_type="video/*")`` (see
``agent_framework_ag_ui/_message_adapters.py::_parse_multimodal_media_part``)
and attaches it to the message next to any text parts. Whether the chat
client underneath can do anything useful with a raw ``video/*``/``audio/*``
content item depends on the provider - most chat-completions APIs only
understand images. If the model never sees the URL as ordinary text, it can
never pass it as a string argument to a tool that knows how to fetch or
analyze the video (a tool arriving in a later contract).

This module adds a plain-text note next to such a part, so the URL survives
even when the raw multimodal content does not mean anything to the
underlying client:

    "\\n[allegato video: http://example.test/video.mp4]"

Only ``InputContentUrlSource`` parts are handled: an inline
``InputContentDataSource`` (base64) attachment has no URL to expose, so it is
left untouched by design.
"""
from __future__ import annotations

from typing import Any

# AG-UI multimodal part "type" -> human label used in the note.
_MEDIA_LABELS = {
    "video": "allegato video",
    "audio": "allegato audio",
}


def _url_from_source(part: dict[str, Any]) -> str | None:
    """Return the URL of a part's ``InputContentUrlSource``, or ``None``.

    An ``InputContentDataSource`` (``source.type == "data"``, inline base64)
    has no URL: this case is intentionally left unhandled here.
    """
    source = part.get("source")
    if not isinstance(source, dict):
        return None
    source_type = str(source.get("type", "")).lower()
    if source_type not in {"url", "uri"}:
        return None
    value = source.get("value")
    return value if isinstance(value, str) and value else None


def _attachment_notes(content_parts: list[Any]) -> list[str]:
    notes: list[str] = []
    for part in content_parts:
        if not isinstance(part, dict):
            continue
        label = _MEDIA_LABELS.get(str(part.get("type", "")).lower())
        if label is None:
            continue
        url = _url_from_source(part)
        if url:
            notes.append(f"[{label}: {url}]")
    return notes


def annotate_video_audio_attachments(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return ``messages`` with a text note appended for URL-referenced video/audio parts.

    Only user messages are inspected (the "incoming turn"), and only messages
    whose ``content`` is already a list of typed parts (the multimodal shape;
    a plain string ``content`` cannot carry a video/audio part). Messages with
    nothing to annotate are returned unchanged; the input list itself is never
    mutated.
    """
    annotated: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if str(message.get("role", "")).lower() != "user" or not isinstance(content, list):
            annotated.append(message)
            continue

        notes = _attachment_notes(content)
        if not notes:
            annotated.append(message)
            continue

        new_message = dict(message)
        new_message["content"] = [*content, {"type": "text", "text": "\n" + "\n".join(notes)}]
        annotated.append(new_message)

    return annotated
