"""Two one-turn exchanges with trusted sample processes, not a match runner."""

import json
from pathlib import Path
import subprocess
import sys

from .engine import FormatError, IllegalMove, sample_game
from .protocol import state_message, validate_ready

ROOT = Path(__file__).resolve().parent.parent


def request_move(game, strategy):
    message = json.dumps(state_message(game)) + "\n"
    # run/communicate closes stdin and drains stdout/stderr, preventing pipe deadlock.
    # Five seconds here is only a demo wall timeout, NOT a tournament CPU budget.
    response = subprocess.run(
        [sys.executable, "-m", "ottv2.bot_process", "--strategy", strategy, "--seed", "1"],
        input=message, capture_output=True, text=True, encoding="utf-8", timeout=5, cwd=ROOT,
    )
    if response.returncode:
        raise ValueError(f"bot exited with code {response.returncode}: {response.stderr.strip()}")
    lines = response.stdout.splitlines()
    if len(lines) != 2:
        raise ValueError(f"expected READY and one move line, received {len(lines)} lines")
    validate_ready(json.loads(lines[0]))
    move = json.loads(lines[1])
    game.validate_move(move)
    return move


def main():
    game = sample_game()
    print("OTTv2 COMMUNICATION DEMO | 2 exchanges, not a full match")
    try:
        for strategy in ("random", "greedy"):
            side = game.turn
            print(f"SEND: state for {side} -> {strategy} bot")
            move = request_move(game, strategy)
            print("RECEIVE: " + json.dumps(move))
            game.play(move)
            print(f"CHECK: legal | next={game.turn}")
    except subprocess.TimeoutExpired:
        print("DEMO ERROR: bot did not finish within 5 seconds (wall time).", file=sys.stderr)
        return 1
    except (OSError, ValueError, FormatError, IllegalMove) as error:
        print(f"DEMO ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: both sample bots communicated through stdin/stdout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
