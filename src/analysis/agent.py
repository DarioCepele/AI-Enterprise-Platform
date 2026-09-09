"""The analysis subagent: it works on the numbers it is given.

The other subagent of the laboratory reads documents; this one reads nothing.
Everything it needs arrives in the request, and its tools compute rather than
retrieve -- which is what makes routing between the two a real decision instead
of a coin toss.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Annotated, Any

from agent_framework import Agent, BaseChatClient, Content, FunctionTool, tool
from agent_framework.openai import OpenAIChatCompletionClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are an analysis agent queried by another agent.

Answer in Italian. Work only on the numbers you are given: call `measure` for a
series of values and `compare` to weigh options against each other. Never invent
data, and never carry numbers over from memory.

Say what the numbers do **not** say. A spread, a single outlier, three data
points: whoever asked will act on your answer, and the size of the evidence is
part of the answer.

Whoever queries you is not a person but another agent, which will use your
answer inside a larger job: no pleasantries, dense and short prose.

If the request does not carry the numbers to work on -- or asks for a judgement
that no measurement could settle -- reply with this single line:

[NEEDS-CLARIFICATION] <the question you would ask, in Italian>

Use it sparingly: it is a question that travels all the way up to a person."""


def _numbers(raw: str) -> list[float]:
    """Reads a series written the way a model writes one.

    A JSON list, or numbers separated by commas or spaces: accepting all three
    costs a few lines here and saves a retry every time the model chooses a
    different shape.
    """
    text = raw.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = [piece for piece in text.replace(",", " ").split() if piece]
    if isinstance(parsed, (int, float)):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"'{raw}' is not a series of numbers")

    values = []
    for item in parsed:
        try:
            values.append(float(item))
        except (TypeError, ValueError) as error:
            raise ValueError(f"'{item}' is not a number") from error
    if not values:
        raise ValueError("the series is empty")
    return values


def _far_from_the_middle(ordered: list[float], middle: float) -> list[float]:
    """Values worth naming, measured against the median and not the mean.

    A single large value drags the mean and the spread towards itself, and then
    sits inside the range it just widened -- on a short series it hides itself.
    Distance from the median, in units of the typical distance from the median,
    does not have that problem.
    """
    distances = sorted(abs(value - middle) for value in ordered)
    count = len(distances)
    typical = (
        distances[count // 2]
        if count % 2
        else (distances[count // 2 - 1] + distances[count // 2]) / 2
    )
    if not typical:
        return []
    return [value for value in ordered if abs(value - middle) / typical > 3.5]


def summary_of(values: list[float]) -> dict[str, Any]:
    """The measures, and the one caveat a small series always deserves."""
    ordered = sorted(values)
    count = len(ordered)
    mean = sum(ordered) / count
    middle = (
        ordered[count // 2]
        if count % 2
        else (ordered[count // 2 - 1] + ordered[count // 2]) / 2
    )
    spread = math.sqrt(sum((value - mean) ** 2 for value in ordered) / count)
    far = _far_from_the_middle(ordered, middle)
    return {
        "count": count,
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(mean, 4),
        "median": round(middle, 4),
        "spread": round(spread, 4),
        "outliers": far,
        "thin": count < 5,
    }


def scores_of(options: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, Any]:
    """Weighs options on the criteria given, and says how close the top two are."""
    total_weight = sum(weights.values()) or 1.0
    scored = {
        name: round(
            sum(values.get(criterion, 0.0) * weight for criterion, weight in weights.items())
            / total_weight,
            4,
        )
        for name, values in options.items()
    }
    ranking = sorted(scored, key=lambda name: scored[name], reverse=True)
    margin = (
        round(scored[ranking[0]] - scored[ranking[1]], 4) if len(ranking) > 1 else None
    )
    return {
        "scores": scored,
        "ranking": ranking,
        "margin": margin,
        # A margin this small is a tie dressed up as a winner.
        "too_close": margin is not None and margin < 0.05,
    }


def build_analysis_tools() -> list[FunctionTool]:
    @tool
    def measure(
        values: Annotated[str, "The series, as a JSON list or numbers separated by commas"],
    ) -> Content:
        """Measures a series of numbers: count, extremes, mean, median, spread, outliers."""
        try:
            summary = summary_of(_numbers(values))
        except ValueError as error:
            logger.warning("Series refused: %s", error)
            return Content.from_text(f"I cannot measure that: {error}.")
        logger.info("Series measured: %d values.", summary["count"])
        return Content.from_text(json.dumps(summary, ensure_ascii=False))

    @tool
    def compare(
        options: Annotated[
            str,
            'The options as JSON: {"a": {"cost": 3, "speed": 8}, "b": {"cost": 6, "speed": 5}}',
        ],
        weights: Annotated[str, 'The weight of each criterion as JSON: {"cost": 2, "speed": 1}'],
    ) -> Content:
        """Weighs options against each other on the given criteria, and ranks them."""
        try:
            parsed_options = json.loads(options)
            parsed_weights = json.loads(weights)
        except json.JSONDecodeError as error:
            return Content.from_text(f"I cannot read that comparison: {error}.")
        if not isinstance(parsed_options, dict) or not isinstance(parsed_weights, dict):
            return Content.from_text("Options and weights are both JSON objects.")
        if not parsed_options:
            return Content.from_text("There is nothing to compare.")

        result = scores_of(
            {
                str(name): {str(k): float(v) for k, v in (values or {}).items()}
                for name, values in parsed_options.items()
            },
            {str(name): float(weight) for name, weight in parsed_weights.items()},
        )
        logger.info("Comparison of %d options.", len(parsed_options))
        return Content.from_text(json.dumps(result, ensure_ascii=False))

    return [measure, compare]


def _default_chat_client() -> BaseChatClient:
    return OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_CHAT_COMPLETION_MODEL", "anthropic/claude-sonnet-5"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def build_analysis_agent(chat_client: BaseChatClient | None = None) -> Agent:
    return Agent(
        name="analysis",
        description="Measures and compares the numbers it is given, and says what they do not say.",
        instructions=INSTRUCTIONS,
        client=chat_client or _default_chat_client(),
        tools=build_analysis_tools(),
    )
