#!/usr/bin/env python3
"""
Generate a synthetic, photorealistic, 100% anonymized demo GIF for tmux-agent-switcher.
Zero personal info, zero recording tools needed.
Uses Pillow to render crisp antialiased frames and ffmpeg for high-quality palette-optimized GIF.
"""

import os
import shutil
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(REPO_DIR, "assets")
OUTPUT_GIF = os.path.join(ASSETS_DIR, "demo.gif")

WIDTH, HEIGHT = 1080, 640
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"

# Color Palette
BG_COLOR = "#0b0e14"
WIN_BORDER = "#1f2430"
TITLEBAR_BG = "#151b24"
TITLEBAR_TEXT = "#707a8c"

POPUP_BG = "#111620"
POPUP_BORDER = "#2e3b52"
POPUP_HEADER = "#8a99b5"
CURSOR_BG = "#1e293b"

# Agent Colors
COL_CLAUDE = "#d97757"
COL_AGY = "#4e88f5"
COL_CODEX = "#10a37f"
COL_CURSOR = "#e2e8f0"
COL_MONITOR = "#94a3b8"
COL_SHELL = "#64748b"

COL_INPUT = "#ef4444"
COL_RUN = "#22c55e"
COL_IDLE = "#64748b"
COL_WHITE = "#f1f5f9"
COL_CYAN = "#7dcfff"
COL_MINT = "#9ece6a"
COL_PEACH = "#ffc387"
COL_PURPLE = "#a855f7"
COL_AMBER = "#f59e0b"
COL_BORDER = "#475569"

ROWS = [
    {"pane": "auth:1.2", "mark": " ", "h": "🟠 Claude", "h_c": COL_CLAUDE, "s": "🔔 input", "s_c": COL_INPUT, "age": " 2m", "title": "[Auth API] Key rotation handler"},
    {"pane": "db:2.1",   "mark": "▶", "h": "🤖 AGY",    "h_c": COL_AGY,    "s": "⚡ run",   "s_c": COL_RUN,   "age": " 8s", "title": "[DB Migration] Postgres 16 partition"},
    {"pane": "core:1.1", "mark": " ", "h": "⚡ Codex",  "h_c": COL_CODEX,  "s": "⚡ run",   "s_c": COL_RUN,   "age": "45s", "title": "[Scheduler] Async worker pool"},
    {"pane": "web:3.1",  "mark": " ", "h": "⚡ Cursor", "h_c": COL_CURSOR, "s": "💤 idle",  "s_c": COL_IDLE,  "age": "15m", "title": "[Dashboard] Realtime WebSocket UI"},
    {"pane": "ops:1.4",  "mark": " ", "h": "📊 Token-mo","h_c": COL_MONITOR,"s": "💤 idle",  "s_c": COL_IDLE,  "age": "LIVE","title": "System Monitor (token-monitor)"},
    {"pane": "ops:1.5",  "mark": " ", "h": "📊 Btop",   "h_c": COL_MONITOR,"s": "💤 idle",  "s_c": COL_IDLE,  "age": "LIVE","title": "System Monitor (btop)"},
    {"pane": "shell:1",  "mark": " ", "h": "💻 Shell",  "h_c": COL_SHELL,  "s": "💤 idle",  "s_c": COL_IDLE,  "age": " - ", "title": "~/src/autonomous-platform"},
]

