"""
Generate Chrome Web Store assets for AgentMesh:
  - store_icon_128.png      (128x128  — store listing icon)
  - promo_small_440x280.png (440x280  — required promo image)
  - screenshot_1280x800.png (1280x800 — store screenshot)

Run:
    pip install Pillow
    python generate_store_assets.py
"""

import math
import os
from PIL import Image, ImageDraw, ImageFont

OUT = "store_assets"
os.makedirs(OUT, exist_ok=True)

# ── Brand colours ─────────────────────────────────────────────────────────────
BG      = (8,   14,  26)    # #080e1a  dark navy
INDIGO  = (79,  70, 229)    # #4f46e5
INDIGO2 = (99, 102, 241)    # #6366f1
LAVNDR  = (165, 180, 252)   # #a5b4fc
WHITE   = (255, 255, 255)
GREEN   = (34,  197, 94)    # #22c55e
CARD    = (15,  23,  42)    # slightly lighter than BG


def hexagon_points(cx, cy, r, angle_offset=-90):
    return [
        (cx + r * math.cos(math.radians(60 * i + angle_offset)),
         cy + r * math.sin(math.radians(60 * i + angle_offset)))
        for i in range(6)
    ]


def draw_hex_logo(draw, cx, cy, size):
    """Draw the AgentMesh hexagon logo centred at (cx, cy) with given radius."""
    r_out = size
    draw.polygon(hexagon_points(cx, cy, r_out), fill=INDIGO)
    r_in  = r_out * 0.72
    draw.polygon(hexagon_points(cx, cy, r_in), fill=INDIGO2)
    r_dot = r_in * 0.28
    draw.ellipse([cx - r_dot, cy - r_dot, cx + r_dot, cy + r_dot], fill=LAVNDR)
    # Chevron / A mark
    bh = r_in * 0.22
    bw = max(2, int(r_in * 0.08))
    draw.line([(cx - bh * 0.7, cy - bh * 0.45), (cx, cy - bh * 0.9)], fill=BG, width=bw)
    draw.line([(cx, cy - bh * 0.9), (cx + bh * 0.7, cy - bh * 0.45)], fill=BG, width=bw)


def safe_font(size):
    for name in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
                 "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def text_centre(draw, x, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((x - w // 2, y), text, font=font, fill=fill)


# ── 1. Store icon 128x128 ─────────────────────────────────────────────────────
def make_store_icon():
    img  = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Dark circle background
    draw.ellipse([4, 4, 124, 124], fill=BG)
    draw_hex_logo(draw, 64, 64, 46)
    path = os.path.join(OUT, "store_icon_128.png")
    img.save(path)
    print(f"  {path}")


