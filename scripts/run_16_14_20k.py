#!/usr/bin/env python3
"""Driver: collect 20,000 Summoner's Rift .rofl files from patch 16.14 only.

Alternates enumeration batches (16.14-targeted BFS) and download batches until
TARGET 16.14 files are on disk, or discovery/quality plateaus. All state lives
in meta/ (candidates.tsv, excluded.tsv, visited_puuids.txt, frontier.txt), so
every phase is resume-safe and the driver can be killed and restarted freely.

Pacing (user-approved for this run): 4 download workers, >=1.5s gap per worker.

Run from the replay_collection/ root:
    python scripts/run_16_14_20k.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
DOWNLOADS = os.path.join(ROOT, "downloads")
META = os.path.join(ROOT, "meta")
CANDIDATES = os.path.join(META, "candidates.tsv")
EXCLUDED = os.path.join(META, "excluded.tsv")
LOG = os.path.join(META, "driver_16_14_20k.log")

TARGET = 20000      # total 16.14 .rofl files on disk
BATCH = 800         # candidates/downloads processed per cycle
ENUM_PLAYERS = 250  # max players visited per enumerate batch (bounds runtime)
WORKERS = 4         # download workers (hard-capped at 4 in download_parallel)
MIN_GAP = 1.5       # seconds between requests, per worker
STALL_LIMIT = 8     # consecutive cycles without progress before giving up


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def load_rows():
    rows = []
    if not os.path.exists(CANDIDATES):
        return rows
    with open(CANDIDATES, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        gi = header.index("gameVersion")
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            if gi < len(parts) and patch_of(parts[gi]) == "16.14":
                rows.append((parts[0], parts[1]))
    return rows


def load_excluded():
    ex = set()
    if not os.path.exists(EXCLUDED):
        return ex
    with open(EXCLUDED, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) >= 2:
                ex.add(f"{parts[0]}_{parts[1]}")
    return ex


def count_state():
    rows = load_rows()
    excl = load_excluded()
    done, pending = 0, 0
    for platform, gid in rows:
        key = f"{platform}_{gid}"
        if key in excl:
            continue
        if os.path.exists(os.path.join(DOWNLOADS, f"{platform}-{gid}.rofl")):
            done += 1
        else:
            pending += 1
    return done, pending, len(rows), len(excl)


def run(cmd, label):
    log(f"--- {label}: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        log(f"!! {label} exited {r.returncode}")
    return r.returncode


def main():
    log(f"=== 16.14 20k driver start: target={TARGET} workers={WORKERS} "
        f"min_gap={MIN_GAP} batch={BATCH} ===")
    prev_total = None
    prev_done = None
    enum_stall = 0
    dl_stall = 0
    while True:
        done, pending, total_cand, n_excl = count_state()
        log(f"status: done={done} pending={pending} "
            f"total_candidates={total_cand} excluded={n_excl}")
        if done >= TARGET:
            log(f"=== REACHED TARGET: {done} 16.14 files on disk ===")
            break

        # plateau guards
        if prev_total is not None and total_cand == prev_total:
            enum_stall += 1
        else:
            enum_stall = 0
        prev_total = total_cand
        if prev_done is not None and done == prev_done and pending >= BATCH:
            dl_stall += 1
        else:
            dl_stall = 0
        prev_done = done
        if enum_stall >= STALL_LIMIT:
            log(f"=== PLATEAU: no new 16.14 candidates for {enum_stall} cycles "
                f"(discovery exhausted). done={done} ===")
            break
        if dl_stall >= STALL_LIMIT:
            log(f"=== PLATEAU: {dl_stall} download cycles with zero new files "
                f"(all candidates excluded/unavailable). done={done} ===")
            break

        if pending < BATCH:
            # collect up to BATCH new 16.14 candidates this run (--limit counts
            # new target candidates per run), bounded by ENUM_PLAYERS players.
            run([sys.executable, os.path.join(SCRIPTS, "enumerate.py"),
                 "--limit", str(BATCH),
                 "--max-players", str(ENUM_PLAYERS),
                 "--patch", "16.14"], "enumerate")
        else:
            run([sys.executable, os.path.join(SCRIPTS, "download_parallel.py"),
                 "--patch", "16.14",
                 "--max", str(min(pending, BATCH)),
                 "--workers", str(WORKERS),
                 "--min-gap", str(MIN_GAP)], "download")
        time.sleep(5)
    log("=== driver exit ===")


if __name__ == "__main__":
    main()
