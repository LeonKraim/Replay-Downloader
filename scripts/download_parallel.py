#!/usr/bin/env python3
"""Parallel resume-able, self-validating replay downloader.

Each worker owns its own SGPClient (its own Playwright browser + rate limiter).
HARD SAFETY CAP: at most 4 workers / 4 concurrent requests, and a >=1.5s minimum
gap per worker -- acquisition must not look like botting (user-approved pacing
for the 16.14 20k run; was 2 workers / 2.5s before).

Every downloaded file is re-parsed from ITS OWN BYTES before being kept. A file
is kept only if it is a valid ROFL2 with patch in {16.13-16.16} (or --patch),
mapId==11 (Summoner's Rift, inferred from its own statsJson), and
gameLength >= 300s (no remakes). Anything else is deleted and recorded in
meta/excluded.tsv so it is never re-downloaded. The queue's own duration column
is NOT trusted -- the agent-enumeration path wrote literal "None" and skipped
the duration gate.

Usage:
    python download_parallel.py [--workers 4] [--max 0] [--min-gap 1.5]
                                [--patch 16.14] [--tsv meta/candidates.tsv]
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rofl_parser  # noqa: E402
from sgp_client import SGPClient  # noqa: E402

DOWNLOADS = os.path.join(ROOT, "downloads")
LOG = os.path.join(ROOT, "meta", "download_parallel.log")
EXCLUDED = os.path.join(ROOT, "meta", "excluded.tsv")
# Floor that only excludes truncated/junk downloads. Real ROFL2s of even
# 5-minute games are >200KB; the edge occasionally returns an empty 200 body
# (transient) so the worker retries before giving up.
MIN_SIZE = 200 * 1024
VALID_PATCHES = {"16.13", "16.14", "16.15", "16.16"}
SR_MAP_ID = 11
MIN_GAME_LENGTH_S = 300

_lock = threading.Lock()
_stats = {"ok": 0, "fail": 0, "excluded": 0, "skip": 0, "in_flight": 0, "max_in_flight": 0}


def log(msg):
    with _lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        print(msg, flush=True)


def patch_of(version):
    if not version:
        return "?"
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else "?"


def load_excluded():
    """Return the set of 'PLATFORM_GID' permanently excluded games."""
    excl = set()
    if os.path.exists(EXCLUDED):
        with open(EXCLUDED, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                parts = ln.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    excl.add(f"{parts[0]}_{parts[1]}")
    return excl


def _exclude(platform, gid, reason, rec, size_bytes):
    """Record a permanently-excluded game (never re-download it)."""
    with _lock:
        first = not os.path.exists(EXCLUDED)
        with open(EXCLUDED, "a", encoding="utf-8", newline="") as f:
            if first:
                f.write("platform\tgameId\treason\tpatch\tgameLength_s\tsize_bytes\n")
            f.write(
                f"{platform}\t{gid}\t{reason}\t{patch_of(rec.get('gameVersion')) or ''}\t"
                f"{rec.get('gameLength_s') or ''}\t{size_bytes}\n"
            )


def _gate_reason(rec):
    """Return exclusion reason if the parsed file must NOT be kept, else None.

    A kept file must be a structurally valid ROFL2 whose own metadata confirms
    an allowed patch, Summoner's Rift (mapId 11), and a real game (>= 300s)."""
    if not rec.get("magic_ok") or rec.get("fileFormat") != 2:
        return "not-rofl2"
    patch = patch_of(rec.get("gameVersion"))
    if patch not in VALID_PATCHES:
        return "patch:" + str(patch)
    if rec.get("mapId_inferred") != SR_MAP_ID:
        return "not-sr"
    if (rec.get("gameLength_s") or 0) < MIN_GAME_LENGTH_S:
        return "remake"
    return None


