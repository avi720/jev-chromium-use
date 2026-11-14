"""Jev drives a tab in your everyday, signed-in Chromium browser (Edge or Chrome) through the Jev Loop Bridge extension.

    python jev_ext_loop.py --setup                                   # once: write the token, print install steps
    python jev_ext_loop.py https://www.ynet.co.il "פתח את מדור הכלכלה"

The loop opens a background tab in the browser window that is already open, inside a tab group
titled after the goal, and works only in that tab: no window pops up, nothing takes focus, and
the user's own tabs are never read or touched. If no browser with the extension is running, one
is started normally, so it comes up with the user's own tabs.

Because the profile is signed in:
- A control whose label reads like a commitment (buy, pay, send, delete, publish, confirm...)
  always asks first, even with `--yes`; with no terminal to ask in, the loop hands off.
- Typing asks first unless `--yes`. Password, card and ID fields are never typed into.
"""

import argparse
import base64
import json
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from websockets.sync.server import serve

import visual

from jev_core import BROWSERS, COMMIT_CONF, JEV_MODEL, VISUAL_AT, jev_client, DONE_AT, MIN_CONF, choose_option, commits, describe, USD_PER_TOKEN, choose_text, decide, plan, text_candidates

EXT_DIR = Path(__file__).parent / "jev-bridge-extension"
TOKEN_FILE = EXT_DIR / "token.js"
PORT = 8765
SHOTS_DIR = Path(tempfile.gettempdir()) / "jev-loop" / "screenshots"


def setup() -> None:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(f'const JEV_TOKEN = "{secrets.token_urlsafe(32)}";\n', encoding="utf-8")
        print("wrote a new token")
    print(f"""
Install the bridge in your everyday browser (Edge or Chrome), once:
  1. Open edge://extensions (Edge) or chrome://extensions (Chrome)
  2. Turn on "Developer mode"
  3. Click "Load unpacked" and choose this folder:
       {EXT_DIR}
Keep that folder where it is: the browser loads the extension from it every time.
""")


def read_token() -> str:
    if not TOKEN_FILE.exists():
        raise SystemExit("No token yet. Run: python jev_ext_loop.py --setup")
    return re.search(r'"([^"]+)"', TOKEN_FILE.read_text(encoding="utf-8")).group(1)


