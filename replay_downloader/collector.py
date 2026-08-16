"""The gather loop: spiral through the player graph and download replays.

Starts from one or more seed players, walks their match history, and for every
new player encountered in those matches, walks that player's history too (the
"spiral"). Games that match the filter are queued for download. Every download
is re-parsed from its own bytes before it is kept, so only valid, usable
replays survive.

Pacing is deliberately gentle (user-mandated): at most 4 download workers and
no less than --min-gap seconds between requests per worker.
"""
from __future__ import annotations

import os
import queue as _queue
import threading
import time

from .client import SGPClient, current_version
from .state import State

# Floor that only excludes truncated/junk downloads. A real ROFL2 is far larger.
MIN_SIZE = 200 * 1024
PAGE = 100                     # games per match-history page
MAX_PAGES_PER_PLAYER = 20      # safety cap: at most 2000 games per player
# Rejection filters are OFF by default (0 / empty = accept anything). The user
# turns them on per run via --patch, --map, --min-length.
RECOMMENDED_SR_MAP = 11        # Summoner's Rift
RECOMMENDED_MIN_LENGTH_S = 300  # skip remakes / instant surrenders
# Riot purges replays from the SGP server after a few patches. When the user
# gives no --patch, gather defaults to this many most recent patches.
DEFAULT_RECENT_PATCHES = 3
PATCHES_PER_SEASON = 24        # season rollover: 15.1 follows 14.24


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def prev_patch(patch):
    """The patch before 'X.Y', e.g. '16.16' -> '16.15'; across a season
    rollover '16.1' -> '15.24'. Returns None for the earliest known patch."""
    if not patch or "." not in patch:
        return None
    try:
        major, _, minor = patch.partition(".")
        major, minor = int(major), int(minor)
    except (TypeError, ValueError):
        return None
    if minor > 1:
        return f"{major}.{minor - 1}"
    if major <= 1:
        return None
    return f"{major - 1}.{PATCHES_PER_SEASON}"


