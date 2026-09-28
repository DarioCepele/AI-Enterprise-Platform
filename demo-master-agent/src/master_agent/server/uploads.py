"""Ephemeral storage for uploaded video and audio attachments.

The AG-UI multimodal input carries a **URL** to a video, never the file itself:
before a video can be referenced that way it needs somewhere to land. This
module is that somewhere -- a store with a time-to-live, not a datastore.

Two backends behind one interface:

- `PostgresUploadStore`, when the agent has its shared database: the file is
  written in 1 MB chunks to a table every replica reads, so the replica that
  received the upload and the one that runs the next turn need not be the same
  -- which, behind a load balancer, they usually are not. The same database the
  agent already uses for its shared state: no object store to add.
- `DiskUploadStore`, when it has none: a folder that sweeps itself, fine for
  one replica, and created only when the first file arrives, so a read-only
  filesystem does not stop the service from starting.

Only video and audio are accepted: this is an attachment store, not a place to
park arbitrary files for an hour.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# A short clip fits comfortably; a feature film does not.
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# Long enough that the next step of a turn can still fetch the file, short
# enough that an abandoned upload does not linger.
DEFAULT_UPLOAD_TTL_SECONDS = 60 * 60

CHUNK_BYTES = 1024 * 1024

ACCEPTED_TYPES = ("video/", "audio/")

UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
UPLOAD_PATH = re.compile(r"/uploads/([0-9a-f]{32})/?$")


class UploadTooLarge(Exception):
    """Raised mid-stream, the moment a body crosses the configured ceiling."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"upload too large: over {max_bytes} bytes")
        self.max_bytes = max_bytes


class UploadStore(Protocol):
    max_bytes: int

    async def save(self, chunks: AsyncIterator[bytes], content_type: str) -> str: ...

    async def open(self, upload_id: str) -> tuple[str, AsyncIterator[bytes]]:
        """Content type and chunks of a live upload; `FileNotFoundError` otherwise."""
        ...


def upload_id_in(url: str) -> str | None:
    """The upload id a URL of this store points at, whatever host it names."""
    match = UPLOAD_PATH.search(url.split("?", 1)[0])
    return match.group(1) if match else None


def default_upload_dir() -> Path:
    """Where uploads live when nothing else is configured: the OS temp folder."""
    return Path(tempfile.gettempdir()) / "master-agent-uploads"


def _checked_id(upload_id: str) -> str:
    # The id arrives from the URL: anything but our own format is refused, so
    # nothing can walk out of the folder or into another row.
    if not UPLOAD_ID.match(upload_id):
        raise FileNotFoundError(upload_id)
    return upload_id


