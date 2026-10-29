"""Select one validated local map independently of the public match seed."""

import argparse
import json
from pathlib import Path
import secrets

from .map_config import load_map
from .engine import sample_game
from .protocol import state_message

MAX_POOL_MAPS = 32


def choose_map(directory, first="A"):
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("map pool must be a directory")
    paths = []
    # Bound candidate count before loading file contents.
    for path in directory.iterdir():
        if path.suffix.lower() == ".json":
            if path.is_symlink() or not path.is_file():
                raise ValueError("pool entries must be regular JSON files, not links")
            paths.append(path)
            if len(paths) > MAX_POOL_MAPS:
                raise ValueError("map pool supports at most 32 maps")
    if not paths:
        raise ValueError("map pool has no JSON maps")
    candidates = []
    fingerprints = set()
    names = set()
    for path in sorted(paths):
        game, meta = load_map(path, first)
        # Name/whitespace differences must not give the same layout more weight.
        fingerprint = (game.position_key(), tuple(sorted(game.goals.items())), tuple(sorted(game.obstacles)))
        if fingerprint in fingerprints or meta["name"] in names:
            raise ValueError("map pool has duplicate layout or name")
        fingerprints.add(fingerprint)
        names.add(meta["name"])
        candidates.append((game, meta))
    game, meta = secrets.choice(candidates)
    return game, {**meta, "selection": "pool_random"}


def write_examples(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    base = state_message(sample_game())
    layouts = (("cross", ["d5", "f5"], ["e5"]),
               ("gates", ["c5", "g5"], ["e4", "e6"]),
               ("open", [], []))
    for name, bombs, obstacles in layouts:
        obj = {"schema_version": 1, "name": name, "board": base["board"],
               "goals": base["goals"], "bombs": bombs, "obstacles": obstacles}
        with (directory / f"{name}.json").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(obj, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--write-examples", action="store_true")
    args = parser.parse_args()
    try:
        if args.write_examples:
            write_examples(args.directory)
        _, meta = choose_map(args.directory)
        print(f"POOL OK: selected={meta['name']} | sha256={meta['sha256']}")
    except (ValueError, OSError) as error:
        parser.exit(1, f"POOL ERROR: {error}\n")


if __name__ == "__main__":
    main()
