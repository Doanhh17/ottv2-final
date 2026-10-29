"""Version 1 local messages, with optional own-turn clock snapshots."""

import math


def validate_time_info(info):
    keys = {"cpu_budget_seconds", "cpu_remaining_seconds",
            "response_limit_seconds", "response_remaining_seconds"}
    if not isinstance(info, dict) or set(info) != keys:
        raise ValueError("time must contain CPU and response budgets/remaining seconds")

    def number(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value))

    cpu_budget = info["cpu_budget_seconds"]
    response_limit = info["response_limit_seconds"]
    if not number(cpu_budget) or cpu_budget <= 0 or not number(response_limit) or response_limit <= 0:
        raise ValueError("time budgets must be positive finite seconds")
    cpu_left = info["cpu_remaining_seconds"]
    if (not isinstance(cpu_left, dict) or set(cpu_left) != {"A", "B"}
            or any(not number(value) or not 0 <= value <= cpu_budget for value in cpu_left.values())):
        raise ValueError("time CPU remaining must include A/B within the CPU budget")
    response_left = info["response_remaining_seconds"]
    if not number(response_left) or not 0 <= response_left <= response_limit:
        raise ValueError("time response remaining must be within the response limit")
    return {"cpu_budget_seconds": cpu_budget, "cpu_remaining_seconds": dict(cpu_left),
            "response_limit_seconds": response_limit, "response_remaining_seconds": response_left}


def ready_message():
    return {"protocol_version": 1, "type": "ready"}


def validate_ready(message):
    if (not isinstance(message, dict)
            or set(message) != {"protocol_version", "type"}
            or type(message["protocol_version"]) is not int
            or message["protocol_version"] != 1
            or message["type"] != "ready"):
        raise ValueError("expected READY: protocol_version=1, type=ready")


def state_message(game, time_info=None):
    message = {
        "protocol_version": 1,
        "type": "state",
        "you": game.turn,
        "turn": game.turn,
        "board": {pos: {"side": piece.side, "kind": piece.kind}
                  for pos, piece in sorted(game.board.items())},
        "goals": dict(game.goals),
        "bombs": sorted(game.bombs),
        "obstacles": sorted(game.obstacles),
    }
    if time_info is not None:
        message["time"] = validate_time_info(time_info)
    return message
