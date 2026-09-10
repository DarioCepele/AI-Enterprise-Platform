"""Ephemeral storage for uploaded video attachments.

The AG-UI multimodal input standard carries a **URL** to a video
(`VideoInputPart` with an `InputContentUrlSource`), never the file itself:
before a video can be referenced that way, it needs somewhere to land. This
module is that somewhere -- a small, self-cleaning folder, not a datastore.
Nothing here is durable by construction: an upload that is not read again
within its TTL is gone, on purpose, consistent with the rest of the plan.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

# A short clip fits comfortably; a feature film does not. 200MB keeps a few
# concurrent uploads well inside what a single laboratory process should hold
# on disk at once.
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# Long enough that the next step of a turn can still fetch the file, short
# enough that an abandoned upload does not linger on disk.
DEFAULT_UPLOAD_TTL_SECONDS = 60 * 60


def default_upload_dir() -> Path:
    """Where uploads live when nothing else is configured: the OS temp folder.

    Not the project's own tree -- an ephemeral folder belongs where the OS
    already knows to reclaim it, not next to source code that gets copied,
    packaged, or committed.
    """
    return Path(tempfile.gettempdir()) / "demo-master-agent-uploads"


class UploadTooLarge(Exception):
    """Raised mid-stream, the moment a body crosses the configured ceiling."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"upload too large: over {max_bytes} bytes")
        self.max_bytes = max_bytes


class UploadStore:
    """Where an uploaded file lives between the POST that creates it and the
    GET that later reads it.

    There is no background task: every access -- a save or a read -- first
    sweeps away whatever in the folder is older than `ttl_seconds`. For a
    laboratory process that is enough to keep the folder bounded, and it is
    trivially testable without a scheduler: write an old file, or hand in a
    fake clock, and call a public method.
    """

    def __init__(
        self,
        directory: Path | None = None,
        max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        ttl_seconds: float = DEFAULT_UPLOAD_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory or default_upload_dir()
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, upload_id: str) -> Path:
        # The id normally comes from uuid4().hex, but it arrives from the URL
        # on the way in: refuse anything that could walk out of the folder.
        if not upload_id or "/" in upload_id or "\\" in upload_id or ".." in upload_id:
            raise ValueError(f"invalid upload id: {upload_id!r}")
        return self.directory / upload_id

    def _is_expired(self, path: Path) -> bool:
        return (self._clock() - path.stat().st_mtime) > self.ttl_seconds

    def sweep(self) -> None:
        """Deletes every file older than the TTL. Cheap, and safe to call often."""
        if not self.directory.is_dir():
            return
        for child in self.directory.iterdir():
            if not child.is_file():
                continue
            try:
                if self._is_expired(child):
                    child.unlink()
            except FileNotFoundError:
                pass  # swept by a concurrent request: nothing left to do.

    async def save(self, chunks: AsyncIterator[bytes]) -> str:
        """Streams `chunks` to a new file with a random id, and returns that id.

        Written incrementally so a body over the limit is caught -- and the
        partial file removed -- without ever holding the whole upload in
        memory first.
        """
        self.sweep()
        upload_id = uuid.uuid4().hex
        path = self._path_for(upload_id)
        written = 0
        try:
            with path.open("wb") as handle:
                async for chunk in chunks:
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise UploadTooLarge(self.max_bytes)
                    handle.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if written == 0:
            path.unlink(missing_ok=True)
            raise ValueError("empty upload")
        # Stamped with this store's own clock, not necessarily the OS wall
        # clock: what decides expiry is the same clock the sweep reads back,
        # which is what lets a test inject a fake one.
        stamp = self._clock()
        os.utime(path, (stamp, stamp))
        return upload_id

    def path_of(self, upload_id: str) -> Path:
        """The path to a live upload, or raises if it is missing or expired."""
        self.sweep()
        path = self._path_for(upload_id)
        if not path.is_file():
            raise FileNotFoundError(upload_id)
        return path


def build_upload_router(store: UploadStore, base_url: Callable[[Request], str]) -> APIRouter:
    """The two routes for the store above: `POST /uploads`, `GET /uploads/{id}`.

    `base_url` builds the URL prefix returned to the caller from the request
    itself, so the router works the same behind any host or proxy without a
    setting that has to be kept in sync with it.
    """
    router = APIRouter()

    @router.post("/uploads")
    async def create_upload(request: Request) -> dict[str, str]:
        declared = request.headers.get("content-length")
        if declared and int(declared) > store.max_bytes:
            logger.warning("Upload refused: %s bytes declared, limit %s.", declared, store.max_bytes)
            raise HTTPException(
                status_code=413,
                detail=f"upload too large: over {store.max_bytes} bytes",
            )
        try:
            upload_id = await store.save(request.stream())
        except UploadTooLarge as error:
            logger.warning("Upload refused mid-stream: over %s bytes.", store.max_bytes)
            raise HTTPException(status_code=413, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"url": f"{base_url(request)}/uploads/{upload_id}"}

    @router.get("/uploads/{upload_id}")
    async def read_upload(upload_id: str) -> FileResponse:
        try:
            path = store.path_of(upload_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(
                status_code=404, detail="upload not found or expired"
            ) from error
        return FileResponse(path, media_type="application/octet-stream")

    return router
