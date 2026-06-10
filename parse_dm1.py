"""Parse J1939 DM1 diagnostics (SPN/FMI) from decoded MF4 files.

This script expects files produced by asammdf extract_bus_logging, where TP.CM and
TP.DT signals are already decoded into channels such as:
  - ...ControlByte
  - ...TotalMessageSizeBytes
  - ...TotalNumberOfPackets
  - ...PGNOfPacketedMessage
  - ...SequenceNumberTPDT
  - ...PacketizedDataTPDT

It reconstructs DM1 payloads from BAM/TP transport and extracts DTC entries.
"""

from __future__ import annotations

import argparse
import bisect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from asammdf import MDF

DM1_PGN = 0xFECA
TP_CM_PGN = 0xEC00
TP_DT_PGN = 0xEB00

# User-provided IDs often include an extra flag bit. Keep only the 29-bit CAN ID.
ID_29_MASK = 0x1FFFFFFF


@dataclass
class TpCmChannels:
    bus: str
    can_id: int
    sa: int
    da: int
    prefix: str
    time_ch: str
    control_ch: str
    size_ch: str
    packets_ch: str
    pgn_ch: str


@dataclass
class TpDtChannels:
    bus: str
    can_id: int
    sa: int
    da: int
    prefix: str
    time_ch: str
    seq_ch: str
    payload_ch: str


def pgn_from_id(can_id_29: int) -> int:
    pf = (can_id_29 >> 16) & 0xFF
    ps = (can_id_29 >> 8) & 0xFF
    dp = (can_id_29 >> 24) & 0x01
    if pf < 240:
        return (dp << 16) | (pf << 8)
    return (dp << 16) | (pf << 8) | ps


def sa_from_id(can_id_29: int) -> int:
    return can_id_29 & 0xFF


def da_from_id(can_id_29: int) -> int:
    return (can_id_29 >> 8) & 0xFF


def parse_prefix(prefix: str) -> Tuple[str, int] | None:
    match = re.match(r"^(?P<bus>.+?)\.CAN_DataFrame\.ID=0x(?P<id>[0-9A-Fa-f]+) EXT=True$", prefix)
    if not match:
        return None
    bus = match.group("bus")
    can_id = int(match.group("id"), 16) & ID_29_MASK
    return bus, can_id


def decode_tpdt_payload7(sample_u64: int) -> bytes:
    # DBC represents bytes 2..8 as a little-endian uint64 container.
    # TP.DT data payload is exactly 7 bytes.
    return int(sample_u64).to_bytes(8, byteorder="little", signed=False)[:7]


def parse_dm1_payload(dm1_payload: bytes) -> List[Dict[str, int]]:
    dtcs: List[Dict[str, int]] = []
    if len(dm1_payload) < 2:
        return dtcs

    lamp_status = dm1_payload[0]
    lamp_flash = dm1_payload[1]

    i = 2
    while i + 3 < len(dm1_payload):
        b1, b2, b3, b4 = dm1_payload[i : i + 4]
        i += 4

        if b1 == 0xFF and b2 == 0xFF and b3 == 0xFF and b4 == 0xFF:
            continue

        spn = b1 | (b2 << 8) | ((b3 & 0xE0) << 11)
        fmi = b3 & 0x1F
        oc = b4 & 0x7F
        cm = (b4 >> 7) & 0x01

        dtcs.append(
            {
                "spn": spn,
                "fmi": fmi,
                "oc": oc,
                "cm": cm,
                "lamp_status": lamp_status,
                "lamp_flash": lamp_flash,
            }
        )

    return dtcs


