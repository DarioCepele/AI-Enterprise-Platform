"""Secrets that fail closed.

A signing key with a default is a signing key everybody knows. The rule here:
a secret that is required and empty stops the service at startup, with the
name of the variable to set; a secret that still holds a well-known
placeholder is accepted -- a laboratory has to start -- and said out loud.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIN_LENGTH = 16

# The values the example environments ship with: fine to start a laboratory,
# never fine anywhere else.
PLACEHOLDER_MARKERS = ("cambiami", "change-me", "changeme", "laboratory-without")


class MissingSecretError(ValueError):
    """A required secret is empty or unusable."""


def require_secret(variable: str, value: str, *, required: bool = True) -> str:
    """The secret, checked: empty and required is fatal, a placeholder is loud."""
    secret = value.strip()
    if not secret:
        if required:
            raise MissingSecretError(
                f"{variable} is empty: set it to a random value of at least "
                f"{MIN_LENGTH} characters (for example `openssl rand -hex 32`)."
            )
        return ""
    if len(secret) < MIN_LENGTH:
        raise MissingSecretError(
            f"{variable} is shorter than {MIN_LENGTH} characters: a short key is "
            "a guessable key."
        )
    if any(marker in secret.lower() for marker in PLACEHOLDER_MARKERS):
        logger.warning(
            "%s still holds the example placeholder: acceptable on a laptop, "
            "never in a shared environment.",
            variable,
        )
    return secret