class Bridge:
    """Runs the local server and waits for the extension to connect to it."""

    def __init__(self, browser: str = "auto") -> None:
        self.browser = browser
        self.token = read_token()
        self._conn: queue.Queue = queue.Queue()
        self._done = threading.Event()
        self._id = 0
        try:
            self.server = serve(self._handler, "127.0.0.1", PORT, max_size=2**25)  # screenshots are large
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or exc.errno in (48, 98, 10048):
                raise SystemExit(f"Another run of the loop is using port {PORT}. The extension serves one run at a "
                                 "time: wait for it to finish, then run again.") from None
            raise
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.ws = self._wait_for_extension()

    def _handler(self, ws) -> None:
        # Only a browser extension may drive this: a web page that finds the port is turned away.
        if not (ws.request.headers.get("Origin") or "").startswith("chrome-extension://"):
            return
        ws.send(json.dumps({"hello": self.token}))
        if json.loads(ws.recv(timeout=10)).get("hello") != "ok":
            return
        self._conn.put(ws)
        self._done.wait()  # returning would close the connection

    def _wait_for_extension(self):
        images = {"edge": "msedge.exe", "chrome": "chrome.exe"}
        wanted = [self.browser] if self.browser != "auto" else ["edge", "chrome"]
        tasks = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
        if not any(images[b] in tasks for b in wanted):
            installed = [p for b in wanted for p in BROWSERS if images[b] in p.lower() and Path(p).exists()]
            if not installed:
                raise SystemExit(f"No {' or '.join(wanted)} found on this machine")
            print(f"no browser is running: starting {Path(installed[0]).stem} with your own profile and tabs")
            subprocess.Popen([installed[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("waiting for the Jev Loop Bridge extension to connect (up to 45s)...")
        try:
            return self._conn.get(timeout=45)
        except queue.Empty:
            raise SystemExit("The extension did not connect. Is it installed and enabled in your browser? "
                             "See: python jev_ext_loop.py --setup") from None

    def call(self, cmd: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "cmd": cmd, **params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == self._id:
                if not msg["ok"]:
                    raise RuntimeError(f"{cmd}: {msg['error']}")
                return msg["result"]

    def close(self) -> None:
        self._done.set()
        self.server.shutdown()


class Tab:
    def __init__(self, bridge: Bridge, url: str, group_title: str):
        self.b = bridge
        self.id = bridge.call("open", url=url, title=group_title)["tabId"]

    def settle(self, seconds: float = 2.5) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and self.b.call("status", tabId=self.id)["status"] != "complete":
            time.sleep(0.4)
        time.sleep(seconds)

    def perceive(self, hide_overlays: bool, own_click_url: str | None = None) -> dict:
        """Read the page. Returns None when a dialog covers it and the user did not clear it.

        On a signed-in profile a covering dialog is rare (a site never visited, or cookies cleared),
        and answering a consent prompt is the user's call, so the default is to ask them.
        `--hide-overlays` hides it without answering instead.

        A cover that appears on the same URL right after the loop's own click (`own_click_url`) is the
        menu or modal that click opened, not a consent wall: the loop carries on inside it.
        """
        page = self._read()
        while page["covered"] > 2 * len(page["elements"]) and page["url"] != own_click_url:
            if hide_overlays:
                hidden = self.b.call("hide_overlays", tabId=self.id)
                if hidden:
                    print(f"  hid blocking overlay without answering it: {', '.join(hidden)}")
                    page = self._read()
                break
            if not sys.stdin.isatty():
                return None
            self.b.call("show", tabId=self.id)
            if ask_user("  A dialog covers the page (a cookie or consent prompt?). The tab is now in front:\n"
                        "  answer the dialog yourself, then press Enter here to continue...") is None:
                return None
            page = self._read()
        return page

    def _read(self) -> dict:
        try:
            return self.b.call("perceive", tabId=self.id)
        except RuntimeError:  # mid-navigation: the page being read went away
            time.sleep(2)
            return self.b.call("perceive", tabId=self.id)


def _norm(s: str) -> str:
    return re.sub(r"[\s*:()\[\]]+", " ", s.split(" (e.g.")[0]).strip().lower()


def match_field(els: list[dict], label: str) -> int | None:
    """The field or dropdown whose label is `label` (ignoring '*', ':' and the sample value). Exact first, then contains."""
    want = _norm(label)
    usable = [(i, _norm(e["text"])) for i, e in enumerate(els)
              if (e.get("field") and not e["secret"]) or e.get("choice") or e.get("popup")]
    for i, name in usable:
        if name == want:
            return i
    for i, name in usable:
        if want and want in name:
            return i
    return None


def match_option(options: list[str], value: str) -> int | None:
    want = _norm(value)
    for i, o in enumerate(options):
        if _norm(o) == want:
            return i
    hits = [i for i, o in enumerate(options) if want and want in _norm(o)]
    return hits[0] if len(hits) == 1 else None


def record_frame(bridge: "Bridge", tab: "Tab", folder: str, step: int, page: dict, decision: dict) -> dict:
    """--record: save what the tab shows before the step acts, with the decision, for docs/make_gif.py."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    image = Path(folder) / f"step{step:02d}.jpg"
    image.write_bytes(base64.b64decode(bridge.call("screenshot", tabId=tab.id)["data"]))
    return {"step": step, "image": image.name, "url": page["url"], "viewport": page.get("viewport"), **decision}


def full_page_screenshot(bridge: "Bridge", tab_id: int, max_shots: int = 8) -> bytes:
    """Scroll the page's main scroller a screen at a time and stack the new part of each screenshot.

    Works for a page that scrolls as a whole and for a web app whose content scrolls inside one container
    (there a single "beyond the viewport" capture shows only the first screen). Up to `max_shots` screens.
    """
    from io import BytesIO

    from PIL import Image as PILImage

    start = bridge.call("scroll_main", tabId=tab_id, to=None)
    info = bridge.call("scroll_main", tabId=tab_id, to=0)
    shots, tops = [], []
    for _ in range(max_shots):
        time.sleep(0.5)
        shots.append(PILImage.open(BytesIO(base64.b64decode(bridge.call("screenshot", tabId=tab_id)["data"]))).convert("RGB"))
        tops.append(info["top"])
        if info["top"] >= info["max"] - 1:
            break
        info = bridge.call("scroll_main", tabId=tab_id, to=info["top"] + info["view"] * 0.9)
    bridge.call("scroll_main", tabId=tab_id, to=start["top"])
    k = shots[0].width / info["vw"]  # image pixels per CSS pixel
    box_bottom = round((info["box_top"] + info["box_height"]) * k)
    parts = [shots[0]]
    for prev, top, shot in zip(tops, tops[1:], shots[1:]):
        new = round((top - prev) * k)  # how far the content moved: the new rows sit at the bottom of the scroller
        if new > 0:
            parts.append(shot.crop((0, max(0, box_bottom - new), shot.width, box_bottom)))
    out = PILImage.new("RGB", (shots[0].width, sum(p.height for p in parts)))
    y = 0
    for part in parts:
        out.paste(part, (0, y))
        y += part.height
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def ask_user(prompt: str) -> str | None:
    """Ask in the terminal. None when there is nobody to ask (no terminal, or input is closed)."""
    if not sys.stdin.isatty():
        return None
    try:
        return input(prompt)
    except EOFError:
        return None


def confirm(what: str) -> bool:
    answer = ask_user(f"  {what}\n  proceed? [y/N] ")
    return answer is not None and answer.strip().lower() in ("y", "yes", "כ", "כן")


def run(a: argparse.Namespace) -> None:
    if a.visual == "api" and not visual.has_credentials():
        raise SystemExit("--visual api needs Anthropic credentials (ANTHROPIC_API_KEY). Use --visual agent to look yourself.")
    values = dict(v.split("=", 1) for v in a.value)
    # Jev selects what to type, it does not write it: the candidates are --text entries, --value values, and
    # every phrase of the goal. A query that is none of those cannot be chosen, so pass it with --text.
    candidates = list(dict.fromkeys([*a.text, *values.values()])) if a.text else text_candidates(a.goal, values)
    bridge = Bridge(a.browser)
    history: list[str] = [f"someone looked at the screen earlier and reported: {o}" for o in a.observation]
    applied: set[str] = set()  # labels of --value pairs already placed
    looked: set[str] = set()   # page states Claude has already looked at
    frames: list[dict] = []    # --record: one screenshot and decision per step
    visual_reports: list[dict] = []
    llm_cost = 0.0
    second_passes = 0
    decisions = 0
    outcome, tokens = "step limit", 0
    try:
        tab = Tab(bridge, a.url, "Jev: " + a.goal[:28])
        print(f"opened a background tab in the group 'Jev: {a.goal[:28]}'")
        tab.settle()
        with jev_client() as client:
            for step in range(1, a.steps + 1):
                last = history[-1] if history else ""
                page = tab.perceive(a.hide_overlays, own_click_url=last.rsplit(" on ", 1)[-1] if last.startswith("clicked") else None)
                if page is None:
                    outcome = "handoff: a dialog covers the page (cookie or consent prompt?), yours to answer"
                    break
                els = page["elements"]
                typed = {h for h in history if h.startswith("typed")}
                # Values the user gave are facts, not judgments: code puts each one in the field with that label,
                # even a field that already shows a default (a date set to today, a dropdown on Long).
                if step == 1 and values:
                    unmatched = [k for k in values if match_field(els, k) is None]
                    if unmatched:
                        names = [e["text"].split(" (e.g.")[0] for e in els if e.get("field") or e.get("choice") or e.get("popup")]
                        print(f"  note: no field on this page is labeled {unmatched}; the fields here are {names[:20]}")
                placed_one = False
                for label, value in values.items():
                    if label in applied:
                        continue
                    i = match_field(els, label)
                    if i is None:
                        continue  # not on this screen (yet)
                    target = els[i]
                    print(f"\nstep {step}  {page['url'][:90]}\n  -> your value for {label!r}: {value!r} into {describe(target)[:70]!r}")
                    if target.get("popup"):
                        # A custom dropdown: open it, then click the option with that text in the list it shows.
                        if _norm(target.get("value") or "") != _norm(value):
                            bridge.call("click", tabId=tab.id, index=i)
                            time.sleep(0.8)
                            listed = tab.perceive(True)["elements"]
                            options = [k for k, e in enumerate(listed) if e["role"] in ("option", "menuitem", "menuitemradio", "li")]
                            pick = match_option([listed[k]["text"] for k in options], value)
                            if pick is None:
                                outcome = (f"handoff: '{value}' is not in the list '{target['text'][:40]}' opened: "
                                           f"{[listed[k]['text'][:30] for k in options][:12]}")
                                break
                            bridge.call("click", tabId=tab.id, index=options[pick])
                            print(f"  chose {listed[options[pick]]['text']!r}")
                        history.append(f"set dropdown '{target['text'][:60]}' to '{value}' on {page['url']}")
                    elif target.get("choice"):
                        opt = match_option(target["options"], value)
                        if opt is None:
                            outcome = f"handoff: '{value}' is not one of the options of '{target['text'][:40]}': {target['options']}"
                            break
                        if target["options"][opt] != target.get("value"):
                            print("  " + bridge.call("choose", tabId=tab.id, index=i, option=opt))
                        history.append(f"set dropdown '{target['text'][:60]}' to '{target['options'][opt]}' on {page['url']}")
                    else:
                        if not (a.yes or confirm(f"about to type {value!r} into {target['text'][:60]!r}")):
                            outcome = "handoff: typing needs your confirmation"
                            break
                        print("  " + bridge.call("type", tabId=tab.id, index=i, text=value, submit=False))
                        history.append(f"typed '{value}' into field '{target['text'][:60]}' on {page['url']}")
                    applied.add(label)
                    if applied == set(values):
                        history.append("every value the user gave is now in its field")
                    placed_one = True
                    break
                if outcome != "step limit":
                    break
                if placed_one:
                    time.sleep(0.8)
                    continue
                t0 = time.perf_counter()
                # Perception lists on-screen controls first, so the on-screen ones are a prefix of the list and keep
                # their indexes. "two-pass" asks about those first and sends the full list only when Jev is unsure
                # or would scroll; "onscreen" never sends the rest; "full" always does.
                n_on = sum(1 for e in els if not e.get("offscreen"))
                full_page = page
                if a.perception != "full" and n_on < len(els):
                    page = {**full_page, "elements": els[:n_on]}
                resp = decide(client, a.goal, page, history, can_type=any(e.get("field") and not e["secret"] for e in page["elements"]))
                tokens += resp.usage.input_tokens
                if a.perception == "two-pass" and page is not full_page:
                    el = resp.answers.get("element")
                    unsure = el is None or el.confidence < a.min_conf
                    would_scroll = resp.answers["kind"].probabilities.get("scroll_down", 0.0) >= 0.3
                    if (unsure or would_scroll) and resp.answers["goal_reached"].noul < DONE_AT:
                        print(f"  second pass: {'unsure' if unsure else 'would scroll'} on screen, asking with all {len(els)} controls")
                        page = full_page
                        resp = decide(client, a.goal, page, history, can_type=any(e.get("field") and not e["secret"] for e in els))
                        tokens += resp.usage.input_tokens
                        second_passes += 1
                ms = (time.perf_counter() - t0) * 1000
                els = page["elements"]
                decisions += 1
                page_key = page["url"] + "|" + "|".join(page["page_text"][:8])  # one look per page state
                if step == 1 and a.observation:
                    looked.add(page_key)  # the observation you passed is the look at this page
                step_plan = plan(resp, page, already_typed=bool(typed), min_conf=a.min_conf,
                                 already_looked=page_key in looked or a.visual == "off")
                do, target = step_plan["do"], (els[step_plan["index"]] if step_plan["index"] is not None else None)
                nouls = {k: round(v.noul, 2) for k, v in resp.answers.items() if hasattr(v, "noul")}
                print(f"\nstep {step}  {page['url'][:90]}\n  {len(els)} elements, decision {ms:.0f}ms, {resp.usage.input_tokens} tokens, {nouls}"
                      f"\n  -> {do}" + (f" {target['role']}: {target['text'][:70]!r}" if target else "") + f"  ({step_plan['why']})")
                if a.record:
                    frames.append(record_frame(bridge, tab, a.record, step, page, {
                        "do": do, "why": step_plan["why"], "ms": round(ms), "tokens": resp.usage.input_tokens,
                        "target": target and {"role": target["role"], "text": target["text"], "box": target.get("box")},
                        "confidence": round(resp.answers["element"].confidence, 2) if "element" in resp.answers else None,
                        "nouls": nouls}))
                if (do == "done" and a.visual == "off" and page_key not in looked
                        and resp.answers["needs_visual"].noul >= VISUAL_AT):
                    # Nobody looked, so a "done" on a goal about appearance would be a guess.
                    outcome = "handoff: the goal needs someone to look at the page, and --visual is off"
                    break
                if do == "look":
                    looked.add(page_key)
                    shot = bridge.call("screenshot", tabId=tab.id)["data"]
                    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
                    shot_path = SHOTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-step{step}.jpg"
                    shot_path.write_bytes(base64.b64decode(shot))
                    print(f"  screenshot: {shot_path}")
                    if a.visual == "agent" or (a.visual == "auto" and not visual.has_credentials()):
                        # The agent that runs the loop (Claude Code, or you) looks at the file, then runs the loop
                        # again with what it saw in --observation, and Jev carries on from there.
                        outcome = f"handoff to the agent: this step needs eyes. Look at the screenshot: {shot_path}"
                        break
                    if a.visual == "jev":
                        seen = visual.jev_look(a.goal, page, base64.b64decode(shot), JEV_MODEL)
                        tokens += seen["tokens"]
                    else:
                        seen = visual.look(a.goal, page, history, shot)
                        llm_cost += seen["cost_usd"]
                    if seen["refused"]:
                        outcome = f"handoff: Claude declined to judge the screenshot ({shot_path})"
                        break
                    who = "Jev" if a.visual == "jev" else f"Claude ({visual.MODEL}, ${seen['cost_usd']:.4f})"
                    print(f"  {who}: {seen['summary']}")
                    for issue in seen["issues"]:
                        print(f"    [{issue['severity']}] {issue['where']}: {issue['what']}")
                    visual_reports.append(seen)
                    issues = "; ".join(f"{i['where']}: {i['what']}" for i in seen["issues"]) or "no visual issues"
                    history.append(f"Claude looked at a screenshot of {page['url']}: {seen['summary']} Issues: {issues}")
                    if seen["goal_met"]:
                        outcome = f"done (judged by {'Jev' if a.visual == 'jev' else 'Claude'} from a screenshot)"
                        break
                    continue
                if do == "done":
                    outcome = "done"
                    break
                if do == "handoff":
                    if target is not None and "element" in resp.answers:
                        top = sorted(resp.answers["element"].probabilities.items(), key=lambda kv: -kv[1])[:3]
                        print("  candidates: " + "; ".join(f"{els[int(k[1:])]['text'][:50]!r} {p:.2f}" for k, p in top))
                    outcome = "handoff: " + step_plan["why"]
                    break
                if a.dry_run:
                    outcome = f"dry run: would {do}"
                    break
                if do == "scroll":
                    history.append(f"scrolled down on {page['url']}")
                    bridge.call("scroll", tabId=tab.id)
                    time.sleep(1.0)
                    continue
                if do == "choose":
                    ans = choose_option(client, a.goal, page, target, values)
                    tokens += ans.usage.input_tokens
                    pick = ans.answers["option"]
                    option = target["options"][int(pick.choice[1:])]
                    print(f"  option: {option!r} (conf {pick.confidence:.2f}; now {target.get('value')!r})")
                    if pick.confidence < a.min_conf:
                        outcome = f"handoff: unsure which option '{target['text'][:40]}' needs (confidence {pick.confidence:.2f})"
                        break
                    action = f"set dropdown '{target['text'][:60]}' to '{option}' on {page['url']}"
                    if action in history:
                        outcome = "stall: the same dropdown was set to the same option again"
                        break
                    if option != target.get("value"):
                        print("  " + bridge.call("choose", tabId=tab.id, index=step_plan["index"], option=int(pick.choice[1:])))
                    history.append(action)
                    time.sleep(0.8)
                    continue
                if do == "type":
                    tr = choose_text(client, a.goal, page, target, candidates, values)
                    tokens += tr.usage.input_tokens
                    ans = tr.answers["text"]
                    text = candidates[int(ans.choice[1:])]
                    print(f"  text: {text!r} (conf {ans.confidence:.2f})")
                    if a.record:
                        frames[-1]["text"] = text
                    if not target["search"] and ans.confidence < a.min_conf:
                        outcome = f"handoff: unsure what belongs in '{target['text'][:40]}' (confidence {ans.confidence:.2f})"
                        break
                    action = f"typed '{text}' into field '{target['text'][:60]}' on {page['url']}"
                    if action in typed:
                        outcome = "stall: the same text was chosen for the same field again"
                        break
                    if not (a.yes or confirm(f"about to type {text!r} into {target['text'][:60]!r}")):
                        outcome = "handoff: typing needs your confirmation"
                        break
                    print("  " + bridge.call("type", tabId=tab.id, index=step_plan["index"], text=text, submit=target["search"]))
                    history.append(action + (" and pressed Enter" if target["search"] else ""))
                    tab.settle()
                    continue
                action = f"clicked {target['role']} '{target['text'][:80]}' on {page['url']}"
                if action in history:
                    outcome = "stall: the same click was chosen again"
                    break
                missing = [k for k in values if k not in applied]
                if commits(target["text"]) and missing:
                    outcome = (f"handoff: '{target['text'][:40]}' would submit, but your values for {missing} were not "
                               "placed: no field with that label was found")
                    break
                # Thresholds scale with the stakes: a click that commits something needs a surer pick than a
                # navigation click. An --approve that names this control settles which control is meant.
                approved = any(x.strip() and x.strip() in target["text"] for x in a.approve)
                pick_conf = resp.answers["element"].confidence if "element" in resp.answers else 0.0
                if commits(target["text"]) and pick_conf < COMMIT_CONF and not approved:
                    outcome = (f"handoff: '{target['text'][:60]}' looks like a commitment and the pick is not sure enough "
                               f"(confidence {pick_conf:.2f}, needs {COMMIT_CONF}); --approve with its label settles it")
                    break
                if commits(target["text"]) and not a.allow_commits and not approved and not confirm(f"'{target['text'][:80]}' looks like it commits something"):
                    outcome = f"handoff: '{target['text'][:60]}' looks like a commitment (buy, send, delete...), yours to click"
                    break
                bridge.call("click", tabId=tab.id, index=step_plan["index"])
                history.append(action)
                tab.settle()
        if a.record:
            tab.settle(1)
            frames.append(record_frame(bridge, tab, a.record, len(frames) + 1, tab.perceive(True), {"do": "end", "why": outcome}))
            (Path(a.record) / "run.json").write_text(json.dumps(
                {"url": a.url, "goal": a.goal, "outcome": outcome, "tokens": tokens, "usd": tokens * USD_PER_TOKEN,
                 "frames": frames}, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"recording: {a.record}")
        final = bridge.call("status", tabId=tab.id)
        print(f"\noutcome: {outcome}\nfinal page: {final['title']}\n            {final['url']}"
              f"\ncost: Jev {tokens} input tokens = ${tokens * USD_PER_TOKEN:.5f}"
              + (f"; Claude ${llm_cost:.4f} for {len(visual_reports)} look(s)" if visual_reports else ""))
        print("summary: " + json.dumps({"outcome": outcome, "decisions": decisions, "second_passes": second_passes,
                                         "values_not_placed": [k for k in values if k not in applied],
                                         "tokens": tokens, "usd": round(tokens * USD_PER_TOKEN, 6), "final_url": final["url"]},
                                        ensure_ascii=False))
        if a.screenshot:
            SHOTS_DIR.mkdir(parents=True, exist_ok=True)
            shot_path = SHOTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-final{'-full' if a.screenshot == 'full' else ''}.jpg"
            if a.screenshot == "full":
                shot_path.write_bytes(full_page_screenshot(bridge, tab.id))
            else:
                shot_path.write_bytes(base64.b64decode(bridge.call("screenshot", tabId=tab.id)["data"]))
            print(f"screenshot of the final page: {shot_path}")
        if a.show_page:
            seen = bridge.call("perceive", tabId=tab.id)
            print("\ncontrols on the final page:\n  " + " | ".join(
                describe(e)[:110] for e in seen["elements"]))
            print("text on the final page:\n  " + "\n  ".join(t[:160] for t in seen["page_text"][:40]))
        if a.close_tab:
            bridge.call("close", tabId=tab.id)
        else:
            print("the tab is left open in its group for you")
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?")
    ap.add_argument("goal", nargs="?")
    ap.add_argument("--setup", action="store_true", help="write the token and print the one-time install steps")
    ap.add_argument("--value", action="append", default=[], metavar="LABEL=TEXT", help="a value the loop may type into a form field")
    ap.add_argument("--yes", action="store_true", help="do not ask before typing (commitment-looking clicks still ask)")
    ap.add_argument("--text", action="append", default=[], metavar="TEXT",
                    help="a phrase the loop may type where the goal needs it, such as a search query that is not "
                         "written word for word in the goal. Repeatable")
    ap.add_argument("--approve", action="append", default=[], metavar="LABEL",
                    help="click a commitment-looking control whose label contains LABEL without asking (the yes you "
                         "got from the user for that one control). Repeatable")
    ap.add_argument("--allow-commits", action="store_true",
                    help="click commitment-looking controls (save, send, delete...) without asking. Only for a test account")
    ap.add_argument("--show-page", action="store_true", help="print the controls and text of the final page")
    ap.add_argument("--screenshot", nargs="?", const="screen", choices=["screen", "full"],
                    help="save a screenshot of the final page and print its path, for whoever runs the loop to look at: "
                         "the visible part (default) or 'full' for the whole page, stitched from up to 8 screens")
    ap.add_argument("--perception", choices=["onscreen", "two-pass", "full"], default="onscreen",
                    help="which controls Jev sees: only those on screen, scrolling when needed (default, cheapest); on "
                         "screen first and all of them only when unsure; or always all of them, up to three screens away")
    ap.add_argument("--browser", choices=["auto", "edge", "chrome"], default="auto",
                    help="which browser to start when none is running (default: Edge if installed, else Chrome). A "
                         "browser that is already running with the extension is used as it is")
    ap.add_argument("--record", metavar="FOLDER", help="save a screenshot and the decision of every step, for docs/make_gif.py")
    ap.add_argument("--visual", choices=["auto", "api", "agent", "jev", "off"], default="auto",
                    help="who looks when a step needs eyes: 'api' Claude through the Anthropic API, 'agent' hand the "
                         "screenshot to whoever runs the loop, 'jev' Jev answers yes/no checks on the screenshot (cheap, "
                         "coarse, TypeSafe image input is not production-ready yet), 'auto' (default) the API when "
                         "credentials exist and the agent otherwise, 'off' never look")
    ap.add_argument("--observation", action="append", default=[], metavar="TEXT",
                    help="what someone saw on the screen in an earlier run; Jev takes it into account. Repeatable")
    ap.add_argument("--close-tab", action="store_true", help="close the tab when the run ends")
    ap.add_argument("--hide-overlays", action="store_true",
                    help="hide a dialog that covers the page (without answering it) instead of asking you to deal with it")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--min-conf", type=float, default=MIN_CONF, help="below this the loop stops and hands the decision back")
    ap.add_argument("--dry-run", action="store_true", help="decide one step, act on nothing")
    ap.add_argument("--reload-extension", action="store_true",
                    help="make the browser reload the extension from its folder, after its files were edited")
    args = ap.parse_args()
    if args.setup:
        setup()
    elif args.reload_extension:
        b = Bridge(args.browser)
        try:
            print("extension:", b.call("reload"))
        finally:
            b.close()
    elif not (args.url and args.goal):
        ap.error("url and goal are required unless --setup")
    else:
        run(args)
