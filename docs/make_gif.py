"""Turn a recorded run into docs/demo.gif.

    python jev_ext_loop.py <url> "<goal>" --record runs/demo
    python docs/make_gif.py runs/demo

Every frame is a real screenshot of the loop's tab, taken before the step acted, with the target
the loop chose outlined and the decision written underneath, the way the terminal printed it.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = 960
BAR_H, LOG_H = 38, 222
BG, PANEL, INK, MUTED = (11, 15, 20), (17, 24, 33), (232, 237, 242), (125, 138, 153)
COLORS = {"type": (56, 189, 248), "click": (45, 212, 191), "choose": (167, 139, 250), "done": (251, 191, 36),
          "look": (167, 139, 250), "scroll": (125, 138, 153), "handoff": (251, 113, 133), "end": (251, 191, 36)}
FONTS = Path("C:/Windows/Fonts")
MONO = ImageFont.truetype(str(FONTS / "cour.ttf"), 15)
MONO_B = ImageFont.truetype(str(FONTS / "courbd.ttf"), 15)
SANS = ImageFont.truetype(str(FONTS / "arial.ttf"), 14)
SANS_B = ImageFont.truetype(str(FONTS / "arialbd.ttf"), 15)
GOAL = ImageFont.truetype(str(FONTS / "arialbd.ttf"), 18)

HEB = re.compile(r"[\u0590-\u05FF](?:[\u0590-\u05FF\s'\"\u05F3\u05F4-]*[\u0590-\u05FF])?")


def vis(text: str) -> str:
    """Pillow without libraqm draws characters left to right; reverse each Hebrew run so it reads right."""
    return HEB.sub(lambda m: m.group(0)[::-1], text)


def line_for(f: dict) -> tuple[str, tuple]:
    do, t = f["do"], f.get("target")
    if do == "type":
        s = f"-> type {f.get('text', '')!r} into {t['text'][:28]!r}"
    elif do in ("click", "choose"):
        s = f"-> {do} {t['role']}: {t['text'][:34]!r}"
    elif do == "end":
        s = f"outcome: {f['why']}"
    else:
        s = f"-> {do}  ({f['why']})"
    if do not in ("end",) and f.get("confidence") is not None and do != "done":
        s += f"  conf {f['confidence']:.2f}"
    if f.get("ms"):
        s += f"  · {f['ms']} ms"
    return s, COLORS.get(do, INK)


def frame(run: dict, folder: Path, upto: int, highlight: dict | None, footer: str) -> Image.Image:
    f = run["frames"][upto]
    shot = Image.open(folder / f["image"]).convert("RGB")
    scale = W / shot.width
    shot = shot.resize((W, round(shot.height * scale)), Image.LANCZOS)
    img = Image.new("RGB", (W, BAR_H + shot.height + LOG_H), BG)
    d = ImageDraw.Draw(img)
    # A slim browser bar: the tab group the loop works in, and the address.
    d.rectangle([0, 0, W, BAR_H], fill=PANEL)
    group = "Jev: " + vis(run["goal"][:28])  # the same title the loop gives its tab group
    gw = d.textlength(group, font=SANS_B) + 22
    d.rounded_rectangle([10, 8, 10 + gw, BAR_H - 8], 6, fill=(124, 58, 237))
    d.text((21, 11), group, font=SANS_B, fill=(255, 255, 255))
    from urllib.parse import unquote
    d.text((gw + 26, 11), vis(unquote(f["url"])[:80]), font=SANS, fill=MUTED)
    img.paste(shot, (0, BAR_H))
    if highlight and highlight.get("target") and highlight["target"].get("box"):
        x, y, w, h = highlight["target"]["box"]
        k = W / highlight["viewport"][0]
        box = [x * k - 5, BAR_H + y * k - 5, (x + w) * k + 5, BAR_H + (y + h) * k + 5]
        color = COLORS.get(highlight["do"], INK)
        d.rounded_rectangle(box, 6, outline=color, width=4)
        tag = f"{highlight['do']} {highlight['confidence']:.2f}" if highlight.get("confidence") is not None else highlight["do"]
        tw = d.textlength(tag, font=MONO_B) + 14
        ty = box[1] - 26 if box[1] - 26 > BAR_H else box[3] + 4
        d.rounded_rectangle([box[0], ty, box[0] + tw, ty + 22], 5, fill=color)
        d.text((box[0] + 7, ty + 3), tag, font=MONO_B, fill=BG)
    # The terminal underneath: the command, then one line per decision so far.
    top = BAR_H + shot.height
    d.rectangle([0, top, W, top + LOG_H], fill=BG)
    d.line([0, top, W, top], fill=(40, 52, 66), width=2)
    y = top + 12
    # The goal in full, on its own line: without it the clicks that follow make no sense.
    d.text((16, y), f'> python jev_ext_loop.py {run["url"]} "<goal>"', font=MONO_B, fill=INK)
    y += 22
    goal = vis(f'"{run["goal"]}"')
    d.text((W - 16 - d.textlength(goal, font=GOAL), y - 1), goal, font=GOAL, fill=COLORS["done"])
    d.text((16, y + 1), "goal:", font=MONO, fill=MUTED)
    y += 30
    for g in run["frames"][: upto + (1 if highlight else 0)]:
        if g["do"] == "end" and g is not highlight:
            continue
        text, color = line_for(g)
        if g["do"] != "end":
            d.text((16, y), f"step {g['step']}", font=MONO, fill=MUTED)
        d.text((96, y), vis(text), font=MONO, fill=color)
        y += 22
    d.text((16, top + LOG_H - 28), footer, font=SANS, fill=MUTED)
    return img


def main(folder: Path) -> None:
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    decisions = len([f for f in run["frames"] if f["do"] != "end"])
    foot = "Jev picks every step  ·  real run in a background tab  ·  speed not to scale"
    images, durations = [], []
    images.append(frame(run, folder, 0, None, foot))
    durations.append(1800)
    for i, f in enumerate(run["frames"]):
        if f["do"] == "end":
            end = f"{decisions} Jev decisions · {run['tokens']:,} input tokens · ${run['usd']:.5f} in total"
            images.append(frame(run, folder, i, f, end))
            durations.append(4200)
        else:
            images.append(frame(run, folder, i, f, foot))
            durations.append(2600 if f["do"] != "done" else 2200)
    out = Path(__file__).parent / "demo.gif"
    pal = [im.quantize(colors=160, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for im in images]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True)
    print(f"wrote {out}: {len(images)} frames, {out.stat().st_size / 1e6:.2f} MB, {images[0].size}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]))
