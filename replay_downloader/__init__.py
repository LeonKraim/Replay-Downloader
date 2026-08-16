"""Replay Downloader: collect League of Legends replay files.

The tool uses the running League client to authenticate, then downloads
replays (ROFL2 files) from Riot's Spectator Game Protocol (SGP) edge.
"""
from __future__ import annotations

import os

# In a PyInstaller bundle the Playwright driver cannot find the real browser
# cache on its own, so point it at the standard user cache
# (%LOCALAPPDATA%\ms-playwright). Respect a user-set PLAYWRIGHT_BROWSERS_PATH.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"),
)

__version__ = "0.1.0"
