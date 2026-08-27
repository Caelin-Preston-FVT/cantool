"""
parseCANEdge.py — Concatenate and DBC-decode MF4 files from a CANEdge logger.

Mirrors the asammdf GUI 8.8.9 batch workflow:
  1. Open each raw MF4 file as-is (no preprocessing).
  2. Concatenate with sync=False — timestamps are never shifted or stitched.
  3. Files whose virtual-group structure differs from the majority are skipped.
  4. Decode the merged file via extract_bus_logging at MDF version 4.11.

Usage:
    python parseCANEdge.py --mf4 <folder> [--dbc <folder>] [--output <name>]
"""

import argparse
import sys
import traceback
from collections import Counter
from pathlib import Path

from asammdf import MDF
from asammdf.blocks.v4_constants import CompressionAlgorithm

# ── CLI ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(
    description="Concatenate and DBC-decode CANEdge MF4 files."
)
parser.add_argument(
    "--mf4", dest="mf4_folder", required=True,
    help="Folder containing raw .mf4 files",
)
parser.add_argument(
    "--dbc", dest="dbc_folder", default="./dbc",
    help="Folder containing .dbc files (default: ./dbc)",
)
parser.add_argument(
    "--output", dest="output_name", default="merged",
    help="Base name for output files, without extension (default: merged)",
)
parser.add_argument(
    "--max-batch-mb", dest="max_batch_mb", type=float, default=100000.0,
    help="Maximum total raw MF4 size per batch in MB (default: 100)",
)
args = parser.parse_args()

# ── PATHS ────────────────────────────────────────────────────────────────────

mf4_folder = Path(args.mf4_folder).resolve()
dbc_folder  = Path(args.dbc_folder).resolve()
base_dir    = mf4_folder.parent

merged_dir  = base_dir / "merged"
decoded_dir = base_dir / "decoded"
merged_dir.mkdir(parents=True, exist_ok=True)
decoded_dir.mkdir(parents=True, exist_ok=True)
max_batch_bytes = int(args.max_batch_mb * 1024 * 1024)

if max_batch_bytes <= 0:
    raise ValueError("--max-batch-mb must be greater than 0")

# ── DBC FILES ────────────────────────────────────────────────────────────────

dbc_files = sorted(dbc_folder.glob("*.dbc"))
if not dbc_files:
    raise FileNotFoundError(f"No .dbc files found in {dbc_folder}")

dbc_map = [(str(f), 0) for f in dbc_files]
print(f"\n[DBC] {len(dbc_map)} file(s):", flush=True)
for path, _ in dbc_map:
    print(f"  {path}", flush=True)

# ── FIND MF4 FILES ───────────────────────────────────────────────────────────
# Exclude bus_logging companion files and any previously generated outputs.

_EXCLUDE_STEMS = ("bus_logging", "decoded", "merged")

mf4_files = sorted(
    (
        p for p in mf4_folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() == ".mf4"
        and not any(tag in p.stem.lower() for tag in _EXCLUDE_STEMS)
    ),
    key=lambda p: p.name.lower(),
)

if not mf4_files:
    raise FileNotFoundError(f"No usable .mf4 files found in {mf4_folder}")

print(f"\n[MF4] {len(mf4_files)} file(s) to process:", flush=True)
for f in mf4_files:
    print(f"  {f.name} ({f.stat().st_size / (1024 * 1024):.2f} MB)", flush=True)


def _chunk_by_size(files: list[Path], max_bytes: int) -> list[list[Path]]:
    """Split files into ordered chunks where each chunk total is <= max_bytes (when possible)."""
    chunks: list[list[Path]] = []
    current_chunk: list[Path] = []
    current_size = 0

    for path in files:
        file_size = path.stat().st_size

        if current_chunk and current_size + file_size > max_bytes:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0

        current_chunk.append(path)
        current_size += file_size

        if file_size > max_bytes:
            print(
                f"[BATCH] WARNING: {path.name} is {file_size / (1024 * 1024):.2f} MB and exceeds the per-batch limit by itself.",
                flush=True,
            )

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _vg_signature(mdf: MDF) -> tuple:
    """Order-independent fingerprint of virtual-group channel names."""
    sig = []
    for vg in mdf.virtual_groups:
        names = frozenset(
            mdf.groups[gp_idx].channels[ch_idx].name
            for gp_idx, ch_indexes in mdf.included_channels(vg)[vg].items()
            for ch_idx in ch_indexes
        )
        sig.append(names)
    return tuple(sig)