def load_todo(tsv, min_size, excluded, interleave=True, patch_filter=None):
    """Load pending games, interleaved round-robin by patch so the first N
    downloads span every patch instead of front-loading one group."""
    by_patch = collections.OrderedDict()
    with open(tsv, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        patch_col = None
        for i, h in enumerate(header):
            if h.strip().lower() == "gameversion":
                patch_col = i
        for ln in f:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            platform, gid = parts[0], parts[1]
            if f"{platform}_{gid}" in excluded:
                continue
            out = os.path.join(DOWNLOADS, f"{platform}-{gid}.rofl")
            if os.path.exists(out) and os.path.getsize(out) >= min_size:
                _stats["skip"] += 1
                continue
            patch = patch_of(parts[patch_col]) if patch_col is not None else "?"
            if patch_filter is not None and patch != patch_filter:
                continue
            by_patch.setdefault(patch, []).append((platform, gid, out))
    todo = []
    if interleave:
        # round-robin: take one from each patch group in rotation
        while any(by_patch.values()):
            for patch in list(by_patch.keys()):
                if by_patch[patch]:
                    todo.append(by_patch[patch].pop(0))
    else:
        for patch, items in by_patch.items():
            todo.extend(items)
    return todo


def worker(name, work_queue):
    try:
        with SGPClient(min_gap=args.min_gap) as sgp:
            while True:
                try:
                    platform, gid, out = work_queue.get_nowait()
                except Exception:
                    return
                match_id = f"{platform}_{gid}"
                with _lock:
                    _stats["in_flight"] += 1
                    _stats["max_in_flight"] = max(_stats["max_in_flight"], _stats["in_flight"])
                try:
                    # The edge intermittently returns an empty/bad 200 body;
                    # retry a few times before declaring the game unavailable.
                    success = False
                    for attempt in range(1, 4):
                        if sgp.download_replay(match_id, out):
                            success = True
                            break
                        if os.path.exists(out):
                            os.remove(out)
                        time.sleep(1.0 * attempt)
                    if success:
                        size = os.path.getsize(out)
                        rec = rofl_parser.parse_file(out)
                        reason = _gate_reason(rec)
                        if reason is None:
                            with _lock:
                                _stats["ok"] += 1
                            log(f"  OK  {match_id} "
                                f"patch={patch_of(rec.get('gameVersion'))} "
                                f"len={rec.get('gameLength_s')}s "
                                f"chunks={rec.get('n_chunks')} {size} bytes "
                                f"[total ok={_stats['ok']}]")
                        else:
                            with _lock:
                                _stats["excluded"] += 1
                            _exclude(platform, gid, reason, rec, size)
                            if os.path.exists(out):
                                os.remove(out)
                            log(f"  EXCL {match_id} ({reason})")
                    else:
                        with _lock:
                            _stats["fail"] += 1
                        if os.path.exists(out):
                            os.remove(out)
                        log(f"  FAIL {match_id} (unavailable)")
                except Exception as e:
                    with _lock:
                        _stats["fail"] += 1
                    log(f"  ERR  {match_id} {str(e)[:120]}")
                finally:
                    with _lock:
                        _stats["in_flight"] -= 1
                    work_queue.task_done()
    except Exception as e:
        log(f"  WORKER {name} FATAL {str(e)[:150]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--min-gap", type=float, default=1.5)
    ap.add_argument("--min-size", type=int, default=MIN_SIZE)
    ap.add_argument("--patch", type=str, default=None,
                    help="only download candidates of this patch (e.g. 16.14)")
    ap.add_argument("--tsv", default=os.path.join(ROOT, "meta", "candidates.tsv"))
    global args
    args = ap.parse_args()

    # Hard safety cap: never more than 4 concurrent requests / workers. This
    # keeps traffic gentle on the account/edge (user-approved for the 20k run).
    args.workers = min(args.workers, 4)

    os.makedirs(DOWNLOADS, exist_ok=True)
    excluded = load_excluded()
    todo = load_todo(args.tsv, MIN_SIZE, excluded, patch_filter=args.patch)
    if args.max:
        todo = todo[: args.max]

    import queue as _q
    q = _q.Queue()
    for item in todo:
        q.put(item)

    log(f"=== parallel download start: workers={args.workers} todo={len(todo)} "
        f"excluded={len(excluded)} skipped_existing={_stats['skip']} ===")

    threads = [threading.Thread(target=worker, args=(f"W{i+1}", q), daemon=True)
               for i in range(args.workers)]
    t0 = time.time()
    for t in threads:
        t.start()
    q.join()
    for t in threads:
        t.join()
    dt = time.time() - t0

    log(f"=== parallel download done: ok={_stats['ok']} excluded={_stats['excluded']} "
        f"fail={_stats['fail']} max_in_flight={_stats['max_in_flight']} "
        f"elapsed={dt:.0f}s rate={_stats['ok']/dt:.2f} g/s ===")


if __name__ == "__main__":
    main()
