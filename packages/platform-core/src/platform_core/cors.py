"""Which pages may read a service from a browser, with or without cookies.

The browser-facing services sit on origins other than the page's, so every
call the page makes is cross-origin. By default no cookie goes along. A
deployment that puts a cookie-based proxy in front (single sign-on, a load
balancer's session cookie) needs the opposite: the page sends credentials, and
the service must say it accepts them -- for named origins only.

The one combination refused is a wildcard with credentials: Starlette then
answers every caller by echoing its `Origin`, so any site the user has open
could call the service with the user's cookies and read the answer.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict


class CORSOptions(TypedDict):
    """The keyword arguments of Starlette's `CORSMiddleware` set here."""

    allow_origins: list[str]
    allow_methods: list[str]
    allow_headers: list[str]
    allow_credentials: bool


def cors_options(
    origins: Iterable[str],
    *,
    credentials: bool = False,
    methods: Iterable[str] = ("GET", "POST"),
    headers: Iterable[str] = ("Content-Type",),
) -> CORSOptions:
    """Options for Starlette's `CORSMiddleware`.

    Raises `ValueError` for credentials with a wildcard origin (see the module).
    """
    allowed = list(origins)
    if credentials and "*" in allowed:
        raise ValueError(
            "CORS credentials need the origins named: '*' would let any site "
            "call this service with the user's cookies."
        )
    return {
        "allow_origins": allowed,
        "allow_methods": list(methods),
        "allow_headers": list(headers),
        "allow_credentials": credentials,
    }
