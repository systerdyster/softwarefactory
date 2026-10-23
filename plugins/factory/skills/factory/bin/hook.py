#!/usr/bin/env python3
"""The plugin's hooks. Plugins ignore hooks in agent frontmatter, so these run for every matching
event in every session where the plugin is enabled, and return at once unless the event is the
factory's.

- PreToolUse from a factory agent (agent_type "factory:<agent name>"): that role's guard. An agent
  named like a factory agent but unknown here is blocked, so a renamed agent fails closed.
- PostToolUse of an Agent call whose prompt carries a factory run id (a brief): the agentId Claude
  Code returned is attached to that run, which is what lets the guard bind the agent to its job.
- SubagentStop of a factory agent: its run is done."""

import io
import json
import os
import sys

PREFIX = "factory:"
ROLES = {
    "factory-builder": "builder",
    "factory-tidy": "tidy",
    "factory-wiki": "wiki",
    "factory-codex-fallback": "review-codex",
    "factory-mage": "mage",
}


def role_for(agent_type):
    if not agent_type or not agent_type.startswith(PREFIX):
        return None
    return ROLES.get(agent_type[len(PREFIX):], "unknown")


def _paths():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fx.paths import Paths
    return Paths()


def _attach(event):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fx import runs
    from fx.errors import FactoryError
    run_id = runs.run_id_in((event.get("tool_input") or {}).get("prompt"))
    if not run_id:
        return 0
    agent_id = (event.get("tool_response") or {}).get("agentId")
    try:
        if not agent_id:
            raise FactoryError("the Agent call returned no agentId")
        runs.attach(_paths(), run_id, agent_id)
    except FactoryError as e:
        print(f"factory: run {run_id} could not be attached to its agent: {e}. That agent can't write; "
              f"`factory-cli run abort {run_id}` and ask state again.", file=sys.stderr)
        return 2
    return 0


def _stopped(event):
    if role_for(event.get("agent_type")) in (None, "unknown"):
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fx import runs
    from fx.errors import FactoryError
    try:
        runs.end(_paths(), agent_id=event.get("agent_id"), status="done", why="SubagentStop")
    except FactoryError:
        pass
    return 0


def main(stream=None):
    raw = (stream or sys.stdin).read()
    try:
        event = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return 0
    name = event.get("hook_event_name")
    if name == "PostToolUse":
        return _attach(event) if event.get("tool_name") in ("Agent", "Task") else 0
    if name == "SubagentStop":
        return _stopped(event)
    role = role_for(event.get("agent_type"))
    if role is None:
        return 0
    if role == "unknown":
        print(f"factory guard: {event.get('agent_type')} is not a known factory agent", file=sys.stderr)
        return 2
    from fx import guard
    from fx.errors import FactoryError
    try:
        paths = _paths()
    except FactoryError as e:
        print(f"factory guard ({role}): {e}", file=sys.stderr)
        return 2
    return guard.run(role, stream=io.StringIO(raw), paths=paths)


if __name__ == "__main__":
    sys.exit(main())
