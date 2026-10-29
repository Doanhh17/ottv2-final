"""Validate local JSON maps before any bot starts."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .engine import Game, KINDS, Piece, sample_game
from .protocol import state_message

MAX_MAP_BYTES = 65536


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate map key: {key}")
        result[key] = value
    return result


def load_map(path, first="A"):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_MAP_BYTES + 1)
    if len(data) > MAX_MAP_BYTES:
        raise ValueError("map exceeds 65536 bytes")
    try:
        obj = json.loads(data.decode("utf-8-sig"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("map needs valid UTF-8 JSON") from error
    keys = {"schema_version", "name", "board", "goals", "bombs", "obstacles"}
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ValueError("map needs exactly schema_version/name/board/goals/bombs/obstacles")
    if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise ValueError("unsupported map schema_version")
    if not isinstance(obj["name"], str) or not obj["name"].strip() or len(obj["name"]) > 80:
        raise ValueError("map name must contain 1..80 characters")
    if not isinstance(obj["board"], dict) or len(obj["board"]) != 18:
        raise ValueError("map must start with 18 pieces")
    pieces = {}
    for pos, piece in obj["board"].items():
        if (not isinstance(piece, dict) or set(piece) != {"side", "kind"}
                or piece["side"] not in ("A", "B") or piece["kind"] not in KINDS):
            raise ValueError("map piece needs valid side and kind")
        pieces[pos] = Piece(**piece)
    for side in ("A", "B"):
        if Counter(p.kind for p in pieces.values() if p.side == side) != Counter({k: 3 for k in KINDS}):
            raise ValueError("each side needs 3 rock, 3 paper, 3 scissors")
    if (not isinstance(obj["goals"], dict) or set(obj["goals"]) != {"A", "B"}
            or any(not isinstance(v, str) for v in obj["goals"].values())):
        raise ValueError("map needs goals A/B as coordinates")
    for field in ("bombs", "obstacles"):
        values = obj[field]
        if (not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                or len(values) != len(set(values))):
            raise ValueError(f"map {field} needs a list of distinct coordinates")
    game = Game(pieces, obj["goals"], set(obj["bombs"]), set(obj["obstacles"]), first)
    return game, {"name": obj["name"], "sha256": hashlib.sha256(data).hexdigest()}


def write_example(path):
    state = state_message(sample_game())
    obj = {"schema_version": 1, "name": "nine-piece-example",
           **{k: state[k] for k in ("board", "goals", "bombs", "obstacles")}}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--write-example", action="store_true")
    args = parser.parse_args()
    try:
        if args.write_example:
            write_example(args.path)
        game, meta = load_map(args.path)
        print(f"MAP OK: {meta['name']} | pieces={len(game.board)} | sha256={meta['sha256']}")
    except (ValueError, OSError) as error:
        parser.exit(1, f"MAP ERROR: {error}\n")


if __name__ == "__main__":
    main()
