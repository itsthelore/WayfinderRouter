#!/usr/bin/env python3
"""Generate the menu-bar tray icons: the Wayfinder W as a monochrome *template* image in three
health states (WF-DESIGN-0012 amendment; WF-ROADMAP-0009 Phase 3).

macOS template icons are black + alpha — the system tints them for the menu-bar appearance, so
the W's *shape* carries the state, never colour:
  running  -> solid W
  degraded -> solid W with a notch chipped from the centre peak
  stopped  -> thin outline (hollow) W

Pure stdlib (no Pillow): a supersampled distance-field rasteriser + a hand-rolled RGBA PNG
writer, so the art regenerates anywhere with `python3 make-tray-icons.py`. Deterministic —
re-running produces an identical byte stream.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

# The W as a polyline in a normalised [0,1] box (y down): top-left, bottom valley, centre peak,
# bottom valley, top-right — the classic \/\/ letterform.
W = [(0.08, 0.12), (0.34, 0.90), (0.50, 0.44), (0.66, 0.90), (0.92, 0.12)]
HALF_SOLID = 0.115  # stroke half-width for running / degraded
HALF_THIN = 0.05    # stroke half-width for the hollow "stopped" state
# The degraded notch: a downward wedge biting into the centre peak.
NOTCH = [(0.50, 0.30), (0.41, 0.56), (0.59, 0.56)]

SS = 4  # supersampling factor for anti-aliasing


def _dist_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _in_triangle(px: float, py: float, tri: list[tuple[float, float]]) -> bool:
    (ax, ay), (bx, by), (cx, cy) = tri
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
    d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _alpha(nx: float, ny: float, half: float, notch: bool) -> bool:
    if notch and _in_triangle(nx, ny, NOTCH):
        return False
    return any(
        _dist_to_segment(nx, ny, *W[i], *W[i + 1]) <= half for i in range(len(W) - 1)
    )


def render(size: int, half: float, notch: bool) -> bytes:
    hi = size * SS
    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            covered = 0
            for sy in range(SS):
                for sx in range(SS):
                    nx = (x * SS + sx + 0.5) / hi
                    ny = (y * SS + sy + 0.5) / hi
                    if _alpha(nx, ny, half, notch):
                        covered += 1
            a = round(255 * covered / (SS * SS))
            px[(y * size + x) * 4 + 3] = a  # black RGB (0,0,0), alpha carries the shape
    return bytes(px)


def write_png(path: Path, size: int, rgba: bytes) -> None:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0
        raw += rgba[y * size * 4 : (y + 1) * size * 4]
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


STATES = {
    "running": (HALF_SOLID, False),
    "degraded": (HALF_SOLID, True),
    "stopped": (HALF_THIN, False),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for state, (half, notch) in STATES.items():
        for size, suffix in ((22, ""), (44, "@2x")):
            path = OUT / f"tray-{state}-Template{suffix}.png"
            write_png(path, size, render(size, half, notch))
            print(f"  wrote {path.name}")


if __name__ == "__main__":
    main()