def _process_batch(batch_files: list[Path], batch_index: int, batch_count: int) -> bool:
    """Merge and decode one batch. Returns True when successful, False when skipped."""
    batch_total_bytes = sum(p.stat().st_size for p in batch_files)
    batch_suffix = f"{args.output_name}.{batch_index}"
    merged_path = merged_dir / f"{batch_suffix}.mf4"
    decoded_path = decoded_dir / f"{batch_suffix}_decoded.mf4"

    print(
        f"\n[BATCH {batch_index}/{batch_count}] {len(batch_files)} file(s), total {batch_total_bytes / (1024 * 1024):.2f} MB",
        flush=True,
    )
    for f in batch_files:
        print(f"  {f.name}", flush=True)

    # Identical to GUI _as_mdf: plain MDF() open, no preprocessing.
    print(f"[BATCH {batch_index}] Opening files ...", flush=True)
    mdfs: list[MDF] = []
    for f in batch_files:
        try:
            m = MDF(f)
            print(f"  opened  {f.name}  ({len(m.virtual_groups)} virtual group(s))", flush=True)
            mdfs.append(m)
        except Exception as e:
            print(f"  SKIP    {f.name}  -- could not open: {e}", flush=True)

    if not mdfs:
        print(f"[BATCH {batch_index}] SKIP: no MF4 files could be opened.", flush=True)
        return False

    sig_counts = Counter(_vg_signature(m) for m in mdfs)
    dominant, _ = sig_counts.most_common(1)[0]

    compatible = [m for m in mdfs if _vg_signature(m) == dominant]
    skipped = [m for m in mdfs if _vg_signature(m) != dominant]

    if skipped:
        print(
            f"[BATCH {batch_index}] Skipping {len(skipped)} file(s) -- incompatible channel-group structure:",
            flush=True,
        )
        for m in skipped:
            print(f"  {m.name.name}", flush=True)

    print(f"[BATCH {batch_index}] Concatenating {len(compatible)} file(s) ...", flush=True)
    try:
        merged = MDF.concatenate(
            compatible,
            version="4.11",
            sync=True,
            add_samples_origin=True,
        )
    except BaseException as exc:
        print(f"\n[BATCH {batch_index}] MERGE FAILED: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        for m in mdfs:
            try:
                m.close()
            except Exception:
                pass
        return False
    else:
        for m in mdfs:
            try:
                m.close()
            except Exception:
                pass

    print(f"[BATCH {batch_index}] Concatenate done, saving ...", flush=True)
    try:
        saved_merged = Path(merged.save(str(merged_path)) or merged_path)
    except BaseException as exc:
        print(f"\n[BATCH {batch_index}] MERGE SAVE FAILED: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        try:
            merged.close()
        except Exception:
            pass
        return False
    merged.close()
    print(f"[BATCH {batch_index}] Merged saved -> {saved_merged}", flush=True)

    if saved_merged != merged_path:
        decoded_path = decoded_dir / f"{saved_merged.stem}_decoded{saved_merged.suffix}"
        print(
            f"[BATCH {batch_index}] Decode output name updated to match merged save name -> {decoded_path.name}",
            flush=True,
        )

    print(f"[BATCH {batch_index}] Decoding CAN signals from {saved_merged.name} ...", flush=True)
    raw = MDF(str(saved_merged))
    try:
        decoded = raw.extract_bus_logging(
            database_files={"CAN": dbc_map},
            version="4.11",
            ignore_value2text_conversion=True,
        )
    finally:
        raw.close()

    print(f"[BATCH {batch_index}] Decode done, saving ...", flush=True)
    try:
        saved_decoded = Path(
            decoded.save(
                str(decoded_path),
                overwrite=True,
                compression=CompressionAlgorithm.TRANSPOSED_DEFLATE,
            )
            or decoded_path
        )
    finally:
        decoded.close()

    print(f"[BATCH {batch_index}] Decoded saved -> {saved_decoded}", flush=True)
    return True


batches = _chunk_by_size(mf4_files, max_batch_bytes)

print(
    f"\n[BATCH] Prepared {len(batches)} batch(es) with max size {args.max_batch_mb:.2f} MB.",
    flush=True,
)

success_count = 0
for index, batch in enumerate(batches, start=1):
    if _process_batch(batch, index, len(batches)):
        success_count += 1

print(
    f"\n[DONE] Processed {len(batches)} batch(es): {success_count} succeeded, {len(batches) - success_count} skipped/failed.",
    flush=True,
)

if success_count == 0:
    sys.exit(1)
