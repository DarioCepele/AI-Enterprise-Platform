"""Starting a durable process, and reading where it got to.

The agent holds a conversation; a process outlives it. These two tools are the
whole seam between the two: the agent can set one going and can look at it, and
that is deliberately all -- answering a clarification or approving a step is
something a **person** does, in the panel, because those are the moments the
process stopped to ask somebody.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

import httpx
from agent_framework import Content, FunctionTool, tool
from agent_framework.ag_ui import state_update

logger = logging.getLogger(__name__)

TIMEOUT = 15.0

# What each state means to whoever reads the answer. The service says the same
# thing in the panel; here it has to fit in a sentence the model can pass on.
MEANS = {
    "pending": "not started yet",
    "running": "running",
    "waiting": "waiting for a remote agent",
    "waiting_human": "stopped: it is waiting for an answer from a person",
    "waiting_approval": "stopped: it is waiting for a decision from a person",
    "completed": "finished",
    "failed": "failed",
    "rejected": "refused by whoever had to decide",
    "escalated": "handed on, because nobody answered in time",
    "compensated": "undone: it stopped, and what had been done was taken back",
}


def build_process_tools(service_url: str, scope: str) -> list[FunctionTool]:
    """The tools that talk to the process service. None, when there is none."""
    if not service_url:
        return []

    base = service_url.rstrip("/")
    headers = {"X-Process-Scope": scope} if scope else {}

    async def call(method: str, path: str, payload: Any = None) -> Any:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            response = await http.request(
                method, f"{base}{path}", json=payload, headers=headers
            )
        if response.status_code >= 400:
            said = _detail(response)
            raise RuntimeError(said)
        return response.json()

    @tool
    async def list_processes() -> Content:
        """Lists the processes that can be started, with their versions.

        Use it before starting one, so the id is the service's and not a guess.
        """
        try:
            found = await call("GET", "/processes")
        except Exception as error:
            logger.warning("Process catalogue unreachable: %s", error)
            return Content.from_text(f"I could not read the process catalogue: {error}")

        processes = found.get("processes", [])
        if not processes:
            return Content.from_text("No process is defined in the service.")
        listing = ", ".join(
            f"{item['id']}@{item['version']} ({item['steps']} steps)"
            for item in processes
        )
        logger.info("Process catalogue: %d definitions.", len(processes))
        return Content.from_text(f"Processes that can be started: {listing}.")

    @tool
    async def start_process(
        process_id: Annotated[str, "The process id, as the catalogue lists it"],
        input_json: Annotated[
            str, 'The data the process works on, as a JSON object: {"amount": 25000}'
        ] = "{}",
    ) -> Content:
        """Starts a durable process and returns its instance.

        The process goes on **without this conversation**: it can take hours,
        stop in front of a person, and finish long after the browser is closed.
        Tell the user what was started and where to follow it, instead of
        waiting for it here.
        """
        try:
            payload = json.loads(input_json or "{}")
        except json.JSONDecodeError as error:
            return Content.from_text(f"The input is not valid JSON: {error}")
        if not isinstance(payload, dict):
            return Content.from_text("The input of a process is a JSON object.")

        try:
            instance = await call(
                "POST", f"/processes/{process_id}/instances", {"input": payload}
            )
        except Exception as error:
            logger.warning("Process '%s' not started: %s", process_id, error)
            return Content.from_text(f"I could not start '{process_id}': {error}")

        logger.info(
            "Instance %s of %s started from the conversation.",
            instance["id"][:8],
            instance["process_id"],
        )
        return state_update(
            text=(
                f"I started '{instance['process_id']}' "
                f"(version {instance['process_version']}), instance {instance['id']}. "
                "It goes on by itself: the Instances panel follows it, and it is where "
                "you answer if it stops to ask something."
            ),
            state={
                "process_instance": {
                    "id": instance["id"],
                    "process_id": instance["process_id"],
                    "status": instance["status"],
                }
            },
        )

    @tool
    async def process_status(
        instance_id: Annotated[str, "The instance id returned when it was started"],
    ) -> Content:
        """Says where an instance got to, and what it is waiting for.

        Use it when the user asks about a process that was started: the answer
        comes from the service, never from what was said earlier in the
        conversation.
        """
        try:
            instance = await call("GET", f"/instances/{instance_id}")
        except Exception as error:
            logger.warning("Instance %s unreadable: %s", instance_id[:8], error)
            return Content.from_text(f"I could not read the instance: {error}")

        status = instance["status"]
        lines = [
            f"Instance {instance['id']} of '{instance['process_id']}': "
            f"{MEANS.get(status, status)}."
        ]
        if instance.get("note"):
            lines.append(str(instance["note"]))

        waiting = [
            step
            for step in instance.get("steps", [])
            if step["status"] in ("waiting_human", "waiting_approval")
        ]
        for step in waiting:
            question = step.get("question") or "no question given"
            lines.append(f"Step '{step['step_id']}' is waiting: {question}")
        if waiting:
            # The agent must not answer in the user's place, and must not
            # pretend it can: this is a person's decision, in the panel.
            lines.append(
                "Answering or approving is up to a person, in the Instances panel: "
                "pass the question on instead of deciding."
            )

        done = sum(
            1 for step in instance.get("steps", []) if step["status"] == "completed"
        )
        lines.append(f"{done} of {len(instance.get('steps', []))} steps completed.")
        logger.info("Instance %s read: %s.", instance_id[:8], status)
        return Content.from_text("\n".join(lines))

    return [list_processes, start_process, process_status]


def _detail(response: httpx.Response) -> str:
    """What the service refused, in its own words when it gave them."""
    try:
        said = response.json()
    except ValueError:
        return f"{response.status_code} {response.reason_phrase}"
    if isinstance(said, dict) and isinstance(detail := said.get("detail"), str):
        return detail
    return f"{response.status_code} {response.reason_phrase}"