def find_tp_channels(channel_names: Iterable[str]) -> Tuple[List[TpCmChannels], List[TpDtChannels]]:
    names = set(channel_names)
    cm_channels: List[TpCmChannels] = []
    dt_channels: List[TpDtChannels] = []

    for name in names:
        if name.endswith(".ControlByte"):
            prefix = name[: -len(".ControlByte")]
            parsed = parse_prefix(prefix)
            if not parsed:
                continue
            bus, can_id = parsed
            if pgn_from_id(can_id) != TP_CM_PGN:
                continue

            needed = [
                f"{prefix}.time",
                f"{prefix}.TotalMessageSizeBytes",
                f"{prefix}.TotalNumberOfPackets",
                f"{prefix}.PGNOfPacketedMessage",
            ]
            if not all(ch in names for ch in needed):
                continue

            cm_channels.append(
                TpCmChannels(
                    bus=bus,
                    can_id=can_id,
                    sa=sa_from_id(can_id),
                    da=da_from_id(can_id),
                    prefix=prefix,
                    time_ch=f"{prefix}.time",
                    control_ch=name,
                    size_ch=f"{prefix}.TotalMessageSizeBytes",
                    packets_ch=f"{prefix}.TotalNumberOfPackets",
                    pgn_ch=f"{prefix}.PGNOfPacketedMessage",
                )
            )

        elif name.endswith(".SequenceNumberTPDT"):
            prefix = name[: -len(".SequenceNumberTPDT")]
            parsed = parse_prefix(prefix)
            if not parsed:
                continue
            bus, can_id = parsed
            if pgn_from_id(can_id) != TP_DT_PGN:
                continue

            needed = [f"{prefix}.time", f"{prefix}.PacketizedDataTPDT"]
            if not all(ch in names for ch in needed):
                continue

            dt_channels.append(
                TpDtChannels(
                    bus=bus,
                    can_id=can_id,
                    sa=sa_from_id(can_id),
                    da=da_from_id(can_id),
                    prefix=prefix,
                    time_ch=f"{prefix}.time",
                    seq_ch=name,
                    payload_ch=f"{prefix}.PacketizedDataTPDT",
                )
            )

    return cm_channels, dt_channels


def collect_dt_events(mdf: MDF, dt_channels: List[TpDtChannels]) -> Dict[Tuple[str, int, int], List[Tuple[float, int, bytes]]]:
    events: Dict[Tuple[str, int, int], List[Tuple[float, int, bytes]]] = {}
    for ch in dt_channels:
        times = mdf.get(ch.time_ch).samples
        seqs = mdf.get(ch.seq_ch).samples
        payloads = mdf.get(ch.payload_ch).samples

        key = (ch.bus, ch.sa, ch.da)
        rows: List[Tuple[float, int, bytes]] = []
        for t, seq, payload in zip(times, seqs, payloads):
            rows.append((float(t), int(seq), decode_tpdt_payload7(int(payload))))

        rows.sort(key=lambda x: x[0])
        events[key] = rows

    return events


def parse_file(path: Path, ref_cm_id_29: int, ref_dt_id_29: int) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    mdf = MDF(str(path))
    try:
        cm_channels, dt_channels = find_tp_channels(mdf.channels_db.keys())
        cm_channels = [ch for ch in cm_channels if ch.can_id == ref_cm_id_29]
        dt_channels = [ch for ch in dt_channels if ch.can_id == ref_dt_id_29]
        if not cm_channels or not dt_channels:
            return records

        dt_events_by_key = collect_dt_events(mdf, dt_channels)
        dt_times_by_key = {
            key: [row[0] for row in rows] for key, rows in dt_events_by_key.items()
        }

        for cm in cm_channels:
            key = (cm.bus, cm.sa, cm.da)
            dt_rows = dt_events_by_key.get(key)
            dt_times = dt_times_by_key.get(key)
            if not dt_rows or not dt_times:
                continue

            times = mdf.get(cm.time_ch).samples
            controls = mdf.get(cm.control_ch).samples
            sizes = mdf.get(cm.size_ch).samples
            packets = mdf.get(cm.packets_ch).samples
            pgns = mdf.get(cm.pgn_ch).samples

            for t, control, size, packet_count, payload_pgn in zip(times, controls, sizes, packets, pgns):
                control_i = int(control)
                size_i = int(size)
                packet_count_i = int(packet_count)
                payload_pgn_i = int(payload_pgn) & 0xFFFFFF

                # BAM transport of DM1 payload.
                if control_i != 32 or payload_pgn_i != DM1_PGN:
                    continue
                if packet_count_i <= 0 or size_i <= 0:
                    continue

                start_t = float(t)
                # Next CM/DT pair usually follows almost immediately. Keep a strict window.
                end_t = start_t + 1.0

                i0 = bisect.bisect_left(dt_times, start_t)
                by_seq: Dict[int, bytes] = {}

                i = i0
                while i < len(dt_rows):
                    dt_t, dt_seq, dt_payload7 = dt_rows[i]
                    if dt_t > end_t:
                        break
                    if 1 <= dt_seq <= packet_count_i and dt_seq not in by_seq:
                        by_seq[dt_seq] = dt_payload7
                        if len(by_seq) >= packet_count_i:
                            break
                    i += 1

                if len(by_seq) < packet_count_i:
                    continue

                assembled = b"".join(by_seq[idx] for idx in range(1, packet_count_i + 1))
                dm1_payload = assembled[:size_i]

                dtcs = parse_dm1_payload(dm1_payload)
                if not dtcs:
                    records.append(
                        {
                            "file": path.name,
                            "time": start_t,
                            "bus": cm.bus,
                            "sa": cm.sa,
                            "spn": None,
                            "fmi": None,
                            "oc": None,
                            "cm": None,
                            "lamp_status": dm1_payload[0] if dm1_payload else None,
                            "lamp_flash": dm1_payload[1] if len(dm1_payload) > 1 else None,
                            "transport": "TP",
                        }
                    )
                    continue

                for dtc in dtcs:
                    records.append(
                        {
                            "file": path.name,
                            "time": start_t,
                            "bus": cm.bus,
                            "sa": cm.sa,
                            "spn": dtc["spn"],
                            "fmi": dtc["fmi"],
                            "oc": dtc["oc"],
                            "cm": dtc["cm"],
                            "lamp_status": dtc["lamp_status"],
                            "lamp_flash": dtc["lamp_flash"],
                            "transport": "TP",
                        }
                    )
    finally:
        mdf.close()

    return records


