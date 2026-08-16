"""Replay Downloader: collect League of Legends replay files.

The tool uses the running League client to authenticate, then downloads
replays (ROFL2 files) from Riot's Spectator Game Protocol (SGP) edge.
"""
from __future__ import annotations

import os
import sys as _sys

# In a PyInstaller bundle the Chromium browser ships inside the exe (see
# collector.spec). Point Playwright at that bundled location so the user does
# not need to run `python -m playwright install chromium`. Outside a bundle,
# keep the standard user cache (%LOCALAPPDATA%\ms-playwright).
if getattr(_sys, "frozen", False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
        getattr(_sys, "_MEIPASS", os.path.dirname(_sys.executable)),
        "ms-playwright",
    )
else:
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "ms-playwright"),
    )

__version__ = "0.1.0"
