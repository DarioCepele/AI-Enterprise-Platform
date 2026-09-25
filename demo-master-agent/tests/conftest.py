import pytest

from demo.agents.master import build_master_agent
from demo.chat_clients.fake import FakeStreamingChatClient, ToolCallingFakeClient
from demo.server.app import create_app
from demo.tools.plan_tools import PlanStore


@pytest.fixture
def app():
    """App with a client that emits text only."""
    agent = build_master_agent(
        chat_client=FakeStreamingChatClient(chunks=["hello ", "world"])
    )
    return create_app(agent=agent)


@pytest.fixture
def tool_app():
    """App with a client that calls ui_table on the first round."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="ui_table",
            tool_args={
                "title": "Comparison",
                "columns": ["Topic", "A", "B"],
                "rows": [["Coverage", "empty", "full"]],
            },
            final_text="Here is the comparison.",
        )
    )
    return create_app(agent=agent)


@pytest.fixture
def plan_app():
    """App with a client that writes a plan on the first round."""
    agent = build_master_agent(
        chat_client=ToolCallingFakeClient(
            tool_name="todo_write",
            tool_args={
                "steps": [
                    {
                        "id": 1,
                        "title": "First step",
                        "detail": "detail",
                        "source": "ui_table",
                    }
                ]
            },
            final_text="Plan ready.",
        ),
        plan_store=PlanStore(),
    )
    return create_app(agent=agent)
