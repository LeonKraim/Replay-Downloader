"""SGP client access.

Re-exports the proven SGP client implementation from the scripts/ directory so
the package never forks a second, possibly-divergent copy. The scripts are part
of this repository and are the source of truth for the authenticated SGP calls.
"""
from __future__ import annotations

import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from sgp_client import (  # noqa: E402,F401
    SGPClient,
    SGPError,
    current_version,
    get_rso_token,
    lockfile_parts,
)


def current_puuid():
    """PUUID of the account currently logged into the League client."""
    from sgp_client import _local_get  # noqa: PLC0415
    d = _local_get("/lol-summoner/v1/current-summoner")
    return d.get("puuid")


__all__ = ["SGPClient", "SGPError", "current_version", "get_rso_token",
           "lockfile_parts", "current_puuid"]
