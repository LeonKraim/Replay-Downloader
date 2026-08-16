"""Per-output-directory state for a gather.

All progress lives in the output directory, so a gather can be stopped and
restarted at any time without losing work:

  downloads/            valid .rofl replay files
  candidates.tsv        known games that match the filter (may be downloaded)
  excluded.tsv          games that failed validation and will never be retried
  visited.txt           players whose history was already walked
  frontier.txt          players queued to be walked
  gather.log            human-readable event log
"""
from __future__ import annotations

import os
import threading
import time

CANDIDATE_HEADER = "platform\tgameId\tgameVersion\tmapId\tqueueId\tgameCreation\tgameDuration\tseed"
EXCLUDED_HEADER = "platform\tgameId\treason\tpatch\tgameLength_s\tsize_bytes"


class State:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.downloads = os.path.join(out_dir, "downloads")
        self.candidates = os.path.join(out_dir, "candidates.tsv")
        self.excluded = os.path.join(out_dir, "excluded.tsv")
        self.visited = os.path.join(out_dir, "visited.txt")
        self.frontier = os.path.join(out_dir, "frontier.txt")
        self.logfile = os.path.join(out_dir, "gather.log")
        self._lock = threading.Lock()
        os.makedirs(self.downloads, exist_ok=True)

    # -- logging ----------------------------------------------------------
    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        with self._lock:
            with open(self.logfile, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        print(line, flush=True)

    # -- candidates -------------------------------------------------------
    def load_candidates(self):
        seen = set()
        if not os.path.exists(self.candidates):
            return seen
        with open(self.candidates, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 2 and p[1].isdigit():
                    seen.add(f"{p[0]}_{p[1]}")
        return seen

    def add_candidate(self, platform, gid, game_version, map_id,
                      queue_id, game_creation, game_duration, seed):
        first = not os.path.exists(self.candidates)
        with self._lock:
            with open(self.candidates, "a", encoding="utf-8", newline="") as f:
                if first:
                    f.write(CANDIDATE_HEADER + "\n")
                f.write("\t".join(str(x) for x in [
                    platform, gid, game_version, map_id, queue_id,
                    game_creation, game_duration, seed,
                ]) + "\n")

    # -- excluded ---------------------------------------------------------
    def load_excluded(self):
        ex = set()
        if not os.path.exists(self.excluded):
            return ex
        with open(self.excluded, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 2:
                    ex.add(f"{p[0]}_{p[1]}")
        return ex

    def add_excluded(self, platform, gid, reason, patch, length_s, size):
        first = not os.path.exists(self.excluded)
        with self._lock:
            with open(self.excluded, "a", encoding="utf-8", newline="") as f:
                if first:
                    f.write(EXCLUDED_HEADER + "\n")
                f.write("\t".join(str(x) for x in [
                    platform, gid, reason, patch, length_s, size,
                ]) + "\n")

    # -- visited / frontier ----------------------------------------------
    def load_visited(self):
        if not os.path.exists(self.visited):
            return set()
        with open(self.visited, encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}

    def add_visited(self, puuid):
        with self._lock:
            with open(self.visited, "a", encoding="utf-8") as f:
                f.write(puuid + "\n")

    def load_frontier(self):
        if not os.path.exists(self.frontier):
            return []
        with open(self.frontier, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    def save_frontier(self, frontier):
        with self._lock:
            with open(self.frontier, "w", encoding="utf-8") as f:
                f.write("\n".join(frontier))

    # -- counts -----------------------------------------------------------
    def count(self, patch_filter=None):
        """Return (downloaded, pending, candidates, excluded) for games that
        match patch_filter: None for all games, a two-part patch like '16.14',
        or a set of such patches."""
        def patch_of(version):
            if not version:
                return None
            parts = version.split(".")
            return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None

        def _keep(game_patch):
            if patch_filter is None or gi is None:
                return True
            if isinstance(patch_filter, (set, frozenset)):
                return game_patch in patch_filter
            return game_patch == patch_filter

        excl = self.load_excluded()
        done, pending, total = 0, 0, 0
        if os.path.exists(self.candidates):
            with open(self.candidates, encoding="utf-8") as f:
                header = f.readline().rstrip("\n").split("\t")
                gi = header.index("gameVersion") if "gameVersion" in header else None
                for ln in f:
                    p = ln.rstrip("\n").split("\t")
                    if len(p) < 2 or not p[1].isdigit():
                        continue
                    if not _keep(patch_of(p[gi] if gi is not None and gi < len(p) else None)):
                        continue
                    total += 1
                    key = f"{p[0]}_{p[1]}"
                    if key in excl:
                        continue
                    if os.path.exists(os.path.join(self.downloads, f"{p[0]}-{p[1]}.rofl")):
                        done += 1
                    else:
                        pending += 1
        return done, pending, total, len(excl)
