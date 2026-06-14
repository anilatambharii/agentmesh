"""
Generate AgentMesh extension icons (16, 48, 128 px PNG).
Requires Pillow: pip install Pillow

Run from the agentmesh-extension/ directory:
    python generate_icons.py
"""

import math
import os
from PIL import Image, ImageDraw

SIZES   = [16, 48, 128]
OUT_DIR = os.path.join(os.path.dirname(__file__), "icons")

# Brand colours
BG       = (8,  14,  26)   # #080e1a  dark navy
FILL     = (79,  70, 229)  # #4f46e5  indigo
FILL2    = (99, 102, 241)  # #6366f1  lighter indigo
ACCENT   = (165, 180, 252) # #a5b4fc  lavender


def hexagon_points(cx, cy, r, angle_offset=0):
    """Return 6 (x, y) tuples for a regular hexagon."""
    pts = []
    for i in range(6):
        angle = math.radians(60 * i + angle_offset)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def make_icon(size: int) -> Image.Image:
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = size / 2
    margin = size * 0.06

    # Outer circle background
    draw.ellipse([margin, margin, size - margin, size - margin], fill=BG)

    # Outer hexagon (border ring)
    r_outer = cx - margin - size * 0.04
    outer_pts = hexagon_points(cx, cy, r_outer, angle_offset=-90)
    draw.polygon(outer_pts, fill=FILL)

    # Inner hexagon (body)
    r_inner = r_outer * 0.72
    inner_pts = hexagon_points(cx, cy, r_inner, angle_offset=-90)
    draw.polygon(inner_pts, fill=FILL2)

    # Centre dot / accent
    r_dot = r_inner * 0.28
    draw.ellipse(
        [cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot],
        fill=ACCENT,
    )

    # For larger icons, add a small "A" mark above the dot
    if size >= 48:
        # Two small diagonal bars forming an upward chevron
        bar_h = r_inner * 0.22
        bar_w = r_inner * 0.08
        # Left arm
        x0 = cx - bar_h * 0.7
        y0 = cy - bar_h * 0.45
        draw.line([(x0, y0), (cx, cy - bar_h * 0.9)], fill=BG, width=max(1, int(bar_w)))
        # Right arm
        x1 = cx + bar_h * 0.7
        draw.line([(cx, cy - bar_h * 0.9), (x1, y0)], fill=BG, width=max(1, int(bar_w)))

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for size in SIZES:
        icon = make_icon(size)
        path = os.path.join(OUT_DIR, f"icon{size}.png")
        icon.save(path, "PNG")
        print(f"  Created {path}  ({size}x{size})")
    print("Done.")


if __name__ == "__main__":
    main()
