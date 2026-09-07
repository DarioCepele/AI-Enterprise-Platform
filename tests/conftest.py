import pytest

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient, ToolCallingFakeClient
from demo.server.app import create_app


@pytest.fixture
def app():
    """App con un client che emette solo testo."""
    agent = build_master_agent(
        chat_client=FakeStreamingChatClient(chunks=["ciao ", "mondo"])
    )
    return create_app(agent=agent)


@pytest.fixture
def tool_app():
    """App con un client che al primo giro chiama ui_table."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="ui_table",
            tool_args={
                "title": "Confronto",
                "columns": ["Tema", "A", "B"],
                "rows": [["Copertura", "vuoto", "pieno"]],
            },
            final_text="Ecco il confronto.",
        )
    )
    return create_app(agent=agent)
