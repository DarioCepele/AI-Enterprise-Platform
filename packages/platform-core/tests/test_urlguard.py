"""The SSRF guard: what it refuses, and the tricks it does not fall for."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from platform_core.urlguard import (
    BlockedURL,
    ResponseTooLarge,
    URLPolicy,
    check_url,
    hosts_of,
    is_public,
    open_checked,
    read_limited,
)


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # shared address space
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:10.0.0.1",  # IPv4 hidden in IPv6
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "2002:a00:1::1",  # 6to4 of 10.0.0.1
    ],
)
def test_non_public_addresses_are_refused(address):
    assert is_public(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", ["93.184.215.14", "1.1.1.1", "2606:4700::1111"])
def test_public_addresses_pass(address):
    assert is_public(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "http://127.0.0.1:8100/threads",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/logs",
        "http://user:secret@93.184.215.14/",
        "not a url at all",
    ],
)
async def test_urls_that_reach_inside_or_smuggle_are_blocked(url):
    with pytest.raises(BlockedURL):
        await check_url(url)


async def test_a_public_literal_passes():
    parsed = await check_url("https://93.184.215.14/video.mp4")
    assert parsed.host == "93.184.215.14"


async def test_hosts_the_operator_names_are_reachable():
    policy = URLPolicy(allowed_hosts=hosts_of(["http://master-agent:8000/uploads/"]))
    parsed = await check_url("http://master-agent:8000/uploads/abc", policy)
    assert parsed.host == "master-agent"


async def test_allowed_hosts_never_widen_the_schemes():
    policy = URLPolicy(allowed_hosts=frozenset({"master-agent"}))
    with pytest.raises(BlockedURL):
        await check_url("file://master-agent/etc/passwd", policy)


def _redirecting_to(location: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.215.14":
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, content=b"internal secrets")

    return httpx.MockTransport(handler)


async def test_a_redirect_into_the_network_is_blocked():
    transport = _redirecting_to("http://169.254.169.254/latest/meta-data/")
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BlockedURL):
            async with open_checked("http://93.184.215.14/", client=client):
                pass


async def test_a_redirect_to_a_public_host_is_followed():
    transport = _redirecting_to("http://1.1.1.1/final")
    async with (
        httpx.AsyncClient(transport=transport) as client,
        open_checked("http://93.184.215.14/", client=client) as response,
    ):
        assert str(response.url) == "http://1.1.1.1/final"
        assert await read_limited(response, 1024) == b"internal secrets"


async def test_endless_redirects_stop():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://93.184.215.14/again"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedURL, match="redirects"):
            async with open_checked(
                "http://93.184.215.14/", URLPolicy(max_redirects=2), client=client
            ):
                pass


class _Stream:
    def __init__(self, address: str) -> None:
        self._address = address

    def get_extra_info(self, name: str) -> object:
        return (self._address, 80) if name == "server_addr" else None


async def test_a_name_that_rebinds_to_a_private_address_is_caught_at_connect():
    # The name passed the check, but the connection landed inside: DNS rebinding.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x", extensions={"network_stream": _Stream("10.0.0.7")}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedURL, match=r"connected to 10\.0\.0\.7"):
            async with open_checked("http://93.184.215.14/", client=client):
                pass


async def test_a_body_over_the_ceiling_is_refused_while_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"a" * 2048)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        open_checked("http://93.184.215.14/", client=client) as response,
    ):
        with pytest.raises(ResponseTooLarge):
            await read_limited(response, 1024)
