"""Command line interface for Replay Downloader.

Usage:
    replay-downloader gather <player> [--patch 16.14] [--max 500] [--out DIR]
    replay-downloader get <PLATFORM_GAMEID> [--out DIR]
    replay-downloader status [--out DIR]
    replay-downloader resolve <player>
    replay-downloader version
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .client import current_puuid
from .resolver import Resolver, ResolveError
from .state import State


def _add_common(ap):
    ap.add_argument("--out", default=os.getcwd(),
                    help="output directory (default: current directory)")
    ap.add_argument("--workers", type=int, default=4,
                    help="download workers, 1-4 (default: 4)")
    ap.add_argument("--min-gap", type=float, default=1.5,
                    help="minimum seconds between requests per worker (default: 1.5)")
    ap.add_argument("--patch", action="append", default=[],
                    help="only keep replays from this patch (repeatable), e.g. --patch 16.14")
    ap.add_argument("--map", type=int, default=0,
                    help="only keep replays for this map id, 0=any (default: 0); "
                         "11=Summoner's Rift, 12=Howling Abyss")
    ap.add_argument("--min-length", type=int, default=0,
                    help="skip replays shorter than this many seconds, 0=any "
                         "(default: 0); 300 skips remakes")


def cmd_gather(args):
    state = State(args.out)
    res = Resolver(platform=args.platform)
    seeds = []
    if args.player:
        seeds.append(res.resolve(args.player))
    for s in args.seed:
        seeds.append(res.resolve(s))
    if not seeds:
        try:
            seeds.append(current_puuid())
        except Exception:  # noqa: BLE001
            state.log("error: no player given and the current summoner could not "
                      "be read (is the client running?)")
            sys.exit(1)
        state.log(f"no player given: gathering the current summoner")
    from .collector import Gatherer
    g = Gatherer(
        state,
        patch_set=args.patch,
        map_id=args.map,
        min_length=args.min_length,
        max_replays=args.max,
        max_players=args.max_players,
        workers=args.workers,
        min_gap=args.min_gap,
        no_download=args.no_download,
    )
    g.run(seeds)


def cmd_get(args):
    state = State(args.out)
    from .single import get_replay
    res = get_replay(
        args.game,
        args.out,
        min_length=args.min_length,
        map_id=args.map,
        patch_set=args.patch or None,
        workers=args.workers,
        min_gap=args.min_gap,
    )
    if res["status"] == "ok":
        print(f"OK  {res['path']}  patch={res['patch']} len={res['length_s']}s "
              f"{res['size']} bytes")
    elif res["status"] == "already-downloaded":
        print(f"already downloaded: {res['path']}")
    elif res["status"] == "unavailable":
        print(f"unavailable: {res['match']} returned no replay")
        sys.exit(2)
    else:  # excluded
        print(f"excluded ({res['reason']}): {res['match']}")
        sys.exit(3)


def cmd_status(args):
    state = State(args.out)
    done, pending, total, n_excl = state.count()
    on_disk = 0
    if os.path.isdir(state.downloads):
        on_disk = len([f for f in os.listdir(state.downloads) if f.endswith(".rofl")])
    print(f"output dir : {os.path.abspath(args.out)}")
    print(f"replays    : {done}")
    print(f"files on disk: {on_disk}")
    print(f"pending    : {pending}")
    print(f"candidates : {total}")
    print(f"excluded   : {n_excl}")
    # per-patch breakdown
    if os.path.exists(state.candidates):
        patches = set()
        with open(state.candidates, encoding="utf-8") as f:
            header = f.readline().rstrip("\n").split("\t")
            gi = header.index("gameVersion") if "gameVersion" in header else None
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if gi is not None and gi < len(p):
                    patches.add(patch_of(p[gi]))
        if patches and patches != {None}:
            print("patches    : " + ", ".join(sorted(x for x in patches if x)))


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def cmd_resolve(args):
    res = Resolver(platform=args.platform)
    print(res.resolve(args.player))


def cmd_version(args):
    print(f"replay-downloader v{__version__}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="replay-downloader",
        description="Replay Downloader - League of Legends. Collects League "
                    "of Legends replay files. Requires the League client to "
                    "be running.",
    )
    ap.add_argument("--platform", default=None,
                    help="platform for name resolution, e.g. EUW1 (default: EUW1)")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    p_gather = sub.add_parser("gather", help="spiral from a player and download replays")
    p_gather.add_argument("player", nargs="?", default=None,
                          help="summoner name, Riot ID (Name#TAG), or PUUID; "
                               "defaults to the current summoner")
    p_gather.add_argument("--seed", action="append", default=[],
                          help="extra seed player (repeatable)")
    p_gather.add_argument("--max", type=int, default=0,
                          help="stop after this many valid replays, 0=unlimited (default: 0)")
    p_gather.add_argument("--max-players", type=int, default=0,
                          help="walk at most this many players, 0=unlimited (default: 0)")
    p_gather.add_argument("--no-download", action="store_true",
                          help="enumerate only; do not download")
    _add_common(p_gather)
    p_gather.set_defaults(func=cmd_gather)

    p_get = sub.add_parser("get", help="download one specific replay by game ID")
    p_get.add_argument("game", help="game ID, e.g. EUW1_7923052589 or EUW1-7923052589")
    _add_common(p_get)
    p_get.set_defaults(func=cmd_get)

    p_status = sub.add_parser("status", help="show gather progress")
    p_status.add_argument("--out", default=os.getcwd(),
                          help="output directory (default: current directory)")
    p_status.set_defaults(func=cmd_status)

    p_resolve = sub.add_parser("resolve", help="resolve a player reference to a PUUID")
    p_resolve.add_argument("player", help="summoner name, Riot ID, or PUUID")
    p_resolve.set_defaults(func=cmd_resolve)

    p_ver = sub.add_parser("version", help="show the version")
    p_ver.set_defaults(func=cmd_version)

    args = ap.parse_args(argv)
    if not getattr(args, "command", None):
        ap.print_help()
        sys.exit(1)
    try:
        args.func(args)
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
