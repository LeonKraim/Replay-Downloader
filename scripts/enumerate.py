#!/usr/bin/env python3
"""BFS enumeration over the League player graph for SR replays in target patches.

Walks the graph: player -> match summaries (with participants) -> new players.
Collects every match whose mapId==11 (Summoner's Rift) and whose gameVersion is
in {16.13, 16.14, 16.15, 16.16} (or the --patch set) into meta/candidates.tsv.

Targeted-run niceties:
  * --patch restricts collection (and graph prioritisation) to one patch.
  * Players who appeared in a target-patch match are pushed to the FRONT of the
    frontier, so the BFS explores the 16.14-rich neighbourhood first.
  * A player's history is only paginated back as far as the earliest known
    target-patch game (plus 1-day margin) -- deeper pages cannot hold target
    games, so those requests are skipped.

Usage:
    python enumerate.py [--limit N] [--max-players M] [--patch 16.14]
                        [--seed PUUID [--seed PUUID ...]]

State is resume-able: meta/visited_puuids.txt, meta/frontier.txt and
meta/candidates.tsv are kept.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from sgp_client import SGPClient, get_rso_token  # noqa: E402

VALID_PATCHES = {"16.13", "16.14", "16.15", "16.16"}
SR_MAP_ID = 11
CANDIDATES = os.path.join(ROOT, "meta", "candidates.tsv")
VISITED = os.path.join(ROOT, "meta", "visited_puuids.txt")
FRONTIER = os.path.join(ROOT, "meta", "frontier.txt")
RESULTS_LOG = os.path.join(ROOT, "meta", "enumerate.log")
PAGE = 100  # matches requested per SUMMARY call


def load_frontier():
    if not os.path.exists(FRONTIER):
        return []
    with open(FRONTIER, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def save_frontier(frontier):
    with open(FRONTIER, "w", encoding="utf-8") as f:
        f.write("\n".join(frontier))


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2:
        return None
    return f"{parts[0]}.{parts[1]}"


def load_visited():
    if not os.path.exists(VISITED):
        return set()
    with open(VISITED, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip()}


def load_candidates():
    seen = set()
    if not os.path.exists(CANDIDATES):
        return seen
    with open(CANDIDATES, encoding="utf-8") as f:
        f.readline()  # header
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) >= 2:
                seen.add(f"{parts[0]}_{parts[1]}")
    return seen


def append_candidate(row):
    first = not os.path.exists(CANDIDATES)
    with open(CANDIDATES, "a", encoding="utf-8", newline="") as f:
        if first:
            f.write("platform\tgameId\tgameVersion\tmapId\tqueueId\tgameCreation\tgameDuration\tseed\n")
        f.write("\t".join(str(x) for x in row) + "\n")


def log(msg):
    with open(RESULTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    print(msg, flush=True)


def compute_cutoff_ms():
    """Earliest gameCreation among existing target-patch candidates, minus a
    1-day margin. Pages older than this cannot hold target games, so the
    per-player pagination can stop there."""
    cutoff = None
    if not os.path.exists(CANDIDATES):
        return None
    with open(CANDIDATES, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        if "gameVersion" not in header or "gameCreation" not in header:
            return None
        gi = header.index("gameVersion")
        ci = header.index("gameCreation")
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) <= max(gi, ci):
                continue
            patch = patch_of(parts[gi])
            if patch and patch in VALID_PATCHES:
                try:
                    c = int(parts[ci])
                except (ValueError, TypeError):
                    continue
                if cutoff is None or c < cutoff:
                    cutoff = c
    if cutoff:
        cutoff -= 86400000  # 1 day margin before the earliest known target game
    return cutoff


def player_summary(sgp, puuid, cutoff_ms=None):
    """Yield all games for a player (paginated), or [] on permanent failure.

    Pagination stops early once a page's oldest game predates cutoff_ms, since
    deeper pages are older still and cannot hold target-patch games."""
    games = []
    start = 0
    for _ in range(20):  # safety cap: at most 2000 games per player
        chunk = sgp.get_summary(puuid, start=start, count=PAGE)
        if not chunk:
            break
        games.extend(chunk)
        if len(chunk) < PAGE:
            break
        if cutoff_ms:
            oldest = None
            for g in chunk:
                c = (g.get("json") or {}).get("gameCreation")
                if isinstance(c, (int, float)):
                    oldest = c if oldest is None else min(oldest, c)
            if oldest is not None and oldest < cutoff_ms:
                break
        start += PAGE
    return games


def main():
    global VALID_PATCHES
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after collecting this many SR in-patch candidates (0 = unlimited)")
    ap.add_argument("--max-players", type=int, default=0)
    ap.add_argument("--patch", action="append", default=[],
                    help="restrict collection to these patch(es), e.g. --patch 16.14")
    ap.add_argument("--seed", action="append", default=[])
    args = ap.parse_args()

    if args.patch:
        VALID_PATCHES = set(args.patch)
    cutoff_ms = compute_cutoff_ms()

    visited = load_visited()
    seen_games = load_candidates()
    target_seen = 0  # candidates in VALID_PATCHES only (--limit counts these)
    log(f"=== enumerate start: visited={len(visited)} seen_games={len(seen_games)} "
        f"patches={sorted(VALID_PATCHES)} cutoff={cutoff_ms} ===")

    with SGPClient() as sgp:
        # seed frontier with the current summoner plus any explicit seeds
        try:
            me = get_rso_token()
            import sgp_client
            # current summoner puuid from local API
            summoner = sgp_client._local_get("/lol-summoner/v1/current-summoner")
            seeds = [summoner.get("puuid")]
        except Exception:
            seeds = []
        seeds += args.seed
        seeds = [s for s in dict.fromkeys(s for s in seeds if s)]

        # resume: reload any previously queued frontier, then prepend fresh seeds
        saved_frontier = load_frontier()
        seeds = [s for s in seeds if s and s not in visited]
        queued = set(saved_frontier)
        frontier = [s for s in saved_frontier if s not in visited] + seeds
        queued.update(seeds)
        players_seen = 0

        while frontier:
            if args.limit and target_seen >= args.limit:
                log(f"reached limit {args.limit}")
                break
            if args.max_players and players_seen >= args.max_players:
                log(f"reached max players {args.max_players}")
                break

            puuid = frontier.pop(0)
            if puuid in visited:
                continue
            visited.add(puuid)
            players_seen += 1

            try:
                games = player_summary(sgp, puuid, cutoff_ms)
            except Exception as e:
                log(f"  player {puuid[:12]} ERROR {str(e)[:100]}")
                continue

            new_players = 0
            hits = 0
            if args.limit and target_seen >= args.limit:
                log(f"reached limit {args.limit}")
                break
            for g in games:
                j = g.get("json") or {}
                match_id = g.get("match_id")
                if not match_id or "_" not in match_id:
                    continue
                platform, _, gid = match_id.partition("_")
                patch = patch_of(j.get("gameVersion"))
                target = patch in VALID_PATCHES
                # graph expansion first (cheap, no filtering). Players who
                # appeared in a target-patch match go to the FRONT of the
                # frontier so the BFS explores that neighbourhood first.
                for p in g.get("participants", []):
                    if p and p not in visited and p not in queued:
                        queued.add(p)
                        if target:
                            frontier.insert(0, p)
                        else:
                            frontier.append(p)
                        new_players += 1
                # filter
                if j.get("mapId") != SR_MAP_ID:
                    continue
                if patch not in VALID_PATCHES:
                    continue
                # drop remakes / instant surrenders (sub-5-min) - poor replay data
                if (j.get("gameDuration") or 0) < 300:
                    continue
                key = f"{platform}_{gid}"
                if key in seen_games:
                    continue
                seen_games.add(key)
                target_seen += 1
                hits += 1
                append_candidate([
                    platform, gid, j.get("gameVersion"), j.get("mapId"),
                    j.get("queueId"), j.get("gameCreation"), j.get("gameDuration"),
                    puuid,
                ])
                if args.limit and target_seen >= args.limit:
                    log(f"reached limit {args.limit}")
                    break
            log(f"player {puuid[:12]} games={len(games)} new_sr={hits} "
                f"run_new={target_seen} new_players={new_players}")

            # persist visited + frontier incrementally
            with open(VISITED, "a", encoding="utf-8") as f:
                f.write(puuid + "\n")
            if players_seen % 5 == 0:
                save_frontier(frontier)

    save_frontier(frontier)
    log(f"=== enumerate done: candidates_all={len(seen_games)} new_target={target_seen} "
        f"players_visited={players_seen} ===")


if __name__ == "__main__":
    main()
