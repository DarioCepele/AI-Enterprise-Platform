"""Local start: uv run python -m analysis."""

from __future__ import annotations

from platform_core.runtime import serve


def main() -> None:
    from .server import create_app

    serve(create_app, port=8400)


if __name__ == "__main__":
    main()
