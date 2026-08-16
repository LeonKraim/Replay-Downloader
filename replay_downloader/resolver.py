"""Resolve a player reference to a PUUID.

A player reference can be any of:
  * a PUUID (used directly)
  * a Riot ID, for example "HideOnBush#KR1"  (needs RIOT_API_KEY)
  * a summoner name, for example "Faker"      (needs RIOT_API_KEY)
  * a summoner name the running client can see (client-platform names only)

Riot's public API is used when RIOT_API_KEY is set. Without a key, only the
running client's own platform can be searched, so most names fail with a clear
message. The gather and resolve commands always accept a PUUID.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

_PUUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ResolveError(RuntimeError):
    pass


class Resolver:
    def __init__(self, api_key=None, platform=None):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        self.platform = platform or os.environ.get("RIOT_PLATFORM") or "EUW1"

    def _routing(self):
        """Regional routing value for the account service, from the platform."""
        p = (self.platform or "").lower()
        if p.startswith("na") or p.startswith("br") or p.startswith("la"):
            return "americas"
        if p.startswith("kr") or p.startswith("jp"):
            return "asia"
        if p.startswith("oc") or p.startswith("ph") or p.startswith("sg") \
                or p.startswith("tw") or p.startswith("th") or p.startswith("vn"):
            return "sea"
        return "europe"  # EUW, EUNE, TR, RU, ME

    def _get(self, url):
        req = urllib.request.Request(
            url,
            headers={"X-Riot-Token": self.api_key, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise ResolveError(
                f"Riot API {e.code} for {url}: {e.read()[:120]!r}"
            ) from e

    def resolve(self, ref):
        ref = (ref or "").strip()
        if not ref:
            raise ResolveError("empty player reference")
        if _PUUID_RE.match(ref):
            return ref
        if not self.api_key:
            return self._resolve_local(ref)
        if "#" in ref:
            return self._resolve_riot_id(ref)
        return self._resolve_summoner_name(ref)

    # -- Riot API paths ---------------------------------------------------
    def _resolve_riot_id(self, ref):
        game_name, _, tag = ref.partition("#")
        if not game_name or not tag:
            raise ResolveError(f"invalid Riot ID: {ref!r} (expected Name#TAG)")
        url = (
            f"https://{self._routing()}.api.riotgames.com/riot/account/v1/"
            f"accounts/by-riot-id/{urllib.parse.quote(game_name)}/"
            f"{urllib.parse.quote(tag)}"
        )
        d = self._get(url)
        puuid = d.get("puuid")
        if not puuid:
            raise ResolveError(f"no PUUID returned for {ref!r}")
        return puuid

    def _resolve_summoner_name(self, ref):
        url = (
            f"https://{self.platform}.api.riotgames.com/lol/summoner/v4/"
            f"summoners/by-name/{urllib.parse.quote(ref)}"
        )
        d = self._get(url)
        puuid = d.get("puuid")
        if not puuid:
            raise ResolveError(f"no PUUID returned for {ref!r}")
        return puuid

    # -- local-client path -------------------------------------------------
    def _resolve_local(self, ref):
        from .client import lockfile_parts  # noqa: PLC0415
        import base64  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        import ssl  # noqa: PLC0415

        try:
            _pid, port, password = lockfile_parts()
        except FileNotFoundError:
            raise ResolveError(
                "the League client is not running. Start it, or set "
                "RIOT_API_KEY to resolve names without the client."
            ) from None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        auth = "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()

        def lc(path, timeout=20):
            req = urllib.request.Request(
                f"https://127.0.0.1:{port}{path}",
                headers={"Authorization": auth},
            )
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
                    return _json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (404, 422):
                    return None
                raise ResolveError(f"client lookup failed ({e.code})") from e

        # 1) friends list: name -> puuid without any API key
        want_name, _, want_tag = ref.partition("#")
        friends = lc("/lol-chat/v1/friends") or []
        for f in friends:
            fn = (f.get("gameName") or f.get("name") or "").strip()
            if fn.lower() != (want_name or "").strip().lower():
                continue
            if want_tag and (f.get("tagLine") or "").upper() != want_tag.upper():
                continue
            puuid = f.get("puuid")
            if puuid:
                return puuid

        # 2) in-platform summoner search (rarely succeeds)
        d = lc(f"/lol-summoner/v1/summoners?name={urllib.parse.quote(ref)}")
        if d and d.get("puuid"):
            return d["puuid"]

        raise ResolveError(
            f"cannot resolve {ref!r} without a Riot API key. The client only "
            "searches friends and its own platform. Set RIOT_API_KEY to "
            "resolve any name, or pass a PUUID."
        )


def resolve(ref, api_key=None, platform=None):
    return Resolver(api_key=api_key, platform=platform).resolve(ref)