PREVIEWS = {
    0: {  # Claude
        "header": "auth:1.2 · 🟠 Claude · [Auth API] Key rotation handler",
        "lines": [
            ("badge", "─── ✦ Turn 3 of 4 ──────────────────────────"),
            ("user_pill", "👤 YOU"),
            ("user_text", "Can we support asymmetric RS256 key rotation"),
            ("user_text", "with automatic JWKS caching?"),
            ("gap", ""),
            ("heading", "✦ Solution Overview"),
            ("text", "I've implemented the rotation provider with 5m TTL:"),
            ("fence", "─── python ─────────────────────────────────"),
            ("code", "class KeyRotationManager:"),
            ("code", "    def __init__(self, jwks_url: str):"),
            ("code", "        self.client = jwt.PyJWKClient(jwks_url)"),
            ("code", "        self.cache_ttl = 300"),
            ("fence", "───────────────────────────────────────────"),
            ("gap", ""),
            ("table_top", "┌──────────┬──────────┬────────┐"),
            ("table_row", "│ Key Type │ Rotation │ Status │"),
            ("table_mid", "├──────────┼──────────┼────────┤"),
            ("table_row", "│ RS256    │ 300s TTL │ Ready  │"),
            ("table_bot", "└──────────┴──────────┴────────┘"),
            ("gap", ""),
            ("footer", "── last 4 turns · ctrl-o: full history ──"),
        ]
    },
    1: {  # AGY
        "header": "db:2.1 · 🤖 AGY · [DB Migration] Postgres 16 partition",
        "lines": [
            ("badge", "─── ✦ Turn 2 of 2 ──────────────────────────"),
            ("active_pill", "⚡ YOU (Active)"),
            ("active_text", "Run the migration in dry-run mode and verify"),
            ("active_text", "foreign key partition pruning performance."),
            ("gap", ""),
            ("running", "⚡ Agent is actively running..."),
            ("gap", ""),
            ("heading", "✦ Execution Log"),
            ("text", "Analyzing table partition boundaries:"),
            ("fence", "─── sql ────────────────────────────────────"),
            ("code", "EXPLAIN (ANALYZE, COSTS OFF)"),
            ("code", "SELECT * FROM events_2026"),
            ("code", "WHERE created_at >= '2026-10-01';"),
            ("fence", "───────────────────────────────────────────"),
            ("gap", ""),
            ("footer", "── last 2 turns · ctrl-o: full history ──"),
        ]
    },
    2: {  # Codex
        "header": "core:1.1 · ⚡ Codex · [Scheduler] Async worker pool",
        "lines": [
            ("badge", "─── ✦ Turn 4 of 4 ──────────────────────────"),
            ("user_pill", "👤 YOU"),
            ("user_text", "Benchmark concurrency under 64 parallel workers."),
            ("gap", ""),
            ("heading", "■ Benchmark Results"),
            ("text", "All 64 threads completed with zero lock contention:"),
            ("fence", "─── bash ───────────────────────────────────"),
            ("code", "$ cargo bench --bench scheduler"),
            ("code", "worker_pool_dispatch: [12.4 us ... 12.8 us]"),
            ("code", "throughput: 1,420,000 tasks/sec (±0.4%)"),
            ("fence", "───────────────────────────────────────────"),
            ("gap", ""),
            ("table_top", "┌──────────────┬──────────┬─────────┐"),
            ("table_row", "│ Concurrency  │ Latency  │ Speedup │"),
            ("table_mid", "├──────────────┼──────────┼─────────┤"),
            ("table_row", "│ 64 workers   │ 12.6 µs  │ 14.8x   │"),
            ("table_bot", "└──────────────┴──────────┴─────────┘"),
            ("gap", ""),
            ("footer", "── last 4 turns · ctrl-o: full history ──"),
        ]
    },
    3: {  # Cursor
        "header": "web:3.1 · ⚡ Cursor · [Dashboard] Realtime WebSocket UI",
        "lines": [
            ("badge", "─── ✦ Turn 1 of 1 ──────────────────────────"),
            ("user_pill", "👤 YOU"),
            ("user_text", "Connect the reactive telemetry state machine."),
            ("gap", ""),
            ("heading", "✦ WebSocket Bridge Connected"),
            ("text", "Streaming live token consumption at 60 FPS:"),
            ("code", "const socket = new WebSocket('wss://stream');"),
            ("code", "socket.onmessage = (e) => updateLedger(e.data);"),
            ("gap", ""),
            ("footer", "── last 1 turns · ctrl-o: full history ──"),
        ]
    }
}