def iter_input_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.mf4") if p.is_file())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse DM1 SPN/FMI from decoded MF4 files")
    parser.add_argument(
        "--input",
        default="decoded",
        help="MF4 file or folder to scan (default: decoded)",
    )
    parser.add_argument(
        "--cm-id",
        type=lambda x: int(x, 0),
        default=2632777680,
        help="Reference DM1 TP.CM CAN ID (decimal or hex)",
    )
    parser.add_argument(
        "--dt-id",
        type=lambda x: int(x, 0),
        default=2632712144,
        help="Reference DM1 TP.DT CAN ID (decimal or hex)",
    )
    parser.add_argument(
        "--sf-id",
        type=lambda x: int(x, 0),
        default=2566834896,
        help="Reference DM1 single-frame CAN ID (decimal or hex)",
    )
    return parser.parse_args()


def print_records(records: List[Dict[str, object]]) -> None:
    if not records:
        print("No DM1 records found.")
        return

    print("\nDM1 occurrences:")
    print("file,time_s,bus,sa,spn,fmi,oc,cm,lamp_status,lamp_flash,transport")
    for r in records:
        print(
            f"{r['file']},{r['time']:.6f},{r['bus']},0x{int(r['sa']):02X},"
            f"{r['spn']},{r['fmi']},{r['oc']},{r['cm']},"
            f"{r['lamp_status']},{r['lamp_flash']},{r['transport']}"
        )

    counts: Dict[Tuple[int, int], int] = {}
    for r in records:
        spn = r["spn"]
        fmi = r["fmi"]
        if spn is None or fmi is None:
            continue
        key = (int(spn), int(fmi))
        counts[key] = counts.get(key, 0) + 1

    if counts:
        print("\nUnique SPN/FMI pairs:")
        print("spn,fmi,count")
        for (spn, fmi), count in sorted(counts.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
            print(f"{spn},{fmi},{count}")


def main() -> None:
    args = parse_args()

    cm_id_29 = args.cm_id & ID_29_MASK
    dt_id_29 = args.dt_id & ID_29_MASK
    sf_id_29 = args.sf_id & ID_29_MASK

    print("Using reference IDs (29-bit normalized):")
    print(f"  DM1 CM: 0x{cm_id_29:X}")
    print(f"  DM1 DT: 0x{dt_id_29:X}")
    print(f"  DM1 SF: 0x{sf_id_29:X}")

    in_path = Path(args.input)
    files = iter_input_files(in_path)
    if not files:
        raise FileNotFoundError(f"No .mf4 files found in {in_path}")

    all_records: List[Dict[str, object]] = []
    for file_path in files:
        try:
            recs = parse_file(file_path, cm_id_29, dt_id_29)
            if recs:
                all_records.extend(recs)
        except Exception as exc:
            print(f"[WARN] Failed to parse {file_path}: {exc}")

    print_records(all_records)


if __name__ == "__main__":
    main()
