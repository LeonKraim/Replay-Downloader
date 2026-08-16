#!/usr/bin/env python3
"""Professional validation + manifest builder for the replay collection.

Scans downloads/*.rofl, parses each with rofl_parser (magic, gameVersion,
zstd chunk walk, statsJson SR-inference), and emits:

  meta/manifest.json   - authoritative machine-readable manifest
  meta/manifest.csv    - human-readable summary
  meta/validation.tsv  - full per-file parse detail

A file is "valid" only if ALL hold:
  * ROFL2 magic ok and full parse ok (fileFormat 2, metadata JSON, chunk walk)
  * gameVersion major.minor in {16.13, 16.14, 16.15, 16.16}
  * mapId inferred from statsJson == 11 (Summoner's Rift), or mapCode SR

Usage:
    python build_manifest.py [--dir downloads]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rofl_parser  # noqa: E402

VALID_PATCHES = {"16.13", "16.14", "16.15", "16.16"}
SR_MAP_ID = 11
# Reject remakes / instant surrenders: too short to be useful replay data.
MIN_GAME_LENGTH_S = 300


def patch_of(version):
    if not version:
        return None
    parts = version.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None


def build(download_dir):
    files = sorted(glob.glob(os.path.join(download_dir, "*.rofl")))
    records = []
    for path in files:
        rec, err = rofl_parser.parse_replay(open(path, "rb").read(), path)
        if err:
            rec["valid"] = False
            rec["reject_reason"] = err
        else:
            patch = patch_of(rec.get("gameVersion"))
            map_ok = rec.get("mapId_inferred") == SR_MAP_ID or rec.get("mapCode_inferred") == "SR"
            patch_ok = patch in VALID_PATCHES
            duration_ok = (rec.get("gameLength_s") or 0) >= MIN_GAME_LENGTH_S
            rec["patch"] = patch
            rec["is_sr"] = bool(map_ok)
            rec["patch_ok"] = bool(patch_ok)
            rec["valid"] = bool(rec.get("magic_ok")) and map_ok and patch_ok and duration_ok
            rec["reject_reason"] = (
                "not-SR" if not map_ok else
                "patch-" + (patch or "?") if not patch_ok else
                "remake-<300s" if not duration_ok else ""
            )
        records.append(rec)

    # -- manifest.json --------------------------------------------------------
    manifest = {
        "collection": "lol-sr-replays",
        "title": "Summoner's Rift .rofl replay collection (patches 16.13-16.16)",
        "target_patches": sorted(VALID_PATCHES),
        "created": datetime.now(timezone.utc).isoformat(),
        "total_files": len(files),
        "valid": sum(1 for r in records if r["valid"]),
        "invalid": sum(1 for r in records if not r["valid"]),
        "files": [],
    }
    for r in records:
        if r["valid"]:
            manifest["files"].append({
                "filename": os.path.basename(r["filename"]),
                "platformId": r.get("platformId"),
                "gameId": r.get("gameId"),
                "gameVersion": r.get("gameVersion"),
                "patch": r.get("patch"),
                "mapId_inferred": r.get("mapId_inferred"),
                "mapCode_inferred": r.get("mapCode_inferred"),
                "gameLength_s": r.get("gameLength_s"),
                "fileFormat": r.get("fileFormat"),
                "n_chunks": r.get("n_chunks"),
                "zstd_roundtrip_ok": r.get("zstd_roundtrip_ok"),
                "zstd_chunks_tested": r.get("zstd_chunks_tested"),
                "size_bytes": r.get("size"),
                "sha256": r.get("sha256"),
            })
    manifest["patch_counts"] = {}
    for r in records:
        if r["valid"]:
            p = r.get("patch")
            manifest["patch_counts"][p] = manifest["patch_counts"].get(p, 0) + 1

    meta_dir = os.path.join(ROOT, "meta")
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(meta_dir, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filename", "platform", "gameId", "gameVersion", "patch", "mapId",
                    "gameLength_s", "size_bytes", "sha256", "n_chunks", "zstd_ok"])
        for r in records:
            if r["valid"]:
                w.writerow([
                    os.path.basename(r["filename"]), r.get("platformId"), r.get("gameId"),
                    r.get("gameVersion"), r.get("patch"), r.get("mapId_inferred"),
                    r.get("gameLength_s"), r.get("size"), r.get("sha256"),
                    r.get("n_chunks"), r.get("zstd_roundtrip_ok"),
                ])
    with open(os.path.join(meta_dir, "validation.tsv"), "w", encoding="utf-8", newline="") as f:
        f.write(rofl_parser.TSV_HEADER + "\tvalid\treject_reason\n")
        for r in records:
            f.write(rofl_parser.tsv_row(r) + f"\t{r['valid']}\t{r.get('reject_reason','')}\n")

    print(f"files={manifest['total_files']} valid={manifest['valid']} "
          f"invalid={manifest['invalid']}")
    print("patch_counts:", manifest["patch_counts"])
    for r in records:
        if not r["valid"]:
            print("  INVALID", os.path.basename(r["filename"]),
                  "->", r.get("reject_reason") or r.get("notes"))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "downloads"))
    args = ap.parse_args()
    build(args.dir)
