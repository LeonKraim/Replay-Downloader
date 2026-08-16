#!/usr/bin/env python3
"""Launcher for Replay Downloader.

Run from the repository root:
    python replay-downloader.py gather <player> --max 500
    python replay-downloader.py get EUW1_7923052589
    python replay-downloader.py status
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from replay_downloader.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
