"""Resolve a player reference to a PUUID.

A player reference can be any of:
  * a PUUID (used directly)
  * a summoner name        (only if the player is a friend)
  * a Riot ID, for example "HideOnBush#KR1"   (only if the player is a friend)

The tool needs no key. The tool resolves names through the running client
only:

  1. the friend list of the current summoner
  2. a search on the platform of the client

Most names fail with a clear message. The gather command and the resolve
command always accept a PUUID.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

_PUUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class ResolveError(RuntimeError):
    pass


class Resolver:
    def __init__(self, platform=None):
        self.platform = platform or os.environ.get("RIOT_PLATFORM") or "EUW1"

    def resolve(self, ref):
        ref = (ref or "").strip()
        if not ref:
            raise ResolveError("empty player reference")
        if _PUUID_RE.match(ref):
            return ref
        return self._resolve_local(ref)

    def _resolve_local(self, ref):
        from .client import lockfile_parts  # noqa: PLC0415

        try:
            _pid, port, password = lockfile_parts()
        except FileNotFoundError:
            raise ResolveError(
                "the League client is not running. Start the client to "
                "resolve names, or give the PUUID."
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
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code in (404, 422):
                    return None
                raise ResolveError(f"client lookup failed ({e.code})") from e

        # 1) friends list: name -> puuid
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
            f"cannot resolve {ref!r}. The tool only searches the friend list "
            "of the current summoner. Give a friend, or pass a PUUID."
        )


def resolve(ref, platform=None):
    return Resolver(platform=platform).resolve(ref)
