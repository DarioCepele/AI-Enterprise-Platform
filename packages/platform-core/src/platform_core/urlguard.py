"""Fetching a URL somebody else chose, without letting them choose our network.

An agent reads URLs out of conversations, web pages and other agents'
answers: every one of them is untrusted input, and the process that fetches it
sits inside the platform's network. This module follows the OWASP SSRF
prevention guidance for applications that must reach arbitrary external URLs:

- only the schemes the policy names (http and https by default);
- every address the name resolves to, A and AAAA, must be public: private,
  loopback, link-local (where cloud metadata lives), multicast, reserved and
  shared ranges are refused, IPv4 hidden inside IPv6 included;
- redirects are never followed blindly: each hop is validated again;
- the address the connection actually reached is checked before the body is
  read, which closes the DNS-rebinding window between the check and the use.

Egress rules at the network layer remain the second line: this is the first.
Hosts the operator names in `allowed_hosts` -- an internal service a tool is
meant to reach -- skip the address check, never the scheme check.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Beyond what `ipaddress` already marks as non-global.
_ALSO_BLOCKED = tuple(
    ipaddress.ip_network(network)
    for network in (
        "100.64.0.0/10",  # shared address space (carrier-grade NAT)
        "192.0.0.0/24",  # IETF protocol assignments
        "198.18.0.0/15",  # benchmarking
        "64:ff9b::/96",  # NAT64: an IPv4 address in disguise
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4: an IPv4 address in disguise
    )
)


class BlockedURL(ValueError):
    """A URL the policy refuses, with the reason in the message."""


class ResponseTooLarge(ValueError):
    """A body that crossed the ceiling while it was being read."""


@dataclass(frozen=True)
class URLPolicy:
    schemes: frozenset[str] = frozenset({"http", "https"})
    allow_private: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    max_redirects: int = 5
    # Behind an egress proxy the peer is the proxy, not the target: whoever
    # runs one turns this off and makes the proxy enforce the same rules.
    verify_peer: bool = True


def hosts_of(urls: Iterable[str]) -> frozenset[str]:
    """The host names inside a list of URLs, for `URLPolicy.allowed_hosts`."""
    hosts = set()
    for url in urls:
        try:
            host = httpx.URL(url).host
        except httpx.InvalidURL:
            continue
        if host:
            hosts.add(host.lower().rstrip("."))
    return frozenset(hosts)


def is_public(address: IPAddress) -> bool:
    """Whether an address belongs to the public internet and nowhere else."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public(address.ipv4_mapped)
        if address.teredo is not None:
            return False
    if not address.is_global or address.is_multicast:
        return False
    return not any(
        address in network
        for network in _ALSO_BLOCKED
        if network.version == address.version
    )


def _address(text: str) -> IPAddress:
    # A link-local IPv6 answer carries its zone ("fe80::1%eth0"); the zone is
    # not part of the address being judged.
    return ipaddress.ip_address(text.split("%", 1)[0])


async def resolve(host: str, port: int) -> list[IPAddress]:
    """Every address a name resolves to, IPv4 and IPv6, without duplicates."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(_address(str(info[4][0])) for info in infos))


def _host_is_allowed(host: str, policy: URLPolicy) -> bool:
    return host.lower().rstrip(".") in policy.allowed_hosts


async def check_url(url: str | httpx.URL, policy: URLPolicy | None = None) -> httpx.URL:
    """The parsed URL when the policy accepts it, `BlockedURL` when it does not."""
    rules = policy or URLPolicy()
    try:
        parsed = httpx.URL(str(url))
    except httpx.InvalidURL as error:
        raise BlockedURL(f"not a usable URL: {error}") from error
    if parsed.scheme not in rules.schemes:
        raise BlockedURL(f"scheme '{parsed.scheme}' is not allowed")
    host = parsed.host
    if not host:
        raise BlockedURL("the URL has no host")
    if parsed.userinfo:
        raise BlockedURL("credentials inside a URL are not accepted")
    if rules.allow_private or _host_is_allowed(host, rules):
        return parsed

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await resolve(host, port)
        except OSError as error:
            raise BlockedURL(f"{host} does not resolve: {error}") from error
    refused = [str(address) for address in addresses if not is_public(address)]
    if refused:
        raise BlockedURL(
            f"{host} resolves to an address that is not public ({', '.join(refused)})"
        )
    return parsed


def _check_peer(response: httpx.Response, url: httpx.URL, policy: URLPolicy) -> None:
    """The address the connection reached must pass the same test as the name did."""
    if not policy.verify_peer or policy.allow_private:
        return
    if url.host and _host_is_allowed(url.host, policy):
        return
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    try:
        server = stream.get_extra_info("server_addr")
    except Exception:
        return
    if not server:
        return
    try:
        address = _address(str(server[0]))
    except ValueError:
        return
    if not is_public(address):
        raise BlockedURL(
            f"{url.host} connected to {address}, which is not public "
            "(the name resolved differently when it was used)"
        )


@asynccontextmanager
async def open_checked(
    url: str | httpx.URL,
    policy: URLPolicy | None = None,
    *,
    client: httpx.AsyncClient,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx.Response]:
    """A streamed response for `url`, every redirect hop validated on the way.

    The body is not read: the caller decides how much of it it is willing to
    hold (see `read_limited`), and the response is closed on exit.
    """
    rules = policy or URLPolicy()
    current = await check_url(url, rules)
    for _ in range(rules.max_redirects + 1):
        request = client.build_request(method, current, headers=headers)
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            _check_peer(response, current, rules)
            if response.is_redirect and response.has_redirect_location:
                current = await check_url(
                    current.join(response.headers["location"]), rules
                )
                continue
            yield response
            return
        finally:
            await response.aclose()
    raise BlockedURL(f"more than {rules.max_redirects} redirects")


async def read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """The body, refused the moment it crosses `max_bytes`, never held beyond it."""
    size = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise ResponseTooLarge(f"body over {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
