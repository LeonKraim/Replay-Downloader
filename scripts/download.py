#!/usr/bin/env python3
"""Resume-able, rate-limited replay downloader for the collection.

Reads meta/candidates.tsv (platform gameId gameVersion mapId ...) and downloads
each match's replay from the SGP edge to downloads/{platform}-{gameId}.rofl.

Skips files already present and >= 1 MiB; logs failures to meta/download.log.

Usage:
    python download.py [--max N] [--skip-existing] [--min-size-bytes B]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from sgp_client import SGPClient  # noqa: E402

CANDIDATES = os.path.join(ROOT, "meta", "candidates.tsv")
DOWNLOADS = os.path.join(ROOT, "downloads")
LOG = os.path.join(ROOT, "meta", "download.log")
MIN_SIZE = 512 * 1024  # 512 KiB; real replays are 6-20 MB, this filters junk


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    print(msg, flush=True)


def load_candidates():
    rows = []
    with open(CANDIDATES, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            rows.append((parts[0], parts[1]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--min-size", type=int, default=MIN_SIZE)
    args = ap.parse_args()

    os.makedirs(DOWNLOADS, exist_ok=True)
    rows = load_candidates()
    todo = []
    for platform, gid in rows:
        out = os.path.join(DOWNLOADS, f"{platform}-{gid}.rofl")
        if os.path.exists(out) and os.path.getsize(out) >= args.min_size:
            continue
        todo.append((platform, gid, out))

    log(f"=== download start: candidates={len(rows)} todo={len(todo)} ===")
    if args.max:
        todo = todo[: args.max]

    ok = fail = 0
    with SGPClient() as sgp:
        for platform, gid, out in todo:
            match_id = f"{platform}_{gid}"
            try:
                success = sgp.download_replay(match_id, out)
                if success:
                    size = os.path.getsize(out)
                    if size >= args.min_size:
                        ok += 1
                        log(f"  OK  {match_id} {size} bytes")
                    else:
                        os.remove(out)
                        fail += 1
                        log(f"  TOO-SMALL {match_id} {size}")
                else:
                    fail += 1
                    log(f"  UNAVAILABLE {match_id}")
            except Exception as e:
                fail += 1
                log(f"  ERR {match_id} {str(e)[:120]}")
    log(f"=== download done: ok={ok} fail={fail} ===")


if __name__ == "__main__":
    main()
