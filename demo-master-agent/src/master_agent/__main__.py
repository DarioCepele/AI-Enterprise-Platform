"""Local start: uv run python -m master_agent

The only place a local .env is read: containers get their environment from the
orchestrator, and tests from the fixtures that set it.
"""

from platform_core.runtime import serve


def main() -> None:
    from .server.app import create_app

    serve(create_app, port=8000)


if __name__ == "__main__":
    main()
