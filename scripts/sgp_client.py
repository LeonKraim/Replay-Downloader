#!/usr/bin/env python3
"""SGP (Spectator Game Protocol) client for the 1000-replay collection.

Authenticates against the running League Client's RCP (Riot Client Platform)
local API to obtain the RSO access token, then calls the public SGP edge
endpoints through a real Chromium (Playwright) context so Cloudflare's
browser-signature checks are satisfied.

Validated endpoints (all return 200 with Bearer {rso_token} via Chromium):
  * Player match list     GET /match-history-query/v1/products/lol/player/{puuid}?startIndex&count&tagsQueryType=AND
  * Player summaries      GET /match-history-query/v1/products/lol/player/{puuid}/SUMMARY?startIndex&count&tagsQueryType=AND
                          -> {"games":[{"metadata":{...participants:[puuid...],...},"json":{gameId,gameVersion,mapId,queueId,platformId,...}},...]}
  * Match DETAILS         GET /match-history-query/v1/products/lol/{platform}_{gameId}/DETAILS
  * Replay download       GET /match-history-query/v3/product/lol/matchId/{platform}_{gameId}/infoType/replay   (binary ROFL2)

Usage (as a library):
    from sgp_client import SGPClient
    with SGPClient() as sgp:
        games = sgp.get_summary(puuid, count=100)          # -> list of {'json':{...}, 'participants':[...], 'match_id':...}
        data  = sgp.get_details('EUW1_7948367635')
        ok    = sgp.download_replay('EUW1_7948367635', r'downloads\EUW1-7948367635.rofl')
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.request
import urllib.error

try:
    from playwright.sync_api import sync_playwright, Error as PWError
except ImportError:  # pragma: no cover
    sync_playwright = None
    PWError = Exception

LOCKFILE = r"C:\Riot Games\League of Legends\lockfile"
SGP_HOST = "https://euc1-red.pp.sgp.pvp.net"
_CTX = None  # module-level unverified SSL context (self-signed local client cert)


def _ssl_ctx():
    global _CTX
    if _CTX is None:
        _CTX = ssl.create_default_context()
        _CTX.check_hostname = False
        _CTX.verify_mode = ssl.CERT_NONE
    return _CTX


def lockfile_parts():
    """Return (pid, port, password) from the running client's lockfile."""
    with open(LOCKFILE, "r") as f:
        p = f.read().strip().split(":")
    return p[1], p[2], p[3]


def _local_get(path):
    """GET a local RCP route with basic auth (riot:password)."""
    pid, port, password = lockfile_parts()
    req = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        headers={"Authorization": "Basic " + base64.b64encode(
            f"riot:{password}".encode()).decode()},
    )
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=25) as r:
        return json.loads(r.read().decode())


def get_rso_token():
    """Fetch the current RSO access token from the local client."""
    d = _local_get("/lol-rso-auth/v1/authorization/access-token")
    return d.get("token") or d.get("accessToken")


def current_version():
    """The current game version from the running client, for example
    '16.16.8049184+branch.releases-16-16...'. Returns None if it cannot be
    read. The version comes from the client's own patch API, so it follows
    new patches automatically without a new release of the tool."""
    try:
        d = _local_get("/lol-patch/v1/game-version")
        if isinstance(d, str) and d.strip():
            return d.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


