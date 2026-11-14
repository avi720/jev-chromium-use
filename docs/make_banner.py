"""Generate docs/banner.svg, the README header.

    python docs/make_banner.py

The right side is a stream of the loop's real step types and confidences, so the picture says
what the project does: one small, scored decision after another.
"""

import random
from pathlib import Path

W, H = 1280, 420
BG, PANEL = "#0b0f14", "#111821"
INK, MUTED = "#e8edf2", "#7d8a99"
TEAL, AMBER, VIOLET, ROSE, SKY = "#2dd4bf", "#fbbf24", "#a78bfa", "#fb7185", "#38bdf8"

# Chips: (text, color, filled). Weighted toward clicks, like a real run.
CHIPS = [
    ("click 1.00", TEAL, True), ("click 0.97", TEAL, False), ("click 0.82", TEAL, False), ("click 0.99", TEAL, False),
    ("click 0.91", TEAL, False), ("type 0.88", SKY, True), ("type 0.79", SKY, False), ("choose 0.94", VIOLET, False),
    ("done 0.93", AMBER, True), ("goal_reached 0.98", AMBER, False), ("fits 0.98", MUTED, False),
    ("look 0.96", VIOLET, True), ("needs_visual", VIOLET, False), ("scroll", MUTED, False),
    ("ask", ROSE, True), ("handoff 0.53", ROSE, False), ("Choice", SKY, False), ("Noul", SKY, False),
    ("$0.0001", AMBER, False), ("312ms", MUTED, False),
    # Hebrew labels stand alone: mixing them with Latin text in one chip flips the order on screen.
    ("חיפוש", TEAL, False), ("כלכלה", TEAL, False), ("סניפים", TEAL, False), ("רפאל", SKY, False),
    ("שלח לעיבוד", ROSE, False), ("כיוון", VIOLET, False), ("הזנת טריידים", TEAL, False),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")


def chip_width(text: str) -> int:
    return 14 + 8 * len(text)


def stream() -> str:
    rnd = random.Random(7)
    out, x0, y = [], 610, 34
    row = 0
    while y < H - 20:
        # Rows start further right near the top, so the stream fans in from the corner.
        x = x0 + max(0, 170 - row * 18) + rnd.randint(-10, 10)
        while x < W - 20:
            text, color, filled = rnd.choice(CHIPS)
            w = chip_width(text)
            if x + w > W - 14:
                break
            fade = 0.35 + 0.65 * (x - x0) / (W - x0)
            fill = color if filled else "none"
            ink = BG if filled else color
            out.append(
                f'<g opacity="{fade:.2f}"><rect x="{x}" y="{y}" width="{w}" height="22" rx="4" fill="{fill}" '
                f'stroke="{color}" stroke-width="1.2"/>'
                f'<text x="{x + w / 2}" y="{y + 15.5}" fill="{ink}" font-size="12.5" text-anchor="middle" '
                f'font-family="ui-monospace, SFMono-Regular, Consolas, monospace">{esc(text)}</text></g>'
            )
            x += w + 6 + rnd.randint(0, 8)
        y += 30
        row += 1
    return "\n".join(out)


def bars() -> str:
    """A Choice answer as Jev returns it: one probability per option."""
    opts = [("חיפוש", 0.97, TEAL), ("כניסה", 0.02, MUTED), ("מבזקים", 0.01, MUTED)]
    out, y = [], 288
    for label, p, color in opts:
        out.append(
            f'<text x="72" y="{y + 12}" fill="{MUTED}" font-size="13" font-family="ui-monospace, Consolas, monospace">{label}</text>'
            f'<rect x="150" y="{y}" width="300" height="14" rx="3" fill="{PANEL}"/>'
            f'<rect x="150" y="{y}" width="{max(3, 300 * p):.0f}" height="14" rx="3" fill="{color}"/>'
            f'<text x="462" y="{y + 12}" fill="{INK}" font-size="13" font-family="ui-monospace, Consolas, monospace">{p:.2f}</text>'
        )
        y += 24
    return "\n".join(out)


svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="jev-chromium-use: a small decision model picks every browser step">
<defs>
  <linearGradient id="fadeL" x1="0" x2="1" y1="0" y2="0">
    <stop offset="0" stop-color="{BG}" stop-opacity="1"/><stop offset="1" stop-color="{BG}" stop-opacity="0"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.18" cy="0.35" r="0.55">
    <stop offset="0" stop-color="{TEAL}" stop-opacity="0.16"/><stop offset="1" stop-color="{TEAL}" stop-opacity="0"/>
  </radialGradient>
</defs>
<rect width="{W}" height="{H}" rx="14" fill="{BG}"/>
<rect width="{W}" height="{H}" rx="14" fill="url(#glow)"/>
{stream()}
<rect x="560" y="0" width="160" height="{H}" fill="url(#fadeL)"/>
<text x="70" y="128" fill="{INK}" font-size="92" font-weight="800" letter-spacing="-3" font-family="Inter, 'Segoe UI', Helvetica, Arial, sans-serif">jev</text>
<text x="70" y="178" fill="{INK}" font-size="40" font-weight="600" letter-spacing="-1" font-family="Inter, 'Segoe UI', Helvetica, Arial, sans-serif">chromium-use</text>
<text x="72" y="222" fill="{VIOLET}" font-size="19" font-family="ui-monospace, SFMono-Regular, Consolas, monospace">a small model picks every step</text>
<text x="72" y="248" fill="{AMBER}" font-size="19" font-family="ui-monospace, SFMono-Regular, Consolas, monospace">your browser, your logins, in the background</text>
{bars()}
<text x="72" y="374" fill="{MUTED}" font-size="12.5" font-family="ui-monospace, Consolas, monospace">Choice: which control? · 312 ms · $0.0001</text>
</svg>
"""

out = Path(__file__).parent / "banner.svg"
out.write_text(svg, encoding="utf-8")
print(f"wrote {out} ({len(svg)} bytes)")
