"""The extended card: what it adds to the public one, and who may see it.

Who may see it is the platform's rule (`platform_core.service_token`); what it
adds -- exactly which measures are computed -- is this agent's own business.
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
    "methods_skill",
]

TOKEN = ServiceToken("ANALYSIS_SERVICE_TOKEN")
ServiceTokenOnly = TOKEN.middleware()
card_for_the_caller = TOKEN.card_modifier()


def methods_skill(measures: Sequence[str]) -> AgentSkill:
    return AgentSkill(
        id="methods",
        name="How it measures",
        description=(
            "The measures this agent computes, so a caller can ask for them by name: "
            + ", ".join(measures)
        ),
        tags=["methods", "internal"],
    )


def build_extended_card(public: AgentCard, measures: Sequence[str]) -> AgentCard:
    """The public card plus what does not go in the shop window.

    Knowing exactly which measures are computed -- and where the agent decides
    that a series is too thin or two options too close -- is what lets a caller
    trust a number. It helps whoever has to use the agent, and not whoever
    happens to walk past.
    """
    extended = AgentCard()
    extended.CopyFrom(public)
    extended.description = (
        f"{public.description} Extended view: includes the measures it computes."
    )
    extended.skills.append(methods_skill(measures))
    return extended
