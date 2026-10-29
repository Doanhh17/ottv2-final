"""Run trusted examples to exercise rules; no tournament time accounting."""

import argparse
import random

from .bots import greedy_bot, random_bot
from .engine import sample_game


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    game = sample_game(first=rng.choice(("A", "B")))
    print(f"OTTv2 LOCAL DEMO | seed={args.seed} | first={game.turn}")
    print("A=random, B=greedy | clocks/sandbox: not implemented")
    for number in range(1, 1001):
        chooser = random_bot if game.turn == "A" else greedy_bot
        move = chooser(game, rng)
        if move is None:
            print("DEMO STOP: no legal move; Worker must retry until timeout.")
            return
        event = game.play(move)
        extra = " BOMB" if event["bomb"] else ""
        if event["capture"]:
            extra += " CAPTURE=" + event["capture"]
        print(f"{number:03d} {event['side']}: {move['from']} -> {move['to']}{extra}")
        if game.result is not None:
            print(f"RESULT: {game.result} | reason={game.reason} | moves={len(game.history)}")
            return
    # Safety stop for this demonstration, never a game draw rule.
    print("DEMO STOP: 1000 moves reached; no tournament result declared.")


if __name__ == "__main__":
    main()
