"""Build three self-contained main.py files for an organizer's comparison."""

import argparse
import json
from pathlib import Path
import zipfile


VARIANTS = {"depth1": (1, False), "depth1_timed": (1, True), "depth2": (2, False)}


def build(destination):
    root = Path(__file__).resolve().parent.parent
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    engine = (root / "ottv2/engine.py").read_text(encoding="utf-8")
    search = (root / "ottv2/search_bot.py").read_text(encoding="utf-8")
    # Embed trusted rules/search helpers; students still receive one main.py.
    search = search[:search.index("def search_move(")]
    search = "\n".join(line for line in search.splitlines() if not line.startswith("from ."))
    entry = '''
import json
import sys
import random

def choose(state):
    started_cpu, started_wall = process_time(), monotonic()
    game = Game({p: Piece(**v) for p, v in state["board"].items()},
                state["goals"], set(state["bombs"]), set(state["obstacles"]), state["turn"])
    moves = sorted(game.legal_moves(), key=lambda m: _move_priority(game, m), reverse=True)
    if not moves:
        own = next(p for p, v in game.board.items() if v.side == game.turn)
        return {"from": own, "to": own}
    selected = moves[0]
    timing = state.get("time", {})
    remaining_cpu = timing.get("cpu_remaining_seconds", {}).get(state["you"], 10.0)
    remaining_wall = timing.get("response_remaining_seconds", 2.0)
    cpu_slice = min(0.08, max(0.0, remaining_cpu - 0.02) / 20)
    wall_slice = min(0.2, max(0.0, remaining_wall - 0.05))
    def check():
        if TIMED and (process_time() - started_cpu >= cpu_slice or
                      monotonic() - started_wall >= wall_slice):
            raise _SearchLimit("time")
    try:
        selected, _ = _search_depth(game, moves, DEPTH, random.Random(1), check)
    except _SearchLimit:
        pass
    return selected

def main():
    print(json.dumps({"protocol_version": 1, "type": "ready"}), flush=True)
    for line in sys.stdin:
        print(json.dumps(choose(json.loads(line))), flush=True)

if __name__ == "__main__":
    main()
'''
    for name, (depth, timed) in VARIANTS.items():
        folder = destination / name
        folder.mkdir()
        source = engine + "\n" + search + f"\nDEPTH = {depth}\nTIMED = {timed!r}\n" + entry
        (folder / "main.py").write_text(source, encoding="utf-8")
        with zipfile.ZipFile(destination / f"{name}.zip", "x", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("main.py", source)
    return {name: {"depth": depth, "time_aware": timed} for name, (depth, timed) in VARIANTS.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
