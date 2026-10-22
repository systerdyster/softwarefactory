#!/usr/bin/env python3
"""The plugin's one PreToolUse hook. Plugins ignore hooks in agent frontmatter, so this hook runs
for every matching tool call in every session where the plugin is enabled. It returns at once
unless a factory agent made the call (agent_type "factory:<agent name>"), and otherwise hands the
event to that role's guard. An agent named like a factory agent but unknown here is blocked, so a
renamed agent fails closed instead of running unguarded."""

import io
import json
import os
import sys

PREFIX = "factory:"
ROLES = {
    "factory-builder": "builder",
    "factory-codex-fallback": "review-codex",
}
REVIEWER_PREFIX = "factory-review-"


def role_for(agent_type):
    if not agent_type or not agent_type.startswith(PREFIX):
        return None
    name = agent_type[len(PREFIX):]
    if name in ROLES:
        return ROLES[name]
    if name.startswith(REVIEWER_PREFIX):
        return "review-" + name[len(REVIEWER_PREFIX):]
    return "unknown"


def main(stream=None):
    raw = (stream or sys.stdin).read()
    try:
        event = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return 0
    role = role_for(event.get("agent_type"))
    if role is None:
        return 0
    if role == "unknown":
        print(f"factory guard: {event.get('agent_type')} is not a known factory agent", file=sys.stderr)
        return 2
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fx import guard
    from fx.errors import FactoryError
    from fx.paths import Paths
    try:
        paths = Paths()
    except FactoryError as e:
        print(f"factory guard ({role}): {e}", file=sys.stderr)
        return 2
    return guard.run(role, stream=io.StringIO(raw), paths=paths)


if __name__ == "__main__":
    sys.exit(main())
