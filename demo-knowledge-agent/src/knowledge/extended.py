"""The extended card: what it adds to the public one, and who may see it.

Who may see it is the platform's rule (`platform_core.service_token`); what it
adds -- the catalogue of indexed documents -- is this agent's own business.
"""

from __future__ import annotations

from collections.abc import Sequence

from a2a.types import AgentCard, AgentSkill
from platform_core.service_token import SCHEME, ServiceToken

__all__ = [
    "SCHEME",
    "TOKEN",
    "ServiceTokenOnly",
    "build_extended_card",
    "card_for_the_caller",
    "catalogue_skill",
]

TOKEN = ServiceToken("KNOWLEDGE_SERVICE_TOKEN")
ServiceTokenOnly = TOKEN.middleware()
card_for_the_caller = TOKEN.card_modifier()


def catalogue_skill(documents: Sequence[str]) -> AgentSkill:
    return AgentSkill(
        id="catalogue",
        name="Document catalogue",
        description=(
            "Lists the indexed documents and allows citing them by name: "
            + ", ".join(documents)
        ),
        tags=["catalogue", "internal"],
    )


def build_extended_card(public: AgentCard, documents: Sequence[str]) -> AgentCard:
    """The public card plus what does not go in the shop window.

    Which documents we have indexed tells an onlooker what the organization
    works on: exactly the kind of detail that helps whoever has to use the
    agent, and not whoever happens to walk past.
    """
    extended = AgentCard()
    extended.CopyFrom(public)
    extended.description = (
        f"{public.description} Extended view: includes the indexed catalogue."
    )
    extended.skills.append(catalogue_skill(documents))
    return extended