class SGPClient:
    """Reusable SGP client backed by a persistent headless-Chromium context.

    Use as a context manager. All external calls are rate-limited and retried
    on transient 403/429/5xx.
    """

    # Minimum gap between external SGP requests (seconds) - be polite to the edge.
    DEFAULT_MIN_GAP = 1.2
    MAX_RETRIES = 4

    def __init__(self, min_gap=DEFAULT_MIN_GAP, token=None, verbose=True):
        self.min_gap = min_gap
        self.verbose = verbose
        self._token = token or get_rso_token()
        self._pw = None
        self._browser = None
        self._context = None
        self._api = None
        self._last = 0.0
        self.n_requests = 0

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self):
        if sync_playwright is None:
            raise RuntimeError("playwright not installed")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context()
        self._api = self._context.request
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def _pace(self):
        """Enforce the minimum gap between requests."""
        dt = time.time() - self._last
        if dt < self.min_gap:
            time.sleep(self.min_gap - dt)
        self._last = time.time()

    def _refresh_token_if_needed(self):
        """Re-fetch the RSO token (cheap) before long gaps in case it rotated."""
        try:
            self._token = get_rso_token()
        except Exception:
            pass

    # -- low-level request ---------------------------------------------------
    def _request(self, path, binary=False):
        url = SGP_HOST + path
        for attempt in range(1, self.MAX_RETRIES + 1):
            self._pace()
            self._refresh_token_if_needed()
            try:
                r = self._api.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/octet-stream" if binary else "application/json",
                    },
                    timeout=120_000,
                )
                self.n_requests += 1
                if r.status == 200:
                    return r.body()
                body = r.body()[:300]
                # transient / edge blocks: back off and retry
                if r.status in (403, 429, 500, 502, 503, 504):
                    if self.verbose:
                        print(f"    ...retry({attempt}) HTTP {r.status} {body[:120]!r}")
                    time.sleep(1.5 * attempt)
                    # 403 with Cloudflare 1010 text -> rotate browser? keep trying.
                    continue
                # permanent
                raise SGPError(f"HTTP {r.status} {url}: {body[:200]!r}")
            except PWError as e:
                if attempt == self.MAX_RETRIES:
                    raise SGPError(f"playwright error on {url}: {str(e)[:150]}")
                time.sleep(1.5 * attempt)
            except SGPError:
                raise
            except Exception as e:
                if attempt == self.MAX_RETRIES:
                    raise SGPError(f"unexpected error on {url}: {str(e)[:150]}")
                time.sleep(1.5 * attempt)
        raise SGPError(f"exhausted retries for {url}")

    # -- public API ----------------------------------------------------------
    def current_version(self):
        """The current game version, for example '16.16.0.7044128'.

        Prefers the running client's patch API; falls back to the SGP server's
        version endpoint. Returns None if neither can be read."""
        v = current_version()
        if v:
            return v
        try:
            raw = self._request("/observer-mode/rest/consumer/version")
            text = raw.decode("utf-8", "replace").strip().strip('"')
            return text or None
        except SGPError:
            return None

    def get_summary(self, puuid, start=0, count=100):
        """Return list of game dicts from a player's match summary.

        Each item: {"match_id": "EUW1_123", "json": {gameId,gameVersion,mapId,...},
                    "participants": [puuid,...]}.
        """
        raw = self._request(
            f"/match-history-query/v1/products/lol/player/{puuid}/SUMMARY"
            f"?startIndex={start}&count={count}&tagsQueryType=AND"
        )
        js = json.loads(raw)
        games = js.get("games", [])
        out = []
        for g in games:
            meta = g.get("metadata", {})
            item = {
                "match_id": meta.get("match_id"),
                "json": g.get("json", {}),
                "participants": meta.get("participants", []),
            }
            out.append(item)
        return out

    def get_match_list(self, puuid, start=0, count=100):
        raw = self._request(
            f"/match-history-query/v1/products/lol/player/{puuid}"
            f"?startIndex={start}&count={count}&tagsQueryType=AND"
        )
        return json.loads(raw)  # list of "PLAT_123" strings

    def get_details(self, match_id):
        raw = self._request(f"/match-history-query/v1/products/lol/{match_id}/DETAILS")
        return json.loads(raw)

    def download_replay(self, match_id, out_path):
        """Download the replay for match_id (e.g. 'EUW1_123') to out_path.

        Returns True on success (a valid ROFL signature was written), False if
        the replay is unavailable (404) or zero-byte.
        """
        raw = self._request(
            f"/match-history-query/v3/product/lol/matchId/{match_id}/infoType/replay",
            binary=True,
        )
        if not raw or raw[:4] not in (b"RIOT", b"RLR"):
            return False
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(raw)
        return True


class SGPError(RuntimeError):
    pass


def _demo():
    """Quick sanity check: enumerate one player, download one SR in-patch replay."""
    import sys

    print("rso token ok:", bool(get_rso_token()))
    # replace with any player's PUUID to run the demo
    puuid = "00000000-0000-0000-0000-000000000000"
    with SGPClient() as sgp:
        games = sgp.get_summary(puuid, count=5)
        print(f"summary: {len(games)} games")
        for g in games[:3]:
            j = g["json"]
            print("  ", j.get("platformId"), j.get("gameId"), j.get("gameVersion"),
                  "map", j.get("mapId"), "q", j.get("queueId"))


if __name__ == "__main__":
    _demo()
