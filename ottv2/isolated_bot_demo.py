"""One READY handshake and one move from the bundled random bot in Isolate."""

import json
import subprocess
import sys

from .engine import sample_game
from .isolated_session import BOX_ROOT, IsolatedSampleSession

BOX = BOX_ROOT / "0"


def run_demo(emit=print):
    game = sample_game()
    with IsolatedSampleSession("random", 0, seed=1) as bot:
        emit("INIT: box 0 created; bundled random bot on CPU 0")
        emit("READY: random bot is running inside Isolate")
        move = bot.request_move(game)
        emit("RECEIVE: " + json.dumps(move))
        game.play(move)
        emit(f"CHECK: legal | next={game.turn}")
    emit(f"META: session_cpu={bot.metadata.get('time', '?')}s (includes Python startup)")
    emit("CLEANUP: bot stopped; box 0 removed")
    emit("ISOLATED BOT OK: READY and one legal move inside Isolate.")
    return game


def main():
    try:
        run_demo()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ISOLATED BOT ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
