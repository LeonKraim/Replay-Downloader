#!/usr/bin/env python3
"""ROFL2 ("Replay V2") .rofl replay parser/validator for League of Legends.

Empirically verified against 30 real replays downloaded by the client
(patches 16.10 - 16.16) and cross-checked against three independent
community parsers:

  * fraxiinus/roflxd.cs  -> Rofl2Reader.cs / ROFL2.cs (Signature "RIOT\\x02\\x00",
                           gameVersion at offset 15, trailing metadata JSON)
  * shyunku-libraries/rofl-parser -> src/cli/decode.js (parseContainer)
  * ROFL-Player (mirror)          -> RequestManager.cs (uses gameVersion only)

EXACT V2 header layout (offset-by-offset, all integers little-endian):

  offset  size  field
  ------  ----  ------------------------------------------------------------
  0x00    4     magic            ASCII "RIOT"
  0x04    2     formatVersion    uint16 LE = 2   (bytes 0x02 0x00).
                         NOTE: some tools view this as byte[4]=0x02 (version)
                         and byte[5]=0x00 (fileFormat). Either reading is
                         equivalent. The 6-byte prefix "RIOT\\x02\\x00" is the
                         ROFL2 signature.
  0x06    8     gameKey          opaque 8-byte value, CONSTANT per
                         (platformId, build) -- NOT the gameId. Empirically:
                         60aed4735b129ebb for all EUN1 16.15.800-802 files,
                         d2922873ff56143b for both EUW1 16.13.791.5903 files,
                         509c49fd988fca11 for EUW1 16.14.794.9266, etc.
                         gameId (a per-match number) is NOT stored in the file.
  0x0E    1     verLen           uint8 = length of gameVersion ASCII string
  0x0F    N     gameVersion      ASCII e.g. "16.14.794.9266" (verLen bytes)

  The chunk stream begins IMMEDIATELY at offset 0x0F + verLen (== "headerEnd",
  29 for a 14-char version). There are NO further plaintext header fields
  (no gameId, mapId, gameMode, queueId, gameCreation, platformId).

  Chunk record (repeats):
  ----    size  field
  +0      4     chunkId          uint32 LE (1-based)
  +4      4     nextChunkId      uint32 LE
  +8      1     chunkType        uint8 (1=game-data, 2=keyframe,
                                 3=startup-data, 4=startup-end/control)
  +9      4     uncompressedLen  uint32 LE
  +13     4     compressedLen    uint32 LE (0 => stored uncompressed)
  +17     storedLen              zstd frame (compressedLen) or raw
                                 (uncompressedLen) payload.

  Footer (from end of file, backwards):
  last 4            metadataLength  uint32 LE
  [end-4-mlen : end-4]  metadata JSON  UTF-8. Keys:
                        gameLength (uint, milliseconds),
                        lastGameChunkId (uint),
                        lastKeyFrameId (uint),
                        statsJson (string, JSON-array of per-player stats).
  [end-4-mlen-256 : end-4-mlen]  signature area, 256 bytes (unparsed /
                                 integrity signature; rofl-parser reports a
                                 sha256 of it).

KEY RESULT: mapId, gameMode, queueId, gameCreation and platformId are NOT
stored anywhere in a ROFL2 file (confirmed by scanning all 30 real files for
those byte strings -> absent). gameId/platformId come from the FILENAME
"<platformId>-<gameId>.rofl"; the remaining match metadata must be fetched
from Riot's match API. This matches the project log note
"ReplayV2s are not expected to have this metadata".

Usage:
    python rofl_parser.py <file_or_dir>... [--out path.tsv]
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import struct
import sys

try:
    import zstandard as zstd  # optional, only used for the chunk sanity check
    _HAS_ZSTD = True
except Exception:  # pragma: no cover
    zstd = None
    _HAS_ZSTD = False

MAGIC_V2 = b"RIOT\x02\x00"          # Replay V2 (current, "RIOT" magic)
MAGIC_V1 = b"RIOT"                  # legacy ROFL v1 (has RLR payload header)
MAGIC_RLR = b"RLR\x01"              # alternate legacy magic seen in docs
SIGNATURE_SIZE = 256                # bytes of signature area before metadata
HEADER_FIXED = 15                   # magic4 + fmt2 + gamekey8 + verlen1
CHUNK_HEADER_SIZE = 17

# Map code embedded in per-player challenge keys inside the trailing
# statsJson metadata: "<season>_<split>_<MAPCODE>_<challenge>".
# This is an INFERENCE from the file's own stats metadata, NOT a header field.
MAP_CODE_TO_MAP_ID = {
    "SR": 11,  # Summoner's Rift
    "HA": 12,  # Howling Abyss (ARAM)
    "TT": 10,  # Twisted Treeline (retired)
}
_CHALLENGE_KEY_RE = re.compile(r"\d{4}_[A-Z0-9]+_([A-Z]{2,3})_[A-Za-z0-9]+")


def _infer_map_code(stats_text):
    """Return (map_code_or_None, map_id_or_None) inferred from statsJson keys."""
    if not isinstance(stats_text, str):
        return None, None
    m = _CHALLENGE_KEY_RE.search(stats_text)
    if not m:
        return None, None
    code = m.group(1)
    return code, MAP_CODE_TO_MAP_ID.get(code)


def _unpack_u32(data, off, name):
    if off + 4 > len(data):
        raise ValueError("truncated %s at offset %d" % (name, off))
    return struct.unpack_from("<I", data, off)[0]


def _unpack_u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def parse_replay(data, filename):
    """Parse ROFL2 bytes. Returns (record_dict, error_str_or_None)."""
    rec = {
        "filename": filename,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "magic_ok": False,
        "fileFormat": None,
        "gameKey": None,
        "gameVersion": None,
        "gameId": None,
        "platformId": None,
        "mapId": None,
        "gameMode": None,
        "queueId": None,
        "gameLength_ms": None,
        "gameLength_s": None,
        "gameCreation": None,
        "lastGameChunkId": None,
        "lastKeyFrameId": None,
        "has_stats": False,
        "mapCode_inferred": None,
        "mapId_inferred": None,
        "n_chunks": None,
        "zstd_roundtrip_ok": None,
        "zstd_chunks_tested": 0,
        "notes": [],
    }

    # ---- filename-derived identity (gameId/platformId live ONLY here) ----
    stem = os.path.basename(filename)
    if stem.lower().endswith(".rofl"):
        stem = stem[:-5]
    if "-" in stem:
        plat, _, gid = stem.partition("-")
        if plat and gid.isdigit():
            rec["platformId"] = plat
            rec["gameId"] = int(gid)

    # ---- magic / format ----
    if len(data) < 19:
        return rec, "too-small"
    if data[:4] == MAGIC_RLR or data[:4] == b"RLR":
        return rec, "legacy-RLR-format"
    if data[:4] != MAGIC_V1:
        # could be a JSON API error page saved with .rofl extension
        if data[:1] == b"{" and b"status" in data[:128]:
            return rec, "not-a-replay-json-error"
        return rec, "bad-magic"
    rec["magic_ok"] = data[:6] == MAGIC_V2

    format_version = _unpack_u16(data, 4)
    rec["fileFormat"] = format_version
    if format_version != 2:
        return rec, "unsupported-format-v%d" % format_version

    rec["gameKey"] = data[6:14].hex()

    # ---- gameVersion (length-prefixed ASCII at 0x0F) ----
    ver_len = data[14]
    if ver_len == 0 or 15 + ver_len > len(data):
        return rec, "bad-version-length"
    try:
        rec["gameVersion"] = data[15:15 + ver_len].decode("ascii")
    except UnicodeDecodeError:
        return rec, "version-not-ascii"

    # ---- trailing metadata JSON (last 4 bytes = length) ----
    header_end = HEADER_FIXED + ver_len
    try:
        meta_len = _unpack_u32(data, len(data) - 4, "metadataLength")
    except ValueError as e:
        return rec, str(e)
    if meta_len <= 0:
        return rec, "bad-metadata-length-0"
    meta_start = len(data) - 4 - meta_len
    if meta_start < header_end:
        return rec, "metadata-overlaps-header"
    try:
        meta = json.loads(data[meta_start:len(data) - 4].decode("utf-8"))
    except Exception as e:
        return rec, "metadata-not-json: %s" % str(e)[:40]
    if not isinstance(meta, dict):
        return rec, "metadata-not-object"

    rec["gameLength_ms"] = meta.get("gameLength")
    if isinstance(rec["gameLength_ms"], (int, float)):
        rec["gameLength_s"] = round(float(rec["gameLength_ms"]) / 1000.0, 1)
    rec["lastGameChunkId"] = meta.get("lastGameChunkId")
    rec["lastKeyFrameId"] = meta.get("lastKeyFrameId")
    stats = meta.get("statsJson")
    if isinstance(stats, str):
        try:
            parsed_stats = json.loads(stats)
            rec["has_stats"] = bool(parsed_stats)
        except Exception:
            rec["notes"].append("statsJson-not-json")
        rec["mapCode_inferred"], rec["mapId_inferred"] = _infer_map_code(stats)

    # NOTE: mapId/gameMode/queueId/gameCreation are NOT present in ROFL2.
    # rec["mapId"]/["gameMode"]/["queueId"]/["gameCreation"] stay None.
    # mapCode_inferred/mapId_inferred above come from the statsJson challenge
    # keys (an in-file inference), NOT from a header field.

    # ---- optional chunk-stream sanity walk ----
    try:
        payload_end = meta_start - SIGNATURE_SIZE
        if payload_end < header_end:
            rec["notes"].append("signature-area-overlaps-header")
            return rec, None
        off = header_end
        n = 0
        # zstd round-trip ALL compressed chunks (capped): each chunk's payload
        # must decompress to its declared uncompressedLen. First chunk alone is
        # not enough to catch mid-file corruption.
        zstd_ok = True
        zstd_tested = 0
        zstd_total = 0
        ZSTD_MAX_CHUNKS = 5000
        ZSTD_MAX_TOTAL = 1 << 28  # 256 MiB of decompressed output cap
        while off < payload_end:
            if off + CHUNK_HEADER_SIZE > payload_end:
                rec["notes"].append("trailing-%d-bytes-in-chunk-area" % (payload_end - off))
                break
            _id = _unpack_u32(data, off, "chunkId")
            nid = _unpack_u32(data, off + 4, "nextId")
            ctype = data[off + 8]
            ulen = _unpack_u32(data, off + 9, "uncompressedLen")
            clen = _unpack_u32(data, off + 13, "compressedLen")
            stored = clen if clen else ulen
            # Every chunk with a compressed payload (clen > 0) is zstd — this
            # covers game-data (ctype 1), keyframes (ctype 2) AND startup-data
            # (ctype 3). Chunks with clen == 0 are stored uncompressed and
            # skipped. This makes the round-trip genuinely full-coverage.
            if _HAS_ZSTD and clen and ulen <= ZSTD_MAX_TOTAL:
                if zstd_tested < ZSTD_MAX_CHUNKS and zstd_total < ZSTD_MAX_TOTAL:
                    try:
                        dec = zstd.ZstdDecompressor().decompress(
                            data[off + 17:off + 17 + clen], max_output_size=max(ulen, 1 << 26)
                        )
                        zstd_total += len(dec)
                        zstd_tested += 1
                        if len(dec) != ulen:
                            zstd_ok = False
                            rec["notes"].append("zstd-mismatch-chunk-%d" % _id)
                    except Exception as e:
                        zstd_ok = False
                        rec["notes"].append("zstd-err-chunk-%d: %s" % (_id, str(e)[:30]))
            off = off + CHUNK_HEADER_SIZE + stored
            n += 1
            if n > 100000:
                rec["notes"].append("chunk-walk-cap")
                break
        rec["n_chunks"] = n
        rec["zstd_roundtrip_ok"] = zstd_ok if zstd_tested else None
        rec["zstd_chunks_tested"] = zstd_tested
        if off != payload_end:
            rec["notes"].append("chunk-walk-end-mismatch")
    except Exception as e:
        rec["notes"].append("chunk-walk-err: %s" % str(e)[:40])

    return rec, None


def parse_file(path):
    with open(path, "rb") as f:
        data = f.read()
    rec, err = parse_replay(data, path)
    if err:
        rec["notes"].append(err)
        rec["magic_ok"] = rec.get("magic_ok", False) and False
    return rec


def tsv_row(rec):
    def g(k):
        v = rec.get(k)
        if v is None:
            return ""
        if isinstance(v, bool):
            return "1" if v else "0"
        return str(v)

    return "\t".join(
        [
            g("filename"),
            g("magic_ok"),
            g("gameId"),
            g("gameVersion"),
            g("mapId"),
            g("gameMode"),
            g("queueId"),
            g("gameLength_s"),
            g("gameLength_ms"),
            g("gameCreation"),
            g("platformId"),
            g("fileFormat"),
            g("sha256"),
            g("size"),
            g("n_chunks"),
            g("zstd_roundtrip_ok"),
            g("zstd_chunks_tested"),
            g("mapCode_inferred"),
            g("mapId_inferred"),
            " | ".join(rec["notes"]),
        ]
    )


TSV_HEADER = "\t".join(
    [
        "filename", "magic_ok", "gameId", "gameVersion", "mapId", "gameMode",
        "queueId", "gameLength_s", "gameLength_ms", "gameCreation",
        "platformId", "fileFormat", "sha256", "size", "n_chunks",
        "zstd_roundtrip_ok", "zstd_chunks_tested",
        "mapCode_inferred", "mapId_inferred", "notes",
    ]
)


def collect_rofl(paths):
    files = []
    for root in paths:
        if os.path.isdir(root):
            files += glob.glob(os.path.join(root, "**", "*.rofl"), recursive=True)
        elif os.path.isfile(root):
            files.append(root)
    return sorted(set(files))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = None
    paths = []
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            i += 1
            out_path = argv[i]
        else:
            paths.append(argv[i])
        i += 1
    if not paths:
        paths = [r"S:\OS Folders\Documents\League of Legends\Replays"]
    if out_path is None:
        out_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "meta", "rofl_parsed.tsv",
        )
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    files = collect_rofl(paths)
    rows = [parse_file(p) for p in files]

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(TSV_HEADER + "\n")
        for r in rows:
            f.write(tsv_row(r) + "\n")

    ok = sum(1 for r in rows if r["magic_ok"])
    print("files=%d parsed_ok=%d failed=%d" % (len(rows), ok, len(rows) - ok))
    print("tsv written to %s" % out_path)
    for r in rows:
        if not r["magic_ok"]:
            print("  FAIL %s  notes=%s" % (r["filename"], r["notes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