# ── 2. Small promo 440x280 ────────────────────────────────────────────────────
def make_promo_small():
    W, H = 440, 280
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Subtle grid lines
    for x in range(0, W, 44):
        draw.line([(x, 0), (x, H)], fill=(20, 30, 50), width=1)
    for y in range(0, H, 40):
        draw.line([(0, y), (W, y)], fill=(20, 30, 50), width=1)

    # Glow behind logo
    for r in range(70, 10, -10):
        alpha = int(30 * (1 - r / 70))
        draw.ellipse([W//2 - r - 30, H//2 - r - 20,
                      W//2 + r - 30, H//2 + r - 20],
                     fill=(79, 70, 229, alpha) if hasattr(draw, '_image') else INDIGO)

    # Hexagon logo (left-centre)
    draw_hex_logo(draw, 120, H // 2, 60)

    # Text (right side)
    f_big   = safe_font(32)
    f_med   = safe_font(18)
    f_small = safe_font(14)

    draw.text((210, 60),  "AgentMesh",  font=f_big,   fill=WHITE)
    draw.text((210, 100), "AI Governance Proxy", font=f_med, fill=LAVNDR)

    # Stats
    stats = [
        ("85%", "cache hit rate"),
        ("75%", "cost reduction"),
        ("0",   "code changes needed"),
    ]
    y = 140
    for val, label in stats:
        draw.text((210, y),      val,   font=safe_font(22), fill=GREEN)
        draw.text((260, y + 4),  label, font=f_small,       fill=(180, 190, 210))
        y += 36

    path = os.path.join(OUT, "promo_small_440x280.png")
    img.save(path)
    print(f"  {path}")


# ── 3. Screenshot 1280x800 ────────────────────────────────────────────────────
def make_screenshot():
    W, H = 1280, 800
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Background grid
    for x in range(0, W, 80):
        draw.line([(x, 0), (x, H)], fill=(14, 22, 38), width=1)
    for y in range(0, H, 80):
        draw.line([(0, y), (W, y)], fill=(14, 22, 38), width=1)

    # ── Left panel: proxy flow diagram ───────────────────────────────────────
    f_title  = safe_font(28)
    f_label  = safe_font(16)
    f_small  = safe_font(13)
    f_mono   = safe_font(13)

    draw.text((60, 50), "AgentMesh", font=safe_font(36), fill=WHITE)
    draw.text((60, 95), "AI Governance Proxy", font=safe_font(18), fill=LAVNDR)

    # Flow boxes
    boxes = [
        (60, 170,  "ChatGPT / Claude.ai / Gemini",   CARD,   LAVNDR),
        (60, 250,  "Chrome Extension intercepts",      CARD,   INDIGO2),
        (60, 330,  "Exact cache  (SHA-256)",           CARD,   GREEN),
        (60, 410,  "Semantic cache  (cosine 0.70)",    CARD,   GREEN),
        (60, 490,  "Quota check  (pre-call block)",    CARD,   (234, 179, 8)),
        (60, 570,  "LLM call  (if cache miss)",        CARD,   INDIGO),
    ]
    for bx, by, label, bg, fg in boxes:
        draw.rounded_rectangle([bx, by, bx + 460, by + 54], radius=8, fill=bg)
        draw.text((bx + 16, by + 16), label, font=f_label, fill=fg)
        if by < 570:
            draw.line([(bx + 230, by + 54), (bx + 230, by + 74)], fill=(60, 70, 100), width=2)
            draw.polygon([(bx+224, by+70), (bx+236, by+70), (bx+230, by+78)], fill=(60,70,100))

    # ── Right panel: popup mock ───────────────────────────────────────────────
    px, py, pw, ph = 740, 120, 380, 520

    # Popup card
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=16, fill=CARD)
    draw.rounded_rectangle([px, py, px + pw, py + ph], radius=16,
                           outline=INDIGO, width=2)

    # Header
    draw.rounded_rectangle([px, py, px + pw, py + 56], radius=16, fill=INDIGO)
    draw_hex_logo(draw, px + 36, py + 28, 16)
    draw.text((px + 60, py + 12), "AgentMesh", font=safe_font(20), fill=WHITE)

    # Status badge
    draw.rounded_rectangle([px + pw - 110, py + 14, px + pw - 14, py + 42],
                           radius=12, fill=(22, 163, 74))
    draw.text((px + pw - 100, py + 18), "● Connected", font=f_small, fill=WHITE)

    # Stats grid
    stat_items = [
        ("3",       "Prompts Intercepted"),
        ("2",       "Cache Hits"),
        ("847",     "Tokens Saved"),
        ("$0.002",  "Cost Saved"),
    ]
    sx, sy = px + 20, py + 80
    for i, (val, label) in enumerate(stat_items):
        col = i % 2
        row = i // 2
        cx2 = sx + col * 175
        cy2 = sy + row * 100
        draw.rounded_rectangle([cx2, cy2, cx2 + 160, cy2 + 80], radius=10, fill=BG)
        draw.text((cx2 + 16, cy2 + 10), val,   font=safe_font(30), fill=GREEN)
        draw.text((cx2 + 16, cy2 + 48), label, font=f_small,       fill=(140, 155, 180))

    # Last prompt result
    draw.text((px + 20, py + 300), "Last prompt:", font=f_small, fill=(100, 115, 140))
    draw.rounded_rectangle([px + 20, py + 322, px + pw - 20, py + 390],
                           radius=8, fill=BG)
    draw.text((px + 36, py + 334), "Cache HIT — semantic match",
              font=f_label, fill=GREEN)
    draw.text((px + 36, py + 358), "Similarity: 0.82  ·  Tokens saved: 423",
              font=f_small, fill=(140, 155, 180))

    # Quota bar
    draw.text((px + 20, py + 410), "Team quota  (engineering)", font=f_small, fill=(100,115,140))
    draw.rounded_rectangle([px + 20, py + 432, px + pw - 20, py + 452], radius=6, fill=BG)
    used_w = int((pw - 40) * 0.23)
    draw.rounded_rectangle([px + 20, py + 432, px + 20 + used_w, py + 452],
                           radius=6, fill=INDIGO)
    draw.text((px + 20, py + 460), "23% used  ·  770K tokens remaining",
              font=f_small, fill=(140, 155, 180))

    # Port setting
    draw.text((px + 20, py + 494), "Proxy port:", font=f_small, fill=(100,115,140))
    draw.rounded_rectangle([px + 110, py + 488, px + 200, py + 510], radius=6, fill=BG)
    draw.text((px + 120, py + 492), "8080", font=f_small, fill=LAVNDR)

    # Bottom label
    draw.text((60, 720),
              "85% cache hit rate  ·  75% cost reduction  ·  Zero code changes",
              font=safe_font(20), fill=LAVNDR)
    draw.text((60, 752),
              "github.com/anilatambharii/agentmesh  ·  pip install agentmesh-proxy",
              font=f_small, fill=(80, 95, 120))

    path = os.path.join(OUT, "screenshot_1280x800.png")
    img.save(path)
    print(f"  {path}")


if __name__ == "__main__":
    print("Generating Chrome Web Store assets...")
    make_store_icon()
    make_promo_small()
    make_screenshot()
    print(f"\nAll assets saved to ./{OUT}/")
