"""Download one specific replay by game ID."""
from __future__ import annotations

import os

from .client import SGPClient
from .rofl import parse_file


def get_replay(match_ref, out_dir, min_length=0, map_id=0,
               patch_set=None, workers=4, min_gap=1.5):
    """Download the replay for a reference like 'EUW1_1234567' or
    'EUW1-1234567'. Returns a result dict with a 'status' key."""
    ref = (match_ref or "").strip()
    if "-" in ref and "_" not in ref:
        ref = ref.replace("-", "_", 1)
    if "_" not in ref:
        raise ValueError(
            f"invalid game reference {match_ref!r}: expected PLATFORM_GAMEID, "
            "for example EUW1_7923052589"
        )
    platform, _, gid = ref.partition("_")
    if not platform or not gid.isdigit():
        raise ValueError(f"invalid game reference {match_ref!r}")
    out = os.path.join(out_dir, "downloads", f"{platform}-{gid}.rofl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        return {"status": "already-downloaded", "path": out}

    with SGPClient(min_gap=min_gap) as sgp:
        if not sgp.download_replay(f"{platform}_{gid}", out):
            if os.path.exists(out):
                os.remove(out)
            return {"status": "unavailable", "match": ref}

    rec = parse_file(out)
    if not rec.get("magic_ok") or rec.get("fileFormat") != 2:
        reason = "not-rofl2"
    elif patch_set and patch_of(rec.get("gameVersion")) not in patch_set:
        reason = "patch:" + str(patch_of(rec.get("gameVersion")))
    elif map_id and rec.get("mapId_inferred") != map_id:
        reason = "map:" + str(rec.get("mapId_inferred"))
    elif (rec.get("gameLength_s") or 0) < min_length:
        reason = "short"
    else:
        reason = None
    if reason:
        if os.path.exists(out):
            os.remove(out)
        return {"status": "excluded", "reason": reason, "match": ref}
    return {"status": "ok", "path": out, "size": os.path.getsize(out),
            "patch": patch_of(rec.get("gameVersion")),
            "length_s": rec.get("gameLength_s")}


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None