class Gatherer:
    def __init__(self, state, patch_set=None, map_id=0,
                 min_length=0, max_replays=0,
                 workers=4, min_gap=1.5, no_download=False,
                 sgp_host=None, plateau_limit=8, players_per_batch=5,
                 download_batch=200, max_players=0):
        self.state = state
        self.patch_set = set(patch_set or [])
        self.map_id = map_id or 0
        self.min_length = min_length or 0
        self.max_replays = max_replays
        self.max_players = max_players
        self.workers = min(workers, 4)          # hard safety cap
        self.min_gap = min_gap
        self.no_download = no_download
        self.plateau_limit = plateau_limit
        self.players_per_batch = players_per_batch
        self.download_batch = download_batch
        self._players_walked = 0
        self._stats = {"ok": 0, "fail": 0, "excluded": 0, "downloaded": 0}
        self._lock = threading.Lock()

    # -- filters ------------------------------------------------------------
    def _gate_reason(self, rec):
        """Return an exclusion reason if the parsed file must NOT be kept,
        else None. A kept file is a structurally valid ROFL2 whose own
        metadata confirms an allowed patch, the requested map, and a real
        (long enough) game."""
        if not rec.get("magic_ok") or rec.get("fileFormat") != 2:
            return "not-rofl2"
        if self.patch_set:
            patch = patch_of(rec.get("gameVersion"))
            if patch not in self.patch_set:
                return "patch:" + str(patch)
        if self.map_id and rec.get("mapId_inferred") != self.map_id:
            return "map:" + str(rec.get("mapId_inferred"))
        if (rec.get("gameLength_s") or 0) < self.min_length:
            return "short"
        return None

    def _resolve_patches(self, sgp):
        """When the user gave no --patch, default to the last few patches that
        Riot still stores on the server. The current patch is read from the
        running client at runtime, so a new patch needs no new build of the
        tool. An explicit --patch always overrides the default."""
        if self.patch_set:
            return self.patch_set
        version = current_version() or sgp.current_version()
        current = patch_of(version)
        if not current:
            self.state.log(
                "no --patch given and the current patch could not be read: "
                "keeping every game (old replays may fail with unavailable)")
            return set()
        patches = []
        p = current
        while p and len(patches) < DEFAULT_RECENT_PATCHES:
            patches.append(p)
            p = prev_patch(p)
        self.patch_set = set(patches)
        self.state.log(
            f"no --patch given: keeping only the last {len(patches)} patches "
            f"({', '.join(patches)}). Riot removes replays of older patches "
            f"from the server. Use --patch to choose other patches, including "
            f"older ones.")
        return self.patch_set

    def _cutoff_ms(self):
        """Earliest known target-patch game, minus a 1-day margin. Used to stop
        paginating a player's history once it is older than the window."""
        if not self.patch_set:
            return None
        cutoff = None
        if not os.path.exists(self.state.candidates):
            return None
        with open(self.state.candidates, encoding="utf-8") as f:
            header = f.readline().rstrip("\n").split("\t")
            if "gameVersion" not in header or "gameCreation" not in header:
                return None
            gi = header.index("gameVersion")
            ci = header.index("gameCreation")
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) <= max(gi, ci):
                    continue
                if patch_of(p[gi]) in self.patch_set:
                    try:
                        c = int(p[ci])
                    except (ValueError, TypeError):
                        continue
                    if cutoff is None or c < cutoff:
                        cutoff = c
        if cutoff:
            cutoff -= 86400000
        return cutoff

    # -- enumeration ---------------------------------------------------------
    def _player_summary(self, sgp, puuid, cutoff_ms):
        games = []
        start = 0
        for _ in range(MAX_PAGES_PER_PLAYER):
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

    def _enumerate_batch(self, sgp, frontier, visited, queued, cutoff_ms):
        """Walk up to players_per_batch players, appending matching games to
        candidates.tsv and queuing their opponents into the frontier."""
        seen = self.state.load_candidates()
        walked = 0
        new_candidates = 0
        limit = self.players_per_batch
        if self.max_players:
            limit = min(limit, self.max_players - self._players_walked)
        while frontier and walked < limit:
            puuid = frontier.pop(0)
            if puuid in visited:
                continue
            visited.add(puuid)
            walked += 1
            try:
                games = self._player_summary(sgp, puuid, cutoff_ms)
            except Exception as e:  # noqa: BLE001
                self.state.log(f"  player {puuid[:12]} ERROR {str(e)[:100]}")
                continue
            for g in games:
                j = g.get("json") or {}
                match_id = g.get("match_id")
                if not match_id or "_" not in match_id:
                    continue
                platform, _, gid = match_id.partition("_")
                patch = patch_of(j.get("gameVersion"))
                target = not self.patch_set or patch in self.patch_set
                # spiral: opponents of target-patch games are explored first
                for p in g.get("participants", []):
                    if p and p not in visited and p not in queued:
                        queued.add(p)
                        if target:
                            frontier.insert(0, p)
                        else:
                            frontier.append(p)
                if j.get("mapId") and self.map_id and j.get("mapId") != self.map_id:
                    continue
                if (j.get("gameDuration") or 0) < self.min_length:
                    continue
                key = f"{platform}_{gid}"
                if key in seen:
                    continue
                seen.add(key)
                new_candidates += 1
                self.state.add_candidate(
                    platform, gid, j.get("gameVersion"), j.get("mapId"),
                    j.get("queueId"), j.get("gameCreation"),
                    j.get("gameDuration"), puuid,
                )
            self.state.add_visited(puuid)
            if walked % 5 == 0:
                self.state.save_frontier(frontier)
            self.state.log(
                f"  player {puuid[:12]} games={len(games)} "
                f"new_candidates={new_candidates} queued={len(frontier)}"
            )
        self._players_walked += walked
        self.state.save_frontier(frontier)
        return walked, new_candidates

    # -- download -------------------------------------------------------------
    def _download_batch(self, items):
        """Download and validate items (list of (platform, gid, out_path))."""
        q = _queue.Queue()
        for it in items:
            q.put(it)
        stats = {"ok": 0, "fail": 0, "excluded": 0}
        lock = threading.Lock()

        def log(msg):
            self.state.log(msg)

        def worker():
            try:
                with SGPClient(min_gap=self.min_gap) as sgp:
                    while True:
                        try:
                            platform, gid, out = q.get_nowait()
                        except Exception:  # noqa: BLE001
                            return
                        match_id = f"{platform}_{gid}"
                        try:
                            success = False
                            for attempt in range(1, 4):
                                if sgp.download_replay(match_id, out):
                                    success = True
                                    break
                                if os.path.exists(out):
                                    os.remove(out)
                                time.sleep(1.0 * attempt)
                            if success:
                                rec = _parse(out)
                                reason = self._gate_reason(rec)
                                if reason is None:
                                    with lock:
                                        stats["ok"] += 1
                                    log(f"  OK  {match_id} patch={patch_of(rec.get('gameVersion'))} "
                                        f"len={rec.get('gameLength_s')}s {os.path.getsize(out)} bytes "
                                        f"[ok={stats['ok']}]")
                                else:
                                    with lock:
                                        stats["excluded"] += 1
                                    self.state.add_excluded(
                                        platform, gid, reason, patch_of(rec.get("gameVersion")),
                                        rec.get("gameLength_s"), os.path.getsize(out))
                                    if os.path.exists(out):
                                        os.remove(out)
                                    log(f"  EXCL {match_id} ({reason})")
                            else:
                                with lock:
                                    stats["fail"] += 1
                                self.state.add_excluded(
                                    platform, gid, "unavailable", None, None, 0)
                                if os.path.exists(out):
                                    os.remove(out)
                                log(f"  FAIL {match_id} (unavailable)")
                        except Exception as e:  # noqa: BLE001
                            with lock:
                                stats["fail"] += 1
                            # record the failure so the game is never retried
                            self.state.add_excluded(
                                platform, gid, "error", None, None, 0)
                            log(f"  ERR  {match_id} {str(e)[:120]}")
                        finally:
                            q.task_done()
            except Exception as e:  # noqa: BLE001
                log(f"  WORKER FATAL {str(e)[:150]}")

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(self.workers, len(items)))]
        for t in threads:
            t.start()
        q.join()
        for t in threads:
            t.join()
        with lock:
            self._stats["ok"] += stats["ok"]
            self._stats["fail"] += stats["fail"]
            self._stats["excluded"] += stats["excluded"]
        return stats

    def _pending_items(self):
        excl = self.state.load_excluded()
        items = []
        if os.path.exists(self.state.candidates):
            with open(self.state.candidates, encoding="utf-8") as f:
                header = f.readline().rstrip("\n").split("\t")
                gi = header.index("gameVersion") if "gameVersion" in header else None
                for ln in f:
                    p = ln.rstrip("\n").split("\t")
                    if len(p) < 2 or not p[1].isdigit():
                        continue
                    platform, gid = p[0], p[1]
                    if self.patch_set and gi is not None:
                        if patch_of(p[gi] if gi < len(p) else None) not in self.patch_set:
                            continue
                    key = f"{platform}_{gid}"
                    if key in excl:
                        continue
                    out = os.path.join(self.state.downloads, f"{platform}-{gid}.rofl")
                    if os.path.exists(out) and os.path.getsize(out) >= MIN_SIZE:
                        continue
                    items.append((platform, gid, out))
        return items

    # -- main loop ------------------------------------------------------------
    def run(self, seeds):
        """Seed the spiral with players and run until max, plateau, or done."""
        with SGPClient() as sgp:
            self._resolve_patches(sgp)
            frontier = [s for s in self.state.load_frontier()
                        if s not in self.state.load_visited()]
            visited = self.state.load_visited()
            queued = set(frontier)
            for s in seeds:
                if s and s not in visited:
                    queued.add(s)
                    frontier.append(s)
            cutoff = self._cutoff_ms()
            self.state.log(
                f"=== gather start: seeds={len(seeds)} queued={len(frontier)} "
                f"patches={sorted(self.patch_set) or 'any'} map={self.map_id or 'any'} "
                f"max={self.max_replays or 'unlimited'} workers={self.workers} "
                f"min_gap={self.min_gap}s ==="
            )
            last_total = None
            last_done = None
            enum_stall = 0
            dl_stall = 0
            while True:
                done, pending, total, n_excl = self.state.count(self._count_filter())
                self.state.log(
                    f"status: downloaded={done} pending={pending} "
                    f"candidates={total} excluded={n_excl}"
                )
                if self.max_replays and done >= self.max_replays:
                    self.state.log(f"=== reached max {self.max_replays} ===")
                    break
                if last_total is not None and total == last_total:
                    enum_stall += 1
                else:
                    enum_stall = 0
                last_total = total
                if last_done is not None and done == last_done and pending > 0:
                    dl_stall += 1
                else:
                    dl_stall = 0
                last_done = done
                if enum_stall >= self.plateau_limit:
                    self.state.log(
                        f"=== plateau: no new candidates for {enum_stall} cycles. "
                        f"downloaded={done} ===")
                    break
                if dl_stall >= self.plateau_limit:
                    self.state.log(
                        f"=== plateau: {dl_stall} cycles with no new downloads. "
                        f"downloaded={done} ===")
                    break

                walk_done = not frontier or (
                    self.max_players and self._players_walked >= self.max_players)
                if self.no_download:
                    if walk_done:
                        self.state.log("=== no more players to walk (no_download) ===")
                        break
                    walked, added = self._enumerate_batch(
                        sgp, frontier, visited, queued, cutoff)
                    if walked == 0 and added == 0:
                        self.state.log("=== frontier exhausted ===")
                        break
                elif pending > 0:
                    n = self.download_batch
                    if self.max_replays:
                        n = min(n, self.max_replays - done)
                    items = self._pending_items()[: n]
                    if items:
                        self._download_batch(items)
                else:
                    if walk_done:
                        self.state.log("=== frontier exhausted ===")
                        break
                    walked, added = self._enumerate_batch(
                        sgp, frontier, visited, queued, cutoff)
                    if walked == 0 and added == 0:
                        self.state.log("=== frontier exhausted ===")
                        break
        done, pending, total, n_excl = self.state.count(self._count_filter())
        self.state.log(
            f"=== gather done: downloaded={done} excluded={n_excl} "
            f"pending={pending} ===")
        return {"downloaded": done, "excluded": n_excl, "pending": pending}

    def _count_filter(self):
        return self.patch_set or None


def _parse(path):
    from . import rofl  # noqa: PLC0415
    return rofl.parse_file(path)
