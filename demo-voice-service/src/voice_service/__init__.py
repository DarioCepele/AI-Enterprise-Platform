"""Voice service: real-time voice pipeline scaffold.

This package currently holds only the service skeleton -- health probes and
the uv/FastAPI/Docker wiring shared with the other services in the
laboratory. The Pipecat pipeline (WebSocket audio in, VAD, STT, the AG-UI
turn, TTS, audio out) is built on top of this in later work.
"""
