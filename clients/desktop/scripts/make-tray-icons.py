#!/usr/bin/env python3
"""Generate the menu-bar tray icons: a Wayfinder signpost as a monochrome *template* image in
three health states (WF-DESIGN-0012 amendment; WF-ROADMAP-0009 Phase 3; signpost swap-in for
the original W letterform).

The signpost's point data is traced from lucide-react's own "signpost-big" icon (ISC licensed;
lucide is already this app's icon library — see HelpTip, the send button, etc.) rather than
hand-drawn, for a cleaner, professionally-proportioned shape:
    M10 9H4L2 7l2-2h6            -> left sign
    M14 5h6l2 2-2 2h-6           -> right sign
    M10 22V4a2 2 0 1 1 4 0v18    -> post (the rounded cap collapses to a centreline)
    M8 22h8                      -> ground
in lucide's 24x24 viewBox, each coordinate divided by 24 to land in this file's normalised box.

macOS template icons are black + alpha — the system tints them for the menu-bar appearance, so
the signpost's *shape* carries the state, never colour:
  running  -> thick post/ground, both signs filled solid
  degraded -> thick post/ground, one sign solid and the other hollow (half up, half down —
              a first cut used a small notch chipped from a sign's tip instead; at 22px it
              read as noise, not damage, so this is a full sign standing in for "the other
              half isn't")
  stopped  -> thin post/ground, both signs as a thin outline (hollow)

The post and ground are plain strokes at two widths (thick for running/degraded, thin for
stopped) — the same trick the old W used — but the signs are filled *polygons*, not just a
thick stroke over a small shape: a uniformly thick stroke reduced the whole signpost to a
formless blob, since the two signs sit close enough that thick strokes over their thin
outlines merged into one mass. Filling them explicitly keeps both pennants legible as
distinct shapes at every state. The live meter (`meter_image` in `src-tauri/src/commands.rs`)
row-splices between the running and stopped renders by local-routing-share fill fraction, so
the two need to differ in *every* row band, not just where the signs are — hence the post and
ground varying too, not just the signs.

Pure stdlib (no Pillow): a supersampled rasteriser (segment-distance strokes for the post,
ground, and the "stopped" signs; even-odd point-in-polygon fill for the solid signs) + a
hand-rolled RGBA PNG writer, so the art regenerates anywhere with `python3
make-tray-icons.py`. Deterministic — re-running produces an identical byte stream.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"


def _n(x: float, y: float) -> tuple[float, float]:
    """A lucide 24x24-viewBox coordinate, normalised into this file's [0,1] box."""
    return (x / 24, y / 24)


# SIGN_RIGHT/SIGN_LEFT are pentagons whose edge nearest the post is left undrawn — the
# point-in-polygon fill test below closes it implicitly (last vertex back to first), so the
# coincident post edge is never double-drawn.
SIGN_LEFT = [_n(10, 9), _n(4, 9), _n(2, 7), _n(4, 5), _n(10, 5)]
SIGN_RIGHT = [_n(14, 5), _n(20, 5), _n(22, 7), _n(20, 9), _n(14, 9)]
POST = [_n(12, 4), _n(12, 22)]
GROUND = [_n(8, 22), _n(16, 22)]

HALF_SOLID = 0.09   # post/ground stroke half-width for running / degraded
HALF_THIN = 0.045   # post/ground stroke half-width for stopped; also the hollow sign outline

SS = 4  # supersampling factor for anti-aliasing


def _dist_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _near_polyline(px: float, py: float, poly: list[tuple[float, float]], half: float) -> bool:
    return any(
        _dist_to_segment(px, py, *poly[i], *poly[i + 1]) <= half for i in range(len(poly) - 1)
    )


def _in_polygon(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    """Even-odd ray cast; wraps from the last vertex back to the first, closing the shape."""
    inside = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > py) != (yj > py):
            x_at_y = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_at_y:
                inside = not inside
        j = i
    return inside


def _alpha(nx: float, ny: float, state: str) -> bool:
    half = HALF_THIN if state == "stopped" else HALF_SOLID
    if _near_polyline(nx, ny, POST, half) or _near_polyline(nx, ny, GROUND, half):
        return True
    for sign in (SIGN_RIGHT, SIGN_LEFT):
        hollow = state == "stopped" or (state == "degraded" and sign is SIGN_LEFT)
        if hollow:
            if _near_polyline(nx, ny, sign, HALF_THIN):
                return True
        elif _in_polygon(nx, ny, sign):
            return True
    return False


def render(size: int, state: str) -> bytes:
    hi = size * SS
    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            covered = 0
            for sy in range(SS):
                for sx in range(SS):
                    nx = (x * SS + sx + 0.5) / hi
                    ny = (y * SS + sy + 0.5) / hi
                    if _alpha(nx, ny, state):
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


STATES = ("running", "degraded", "stopped")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for state in STATES:
        for size, suffix in ((22, ""), (44, "@2x")):
            path = OUT / f"tray-{state}-Template{suffix}.png"
            write_png(path, size, render(size, state))
            print(f"  wrote {path.name}")


if __name__ == "__main__":
    main()
