"""Where the authorization boundary will be plugged in.

The scope decides whose conversations a request may read. Today it is one
tenant and the resolver says so; when authentication arrives, this is the
function that changes, and nothing else has to.
"""
from __future__ import annotations

import pytest
from starlette.datastructures import Headers

from demo.config import get_settings
from demo.server.scope import scope_of_request


class Request:
    def __init__(self, **headers: str) -> None:
        self.headers = Headers(headers)


def test_without_headers_the_configured_scope_is_used(monkeypatch):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")

    assert scope_of_request(Request()) == "acme-tenant"


def test_the_header_is_ignored_until_someone_verifies_it(monkeypatch):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")
    monkeypatch.delenv("DEMO_SCOPE_HEADER", raising=False)

    # A header nobody checked is a request from the client, not an identity: a
    # single-tenant laboratory that trusted it would let anyone read any thread
    # by typing a name.
    assert scope_of_request(Request(**{"X-Scope": "someone-else"})) == "acme-tenant"


def test_the_header_is_read_only_when_it_is_declared_trusted(monkeypatch):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")
    monkeypatch.setenv("DEMO_SCOPE_HEADER", "X-Scope")

    assert scope_of_request(Request(**{"X-Scope": "tenant-b"})) == "tenant-b"


def test_an_empty_or_missing_trusted_header_falls_back(monkeypatch):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")
    monkeypatch.setenv("DEMO_SCOPE_HEADER", "X-Scope")

    assert scope_of_request(Request(**{"X-Scope": "   "})) == "acme-tenant"
    assert scope_of_request(Request()) == "acme-tenant"


@pytest.mark.parametrize("value", ["tenant b", "tenant/b", "../etc", "a" * 200])
def test_a_scope_that_is_not_a_name_falls_back(monkeypatch, value):
    monkeypatch.setenv("DEMO_DEFAULT_SCOPE", "acme-tenant")
    monkeypatch.setenv("DEMO_SCOPE_HEADER", "X-Scope")

    # The scope ends up in URLs and in database keys: an arbitrary string there
    # is somebody else's problem to discover.
    assert scope_of_request(Request(**{"X-Scope": value})) == "acme-tenant"


def test_the_resolver_can_be_replaced_without_touching_the_app():
    from demo.agents.master import build_master_agent
    from demo.chat_clients.fake import FakeStreamingChatClient
    from demo.server.app import create_app

    app = create_app(
        agent=build_master_agent(chat_client=FakeStreamingChatClient(chunks=["ok"])),
        scope_resolver=lambda request: "from-the-outside",
    )

    assert app.state.scope_resolver(Request()) == "from-the-outside"


def test_the_default_scope_is_the_configured_one():
    assert scope_of_request(Request()) == get_settings().default_scope
