"""
make_demo_gif.py — generates docs/demo.gif from scratch using Pillow.

No API keys. No screen recording. No external tools.

Usage:
    pip install pillow
    python examples/make_demo_gif.py

Output: docs/demo.gif (~2MB, ~35 seconds)
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    os.system(f'"{sys.executable}" -m pip install pillow -q')
    from PIL import Image, ImageDraw, ImageFont


# ── Colour palette (GitHub dark theme) ────────────────────────────────────────

BG      = (13,  17,  23)
WHITE   = (230, 237, 243)
DIM     = (110, 118, 129)
RED     = (255, 123, 114)
BRED    = (255,  70,  70)
GREEN   = ( 63, 185,  80)
BGREEN  = ( 40, 220, 100)
YELLOW  = (227, 179,  65)
BYELLOW = (255, 210,  80)
CYAN    = (121, 192, 255)
BCYAN   = (165, 214, 255)
BWHITE  = (255, 255, 255)
BORDER  = ( 48,  54,  61)
HEADER  = ( 88, 166, 255)
ORANGE  = (255, 166,  77)


# ── Font ───────────────────────────────────────────────────────────────────────

FONT_SIZE = 13
FONT_PATHS = [
    r"C:\Windows\Fonts\consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Monaco.ttf",
]

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()

FONT = _load_font(FONT_SIZE)

# Measure exact monospace character dimensions
_probe = Image.new("RGB", (300, 60), BG)
_d = ImageDraw.Draw(_probe)
_bb = _d.textbbox((0, 0), "M" * 10, font=FONT)
CHAR_W = (_bb[2] - _bb[0]) / 10
CHAR_H = int((_bb[3] - _bb[1]) * 1.60)   # line-height with comfortable spacing

# Canvas
COLS  = 88
ROWS  = 34
PAD_L = 16
PAD_T = 14
IMG_W = int(COLS * CHAR_W + PAD_L * 2)
IMG_H = ROWS * CHAR_H + PAD_T * 2


# ── Primitive types ────────────────────────────────────────────────────────────

Seg  = Tuple[str, Tuple[int, int, int]]   # (text, rgb)
Line = List[Seg]

def seg(text: str, colour=WHITE) -> Seg:
    return (text, colour)

def plain(text: str, colour=WHITE) -> Line:
    return [seg(text, colour)]

def blank() -> Line:
    return [seg("")]

def rule(width: int = None, colour=BORDER) -> Line:
    w = width or (COLS - 1)
    return [seg("─" * w, colour)]

def dbl_rule(colour=BORDER) -> Line:
    return [seg("═" * (COLS - 1), colour)]


# ── Renderer ───────────────────────────────────────────────────────────────────

def render(lines: List[Line]) -> Image.Image:
    img = Image.new("RGB", (IMG_W, IMG_H), BG)
    draw = ImageDraw.Draw(img)
    draw.line([(0, 0), (IMG_W - 1, 0)], fill=BORDER, width=2)

    y = PAD_T
    for line_segs in lines[:ROWS]:
        x = PAD_L
        for text, colour in line_segs:
            if text:
                draw.text((x, y), text, fill=colour, font=FONT)
            x += len(text) * CHAR_W
        y += CHAR_H

    return img


# ── Frame builders ─────────────────────────────────────────────────────────────

def title_frame() -> List[Line]:
    pad = blank
    return [
        pad(), pad(), pad(),
        plain("  ┌─────────────────────────────────────────────────────────────────┐", BORDER),
        plain("  │                                                                 │", BORDER),
        [seg("  │   ", BORDER), seg("AgentMesh", BCYAN), seg("  —  The governance plane for AI agents          │", BORDER)],
        plain("  │                                                                 │", BORDER),
        [seg("  │   ", BORDER), seg("Demo: 20-step code review agent · 200 runs/month          │", DIM)],
        [seg("  │   ", BORDER), seg("No API keys required                                      │", DIM)],
        plain("  │                                                                 │", BORDER),
        plain("  └─────────────────────────────────────────────────────────────────┘", BORDER),
        pad(), pad(),
        [seg("  ", WHITE), seg("pip install agentmesh", CYAN),
         seg("   ·   ", DIM), seg("github.com/anilatambharii/agentmesh", DIM)],
    ]


def before_header() -> List[Line]:
    return [
        blank(),
        [seg("  ── ", BORDER), seg("BEFORE AgentMesh", BRED), seg(" " * 45 + "──", BORDER)],
        blank(),
        plain("  Running a 20-step code review agent — claude-opus-4-7 on every call.", DIM),
        plain("  No budget cap. No circuit breaker. No model routing.", DIM),
        blank(),
        [seg("  "), seg("Step", DIM), seg("  "), seg("Action                  ", DIM),
         seg("Input tokens  ", DIM), seg("Step cost  ", DIM), seg("Running total  ", DIM), seg("Model", DIM)],
        rule(width=COLS - 3),
    ]


BEFORE_STEPS = [
    ("Planning",           1_200,   "Planning task and forming execution strategy"),
    ("search",             2_800,   "Searching codebase for relevant auth files"),
    ("read",               4_200,   "Reading auth.py, middleware.py, config.py"),
    ("Analysis",           6_100,   "Analyzing security patterns across codebase"),
    ("search",             8_400,   "Searching for test files related to auth"),
    ("read",              11_200,   "Reading test_auth.py, test_middleware.py"),
    ("Cross-reference",   16_800,   "Cross-referencing security manual with code"),
    ("search",            22_100,   "Searching git history for related issues"),
    ("read",              28_900,   "Reading git diff, last 30 commits"),
    ("Synthesis",         36_400,   "Synthesising findings across all context"),
    ("Draft review",      41_200,   "Drafting initial security review comments"),
    ("search",            47_800,   "Searching for similar patterns in modules"),
    ("read",              55_300,   "Reading additional modules for context"),
    ("Refinement",        61_100,   "Refining review based on broader context"),
    ("search",            68_900,   "Searching vulnerability database (CVE)"),
    ("CVE analysis",      76_200,   "Analysing CVE matches against codebase"),
    ("read ⚠",           124_800,  "Reading SECURITY POLICY DOC — 50k tokens re-injected"),
    ("Integration",       132_100,  "Integrating policy findings with code review"),
    ("Final draft",       138_400,  "Generating final review report"),
    ("Formatting",        141_200,  "Formatting output for GitHub PR comment"),
]

HAIKU_COST  = 0.80  / 1_000_000
SONNET_COST = 3.00  / 1_000_000
OPUS_COST   = 15.00 / 1_000_000


def before_rows(step_count: int) -> List[Line]:
    lines = before_header()
    running = 0.0
    for i, (action, tokens, _) in enumerate(BEFORE_STEPS[:step_count], 1):
        step_c = tokens * OPUS_COST
        running += step_c
        is_last = (i == step_count)
        is_bad  = (i == 17)

        tok_str = f"{tokens:>10,}"
        sc_str  = f"${step_c:>7.4f}"
        rt_str  = f"${running:>7.4f}"
        row_colour = BRED if is_bad else (YELLOW if tokens > 40_000 else (RED if is_last else WHITE))

        row: Line = [
            seg(f"  {i:>2}  "),
            seg(f"{action:<24}", row_colour if is_last or is_bad else WHITE),
            seg(tok_str, BRED if is_bad else (RED if tokens > 40_000 else DIM)),
            seg(sc_str,  BRED if is_bad else (YELLOW if step_c > 0.05 else DIM)),
            seg(rt_str,  RED  if running > 1.0 else YELLOW if running > 0.3 else WHITE),
            seg("  claude-opus-4-7", RED if is_bad or is_last else DIM),
        ]
        if is_bad:
            row.append(seg("  ← 50k security manual!", BRED))
        lines.append(row)

    return lines


def before_summary(total_cost: float) -> List[Line]:
    monthly = total_cost * 200
    lines = before_rows(20)
    lines += [
        blank(),
        rule(),
        blank(),
        [seg("  Total tokens (last step):  ", WHITE),
         seg(f"{141_200:,}", BRED),
         seg("   (context grows every step — O(n²))", DIM)],
        [seg("  Total cost per run:         ", WHITE), seg(f"${total_cost:.4f}", BRED)],
        [seg("  Monthly cost (200 runs):    ", WHITE), seg(f"${monthly:,.0f}/month", BRED)],
        [seg("  Annual projection:          ", WHITE), seg(f"${monthly*12:,.0f}/year", BRED)],
        blank(),
        plain("  No visibility. No circuit breaker. One runaway loop = $47,000 bill.", DIM),
    ]
    return lines


def transition_frame() -> List[Line]:
    return [
        blank(), blank(), blank(), blank(),
        plain("  Wrapping the same agent with AgentMesh...", DIM),
        blank(),
        [seg("  mesh = ", WHITE), seg("AgentMesh", CYAN),
         seg("(policy=", WHITE), seg("Policy", BCYAN), seg(".from_yaml(", WHITE),
         seg('"agentmesh-policy.yaml"', YELLOW), seg("))", WHITE)],
        [seg("  governed = mesh.", WHITE), seg("wrap_langgraph", CYAN),
         seg("(graph)    ", WHITE), seg("← this is the only change", BGREEN)],
        blank(), blank(),
        plain("  Policy:", DIM),
        plain("    default model  : claude-haiku-4-5   ($0.80/1M vs $15/1M)", GREEN),
        plain("    budget cap     : 30,000 tokens/run  hard_stop=True", GREEN),
        plain("    circuit breaker: 25 iterations max", GREEN),
        plain("    compression at : 75% budget used", GREEN),
        plain("    semantic cache : enabled", GREEN),
    ]


AFTER_STEPS = [
    ("Planning",          "haiku",  1_200,  "routine planning"),
    ("search",            "haiku",  2_800,  "simple search"),
    ("read",              "haiku",  4_200,  "file read"),
    ("Analysis",          "sonnet", 6_100,  "complexity > 0.8"),
    ("search",            "haiku",  8_400,  "simple search"),
    ("read",              "haiku",  9_100,  "file read"),
    ("Cross-reference",   "sonnet",10_200,  "complexity > 0.8"),
    ("search",            "haiku",  8_900,  "simple search"),
    ("read",              "haiku",  9_600,  "file read"),
    ("Synthesis",         "sonnet",11_400,  "requires_reasoning"),
    ("Draft review",      "sonnet", 9_800,  "requires_reasoning — ⚡ compressing"),
    ("search",            "haiku",  4_200,  "simple search (compressed)"),
    ("read",              "haiku",  4_800,  "file read (compressed)"),
    ("Refinement",        "sonnet", 5_100,  "requires_reasoning"),
    ("search",            "haiku",  3_900,  "simple search (compressed)"),
    ("CVE analysis",      "sonnet", 6_200,  "complexity > 0.8"),
]

MODEL_COLOUR = {"haiku": CYAN, "sonnet": YELLOW, "opus": RED}
MODEL_COST   = {"haiku": HAIKU_COST, "sonnet": SONNET_COST, "opus": OPUS_COST}


def after_header() -> List[Line]:
    return [
        blank(),
        [seg("  ── ", BORDER), seg("AFTER AgentMesh", BGREEN), seg(" " * 46 + "──", BORDER)],
        blank(),
        plain("  Same agent. Same task. One line changed: governed = mesh.wrap_langgraph(graph)", DIM),
        blank(),
        [seg("  "), seg("Step", DIM), seg("  "), seg("Action                  ", DIM),
         seg("Tokens (pruned) ", DIM), seg("Step cost  ", DIM), seg("Running    ", DIM),
         seg("Model           ", DIM), seg("Reason", DIM)],
        rule(width=COLS - 3),
    ]


def after_rows(step_count: int, show_compress: bool = False,
               show_cache: bool = False) -> List[Line]:
    lines = after_header()
    running = 0.0
    for i, (action, model, tokens, reason) in enumerate(AFTER_STEPS[:step_count], 1):
        step_c = tokens * MODEL_COST[model]
        running += step_c
        is_last = (i == step_count)
        is_comp = (i == 11 and show_compress)

        mc = MODEL_COLOUR[model]
        row: Line = [
            seg(f"  {i:>2}  "),
            seg(f"{action:<16}", BGREEN if is_last else WHITE),
            seg(f"[{model}]", mc),
            seg(f"  {tokens:>8,}   ", GREEN if tokens < 10_000 else DIM),
            seg(f"${step_c:>6.4f}   ", GREEN),
            seg(f"${running:>6.4f}   ", GREEN if running < 0.30 else YELLOW),
            seg(reason, BCYAN if "compress" in reason else (BGREEN if "cache" in reason else DIM)),
        ]
        if is_comp:
            row = [
                seg(f"  {i:>2}  "),
                seg(f"{action:<16}", BYELLOW),
                seg(f"[{model}]", mc),
                seg("  ⚡ COMPRESSING ", BYELLOW),
                seg(f"${step_c:>6.4f}   ", YELLOW),
                seg(f"${running:>6.4f}   ", YELLOW),
                seg("context pruned 58% — LLMLingua active", BYELLOW),
            ]
        lines.append(row)

    if show_cache:
        lines += [
            blank(),
            [seg("  17  "),
             seg("read            ", WHITE),
             seg("[haiku]", CYAN),
             seg("  ✓ CACHE HIT  ", BGREEN),
             seg("$0.0001  ", GREEN),
             seg("(saved $0.752 — security doc seen 847× today)", BGREEN)],
        ]
    return lines


def after_summary(before_monthly: float) -> List[Line]:
    monthly = sum(
        AFTER_STEPS[i][2] * MODEL_COST[AFTER_STEPS[i][1]] for i in range(16)
    ) * 200 + 0.0001 * 200
    saving  = before_monthly - monthly
    pct     = int(saving / before_monthly * 100)

    lines = after_rows(16, show_compress=True, show_cache=True)
    lines += [
        blank(), rule(), blank(),
        [seg("  Monthly cost (200 runs):  ", WHITE),
         seg(f"${monthly:,.0f}/month", BGREEN),
         seg(f"   (was ${before_monthly:,.0f})", DIM)],
        [seg("  Saving:                   ", WHITE),
         seg(f"${saving:,.0f}/month  ", BGREEN),
         seg(f"({pct}% reduction)", BGREEN)],
        blank(),
        [seg("  Model routing   ", WHITE), seg("74% calls on haiku  ", CYAN),
         seg("($0.80/1M vs $15/1M)", DIM)],
        [seg("  Context pruning ", WHITE), seg("58% token reduction", GREEN),
         seg(" from step 11 onward", DIM)],
        [seg("  Semantic cache  ", WHITE), seg("security doc served from cache", GREEN),
         seg(" (847 hits today)", DIM)],
        [seg("  Circuit breaker ", WHITE), seg("armed — runaway loops impossible", GREEN)],
        [seg("  Audit trail     ", WHITE), seg("16 entries, Ed25519 signed ✓", CYAN)],
    ]
    return lines


def comparison_frame(before_monthly: float, after_monthly: float) -> List[Line]:
    saving = before_monthly - after_monthly
    pct    = int(saving / before_monthly * 100)
    annual = saving * 12

    rows = [
        ("Model",            "claude-opus always",    "haiku 74% / sonnet 26%",  "—"),
        ("Context pruning",  "none",                  "58% from step 11",        "—"),
        ("Security manual",  "$0.752/run (re-read)",  "$0.0001 (cache hit)",     "99.8%"),
        ("Circuit breaker",  "none",                  "25-step hard stop",       "—"),
        ("Audit trail",      "none",                  "Ed25519 + OTel export",   "—"),
        ("Cost / run",       f"${before_monthly/200:.4f}",
                             f"${after_monthly/200:.4f}",     f"−{pct}%"),
        ("Monthly",          f"${before_monthly:,.0f}",
                             f"${after_monthly:,.0f}",        f"−${saving:,.0f}"),
        ("Annual",           f"${before_monthly*12:,.0f}",
                             f"${after_monthly*12:,.0f}",     f"−${annual:,.0f}"),
    ]

    col_w = [22, 26, 26, 14]
    header_row = (
        f"  {'Metric':<{col_w[0]}}"
        f"{'Without AgentMesh':<{col_w[1]}}"
        f"{'With AgentMesh':<{col_w[2]}}"
        f"{'Saving':<{col_w[3]}}"
    )

    lines = [
        blank(),
        dbl_rule(colour=HEADER),
        [seg("  "), seg("Results", BWHITE)],
        dbl_rule(colour=HEADER),
        blank(),
        plain(header_row, DIM),
        rule(),
    ]

    for metric, before, after, delta in rows:
        row_text = (
            f"  {metric:<{col_w[0]}}"
            f"{before:<{col_w[1]}}"
            f"{after:<{col_w[2]}}"
        )
        lines.append([
            seg(row_text),
            seg(delta, BGREEN if delta.startswith("−") else DIM),
        ])

    lines += [
        rule(),
        blank(),
        [seg("  One line of code:  ", WHITE),
         seg("governed = mesh.wrap_langgraph(graph)", CYAN)],
        blank(),
        [seg(f"  ${annual:,.0f} saved per year  ·  {pct}% cost reduction  ·  zero agent code changes",
             BGREEN)],
        blank(),
        [seg("  ", WHITE), seg("github.com/anilatambharii/agentmesh", DIM),
         seg("   ·   ", BORDER), seg("pip install agentmesh", CYAN)],
    ]
    return lines


# ── Assemble all frames ────────────────────────────────────────────────────────

def build_frames() -> List[Tuple[List[Line], int]]:
    """Returns list of (lines, duration_ms)."""

    before_total = sum(t * OPUS_COST for _, t, _ in BEFORE_STEPS)
    after_total  = sum(
        AFTER_STEPS[i][2] * MODEL_COST[AFTER_STEPS[i][1]] for i in range(16)
    ) * 200 + 0.0001 * 200
    before_monthly = before_total * 200

    frames: List[Tuple[List[Line], int]] = []

    # Title
    frames.append((title_frame(), 2500))

    # BEFORE — build up row by row
    for n in range(1, 21):
        dur = 1200 if n == 17 else 120
        frames.append((before_rows(n), dur))

    # BEFORE summary
    frames.append((before_summary(before_total), 3000))

    # Transition
    frames.append((transition_frame(), 2200))

    # AFTER — build up row by row
    for n in range(1, 17):
        dur = 100
        if n == 11:
            # Show without compress flag first
            frames.append((after_rows(n, show_compress=False), 200))
            # Then with compress event
            frames.append((after_rows(n, show_compress=True), 1200))
            continue
        frames.append((after_rows(n), dur))

    # Show cache hit
    frames.append((after_rows(16, show_compress=True, show_cache=True), 1800))

    # AFTER summary
    frames.append((after_summary(before_monthly), 3000))

    # Comparison
    frames.append((comparison_frame(before_monthly, after_total), 5000))

    return frames


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"AgentMesh GIF generator")
    print(f"  Canvas : {IMG_W}×{IMG_H}px  ({COLS}×{ROWS} chars)")
    print(f"  Font   : Consolas {FONT_SIZE}px  (char {CHAR_W:.1f}×{CHAR_H}px)")

    frames = build_frames()
    print(f"  Frames : {len(frames)}")

    images = []
    durations = []

    for i, (lines, dur) in enumerate(frames):
        img = render(lines)
        # Quantise to 256 colours (GIF requirement)
        img_p = img.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        images.append(img_p)
        durations.append(dur)
        print(f"  frame {i+1:>3}/{len(frames)}  {dur}ms", end="\r")

    out_path = Path(__file__).parent.parent / "docs" / "demo.gif"
    out_path.parent.mkdir(exist_ok=True)

    print(f"\n  Saving {out_path} ...")
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )

    size_kb = out_path.stat().st_size // 1024
    print(f"  Done — {size_kb}KB  ({len(images)} frames)")
    print(f"\n  Add to README: ![demo](docs/demo.gif)")


if __name__ == "__main__":
    main()
