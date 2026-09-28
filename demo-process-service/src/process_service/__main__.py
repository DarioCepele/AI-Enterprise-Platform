"""Local start: uv run python -m process_service."""

from __future__ import annotations

from platform_core.runtime import serve


def main() -> None:
    from .api import create_app
    from .config import get_settings

    serve(create_app, port=get_settings().port)


if __name__ == "__main__":
    main()