def render_frame(selected_idx: int) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_main = ImageFont.truetype(FONT_PATH, 13)
    font_bold = ImageFont.truetype(FONT_PATH, 13)
    font_sm = ImageFont.truetype(FONT_PATH, 11)

    # 1. Window Frame & Titlebar
    draw.rounded_rectangle([10, 10, WIDTH - 10, HEIGHT - 10], radius=10, fill=TITLEBAR_BG, outline=WIN_BORDER, width=1)
    
    # Traffic Lights
    draw.ellipse([26, 24, 38, 36], fill="#ff5f56")
    draw.ellipse([46, 24, 58, 36], fill="#ffbd2e")
    draw.ellipse([66, 24, 78, 36], fill="#27c93f")
    draw.text((WIDTH // 2 - 140, 22), "tmux · agent-multiplexer (3 windows)", font=font_sm, fill=TITLEBAR_TEXT)

    # Background Terminal Area
    draw.rectangle([11, 48, WIDTH - 11, HEIGHT - 11], fill="#0d1117")
    draw.text((25, 60), "dev@terminal:~/src $ tmux-agent-switcher popup", font=font_main, fill="#8b949e")

    # 2. Floating Popup (88% W x 82% H)
    px1, py1 = 50, 85
    px2, py2 = WIDTH - 50, HEIGHT - 35
    draw.rounded_rectangle([px1, py1, px2, py2], radius=8, fill=POPUP_BG, outline=POPUP_BORDER, width=2)

    # Header / Prompt
    draw.text((px1 + 18, py1 + 15), "🔎 Agent Switcher >", font=font_bold, fill=COL_CYAN)
    hint_text = "Enter: Jump | Tab: Preview | Ctrl-o: Full History | Ctrl-y: Copy | Ctrl-x: Kill"
    draw.text((px1 + 200, py1 + 17), hint_text, font=font_sm, fill="#64748b")
    draw.line([px1 + 15, py1 + 38, px2 - 15, py1 + 38], fill=POPUP_BORDER, width=1)

    # Split: Left (440px), Right (Split Preview)
    split_x = px1 + 450
    draw.line([split_x, py1 + 39, split_x, py2 - 15], fill=POPUP_BORDER, width=1)

    # Left: Agent Rows
    y = py1 + 50
    for idx, r in enumerate(ROWS):
        is_sel = (idx == selected_idx)
        if is_sel:
            draw.rounded_rectangle([px1 + 10, y - 3, split_x - 10, y + 21], radius=4, fill=CURSOR_BG)

        # Pane ID
        draw.text((px1 + 18, y), f"{r['pane']:<9}", font=font_main, fill=COL_CYAN if is_sel else "#64748b")
        # Current Mark
        mark_col = COL_RUN if r["mark"] == "▶" else "#64748b"
        draw.text((px1 + 95, y), r["mark"], font=font_bold, fill=mark_col)
        # Harness Badge
        draw.text((px1 + 115, y), f"{r['h']:<11}", font=font_bold, fill=r["h_c"])
        # Status
        draw.text((px1 + 220, y), f"{r['s']:<9}", font=font_bold, fill=r["s_c"])
        # Age
        draw.text((px1 + 300, y), f"{r['age']:>4}", font=font_main, fill="#64748b")
        # Title
        t_col = COL_WHITE if is_sel else "#94a3b8"
        draw.text((px1 + 345, y), r["title"][:14] + "..", font=font_main, fill=t_col)

        y += 28

    # Right: Preview Pane
    preview_data = PREVIEWS.get(selected_idx, PREVIEWS[0])
    rx = split_x + 20
    ry = py1 + 50

    # Header
    draw.text((rx, ry), f"━━━ {preview_data['header']} ━━━", font=font_bold, fill=COL_CYAN)
    ry += 26

    for kind, val in preview_data["lines"]:
        if kind == "gap":
            ry += 8
        elif kind == "badge":
            draw.text((rx, ry), val, font=font_sm, fill="#475569")
            ry += 18
        elif kind == "user_pill":
            draw.rounded_rectangle([rx, ry, rx + 80, ry + 18], radius=3, fill="#4c1d95")
            draw.text((rx + 8, ry + 2), val, font=font_bold, fill="#f3e8ff")
            ry += 22
        elif kind == "active_pill":
            draw.rounded_rectangle([rx, ry, rx + 135, ry + 18], radius=3, fill="#78350f")
            draw.text((rx + 8, ry + 2), val, font=font_bold, fill="#fef3c7")
            ry += 22
        elif kind in ("user_text", "active_text"):
            bar_col = COL_AMBER if kind == "active_text" else COL_PURPLE
            draw.line([rx + 8, ry - 4, rx + 8, ry + 14], fill=bar_col, width=2)
            draw.text((rx + 18, ry), val, font=font_main, fill=COL_WHITE)
            ry += 18
        elif kind == "running":
            draw.text((rx + 10, ry), val, font=font_bold, fill="#fbbf24")
            ry += 20
        elif kind == "heading":
            draw.text((rx, ry), val, font=font_bold, fill=COL_CYAN)
            ry += 20
        elif kind == "text":
            draw.text((rx, ry), val, font=font_main, fill="#cbd5e1")
            ry += 18
        elif kind in ("code", "fence"):
            draw.text((rx + 10, ry), val, font=font_main, fill=COL_MINT if kind == "code" else "#475569")
            ry += 17
        elif kind.startswith("table"):
            draw.text((rx, ry), val, font=font_main, fill=COL_WHITE if kind == "table_row" else COL_BORDER)
            ry += 17
        elif kind == "footer":
            draw.text((rx + 20, ry + 5), val, font=font_sm, fill="#475569")
            ry += 18

    return img


def build_gif() -> None:
    temp_dir = tempfile.mkdtemp(prefix="switcher_frames_")
    frames = []

    print("🎨 Rendering synthetic terminal frames...")
    # Animation timeline:
    # 0 -> Hold Claude (12 frames)
    # 1 -> Move to AGY (14 frames)
    # 2 -> Move to Codex (14 frames)
    # 3 -> Move to Cursor (10 frames)
    # Back to 0 -> Claude (8 frames)
    sequence = [0] * 12 + [1] * 14 + [2] * 14 + [3] * 10 + [0] * 8

    for f_idx, sel_row in enumerate(sequence):
        frame_img = render_frame(sel_row)
        frame_path = os.path.join(temp_dir, f"frame_{f_idx:03d}.png")
        frame_img.save(frame_path)
        frames.append(frame_path)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    palette_path = os.path.join(temp_dir, "palette.png")

    print("🎬 Generating palette-optimized GIF with ffmpeg...")
    # 1. Generate optimal 256-color palette
    subprocess.run([
        "ffmpeg", "-y", "-framerate", "10",
        "-i", os.path.join(temp_dir, "frame_%03d.png"),
        "-vf", "palettegen=max_colors=256:stats_mode=diff",
        palette_path
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. Output GIF with paletteuse
    subprocess.run([
        "ffmpeg", "-y", "-framerate", "10",
        "-i", os.path.join(temp_dir, "frame_%03d.png"),
        "-i", palette_path,
        "-lavfi", "paletteuse=dither=bayer:bayer_scale=5",
        OUTPUT_GIF
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    shutil.rmtree(temp_dir)
    size_kb = os.path.getsize(OUTPUT_GIF) // 1024
    print(f"✅ Generated demo GIF at: {OUTPUT_GIF} ({size_kb} KB)")


if __name__ == "__main__":
    build_gif()