class DiskUploadStore:
    """A self-cleaning folder, for a single replica.

    Every save and every read first sweeps away whatever is older than the
    TTL: bounded without a scheduler, and testable with a fake clock.
    """

    def __init__(
        self,
        directory: Path | None = None,
        max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        ttl_seconds: float = DEFAULT_UPLOAD_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._directory = directory
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    @property
    def directory(self) -> Path:
        # Resolved lazily: the temp folder of a read-only container is only
        # looked for when the first upload actually needs it.
        if self._directory is None:
            self._directory = default_upload_dir()
        return self._directory

    def _path_for(self, upload_id: str) -> Path:
        return self.directory / _checked_id(upload_id)

    def sweep(self) -> None:
        if not self.directory.is_dir():
            return
        for child in self.directory.iterdir():
            try:
                if child.is_file() and (
                    self._clock() - child.stat().st_mtime > self.ttl_seconds
                ):
                    child.unlink()
            except FileNotFoundError:
                pass  # swept by a concurrent request: nothing left to do.

    async def save(self, chunks: AsyncIterator[bytes], content_type: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sweep()
        upload_id = uuid.uuid4().hex
        path = self._path_for(upload_id)
        kind = path.with_suffix(".type")
        written = 0
        try:
            with path.open("wb") as handle:
                async for chunk in chunks:
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise UploadTooLarge(self.max_bytes)
                    handle.write(chunk)
            if written == 0:
                raise ValueError("empty upload")
            kind.write_text(content_type, encoding="utf-8")
        except BaseException:
            path.unlink(missing_ok=True)
            kind.unlink(missing_ok=True)
            raise
        # Stamped with this store's own clock: expiry is judged by the same one.
        stamp = self._clock()
        os.utime(path, (stamp, stamp))
        os.utime(kind, (stamp, stamp))
        return upload_id

    def path_of(self, upload_id: str) -> Path:
        """The path to a live upload, or `FileNotFoundError`."""
        self.sweep()
        path = self._path_for(upload_id)
        if not path.is_file():
            raise FileNotFoundError(upload_id)
        return path

    async def open(self, upload_id: str) -> tuple[str, AsyncIterator[bytes]]:
        path = self.path_of(upload_id)
        kind = path.with_suffix(".type")
        content_type = (
            kind.read_text(encoding="utf-8")
            if kind.is_file()
            else "application/octet-stream"
        )

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as handle:
                while block := handle.read(CHUNK_BYTES):
                    yield block

        return content_type, chunks()


class PostgresUploadStore:
    """Uploads in the shared database, readable by every replica."""

    def __init__(
        self,
        pool: Any,
        max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        ttl_seconds: float = DEFAULT_UPLOAD_TTL_SECONDS,
    ) -> None:
        self._pool = pool
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds

    async def sweep(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "DELETE FROM uploads "
                "WHERE created_at < now() - %s * interval '1 second'",
                (self.ttl_seconds,),
            )

    async def save(self, chunks: AsyncIterator[bytes], content_type: str) -> str:
        await self.sweep()
        upload_id = uuid.uuid4().hex
        written = 0
        buffer = bytearray()
        sequence = 0
        # One transaction: an upload refused half-way leaves no trace behind.
        async with self._pool.connection() as connection:
            await connection.execute(
                "INSERT INTO uploads (id, content_type, size) VALUES (%s, %s, 0)",
                (upload_id, content_type),
            )

            async def flush() -> None:
                nonlocal sequence
                await connection.execute(
                    "INSERT INTO upload_chunks (upload_id, seq, data) "
                    "VALUES (%s, %s, %s)",
                    (upload_id, sequence, bytes(buffer)),
                )
                sequence += 1
                buffer.clear()

            async for chunk in chunks:
                written += len(chunk)
                if written > self.max_bytes:
                    raise UploadTooLarge(self.max_bytes)
                buffer.extend(chunk)
                if len(buffer) >= CHUNK_BYTES:
                    await flush()
            if buffer:
                await flush()
            if written == 0:
                raise ValueError("empty upload")
            await connection.execute(
                "UPDATE uploads SET size = %s WHERE id = %s", (written, upload_id)
            )
        return upload_id

    async def open(self, upload_id: str) -> tuple[str, AsyncIterator[bytes]]:
        _checked_id(upload_id)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT content_type, "
                "(SELECT count(*) FROM upload_chunks WHERE upload_id = uploads.id) "
                "FROM uploads WHERE id = %s "
                "AND created_at >= now() - %s * interval '1 second'",
                (upload_id, self.ttl_seconds),
            )
            row = await cursor.fetchone()
        if row is None:
            raise FileNotFoundError(upload_id)
        content_type, count = str(row[0]), int(row[1])

        async def chunks() -> AsyncIterator[bytes]:
            for sequence in range(count):
                async with self._pool.connection() as connection:
                    cursor = await connection.execute(
                        "SELECT data FROM upload_chunks "
                        "WHERE upload_id = %s AND seq = %s",
                        (upload_id, sequence),
                    )
                    found = await cursor.fetchone()
                if found is None:
                    return
                yield bytes(found[0])

        return content_type, chunks()


def build_upload_router(
    store: UploadStore, base_url: Callable[[Request], str]
) -> APIRouter:
    """`POST /uploads` and `GET /uploads/{id}` for the store above.

    `base_url` builds the URL prefix returned to the caller from the request
    itself, so the router works the same behind any host or proxy.
    """
    router = APIRouter()

    @router.post("/uploads")
    async def create_upload(request: Request) -> dict[str, str]:
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
        if not content_type.startswith(ACCEPTED_TYPES):
            raise HTTPException(
                status_code=415, detail="only video and audio files can be attached"
            )
        try:
            upload_id = await store.save(request.stream(), content_type)
        except UploadTooLarge as error:
            logger.warning("Upload refused mid-stream: over %s bytes.", store.max_bytes)
            raise HTTPException(status_code=413, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"url": f"{base_url(request)}/uploads/{upload_id}"}

    @router.get("/uploads/{upload_id}")
    async def read_upload(upload_id: str) -> StreamingResponse:
        try:
            content_type, chunks = await store.open(upload_id)
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail="upload not found or expired"
            ) from error
        return StreamingResponse(
            chunks,
            media_type=content_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return router
