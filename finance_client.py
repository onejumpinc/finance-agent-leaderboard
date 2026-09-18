#!/usr/bin/env python3
"""Compatibility client for the registered FinanceBusters green image.

The image predates the current AgentBeats client contract in two ways:

* it reads ``num_tasks`` and ``white_address`` at the top level of the A2A
  message rather than from ``EvalRequest.config.args``; and
* it returns the aggregate metric as a Message text part, not a Task artifact.

Fail closed if the requested number of public tasks was not evaluated.  This
prevents a one-question or empty result from being presented as a full run.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any


def load_request(path: Path) -> tuple[str, dict[str, str], dict[str, Any], int]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    green = data.get("green_agent") or {}
    endpoint = str(green.get("endpoint") or "")
    if not endpoint:
        raise ValueError("green_agent.endpoint is required")

    participants: dict[str, str] = {}
    role_endpoints: dict[str, str] = {}
    for item in data.get("participants") or []:
        role = str(item.get("role") or "")
        agent_id = str(item.get("agentbeats_id") or "")
        participant_endpoint = str(item.get("endpoint") or "")
        if role and agent_id:
            participants[role] = agent_id
        if role and participant_endpoint:
            role_endpoints[role] = participant_endpoint

    config = data.get("config") or {}
    args = config.get("args") or {}
    expected_tasks = int(args.get("num_tasks") or 0)
    if expected_tasks < 1:
        raise ValueError("config.args.num_tasks must be at least 1")
    white_address = str(args.get("white_address") or "")
    if not white_address and len(role_endpoints) == 1:
        white_address = next(iter(role_endpoints.values()))
    if not white_address:
        raise ValueError("config.args.white_address is required")

    # This is the historical message shape implemented by the registered
    # green image's src.server.GreenRequestHandler.
    payload = {
        "num_tasks": expected_tasks,
        "white_address": white_address,
    }
    return endpoint, participants, payload, expected_tasks


def validate_result(result: Any, expected_tasks: int) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("green agent returned a non-object result")
    actual_tasks = int(result.get("total_tasks") or 0)
    if actual_tasks != expected_tasks:
        raise ValueError(
            f"green agent evaluated {actual_tasks} tasks; expected {expected_tasks}"
        )
    if result.get("metric") != "accuracy":
        raise ValueError(f"unexpected metric: {result.get('metric')!r}")
    return result


async def run(scenario_path: Path, output_path: Path) -> None:
    endpoint, participants, payload, expected_tasks = load_request(scenario_path)

    # Import inside the runtime path: the agentbeats-client image provides the
    # package, while the pure parsing/validation functions remain locally
    # testable without that image.
    import agentbeats.client as client_module

    client_module.DEFAULT_TIMEOUT = float(
        os.getenv("FINANCE_ASSESSMENT_TIMEOUT_SECONDS", "14400")
    )
    response = await client_module.send_message(
        json.dumps(payload), endpoint, streaming=False
    )
    raw = str(response.get("response") or "").strip()
    if not raw:
        raise ValueError("green agent returned an empty response")
    result = validate_result(json.loads(raw), expected_tasks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"participants": participants, "results": [result]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2), flush=True)
    print(f"Results written to {output_path}", flush=True)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: finance_client.py SCENARIO OUTPUT", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2])))
    except Exception as exc:
        print(f"Finance assessment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
