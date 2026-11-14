"""Prototype: a browser-driving loop where TypeSafe Jev makes every decision.

    python jev_browser_loop.py --login https://www.google.com        # once: sign in to what you need
    python jev_browser_loop.py https://ksp.co.il/web/ "חפש אוזניות בלוטות'"
    python jev_browser_loop.py https://example.co.il/contact "מלא את טופס יצירת הקשר" --value "שם מלא=..." --value "טלפון=..."

Each step: read the visible DOM over the DevTools protocol (exact text, so Hebrew works; no
OCR), ask Jev which kind of action comes next and on which element, act with real input events.
The loop stops on `done`, on low confidence (handing the decision back with the top
candidates), on a repeated action, or at the step limit.

Profile: Edge on a dedicated profile that persists between runs, so sign-ins made there with
`--login` stay. It is not the everyday Edge profile: Chromium ignores the debugging port on the
default profile (since v136), so that one cannot be driven this way. `--temp-profile` uses a
throwaway profile instead.

Background: runs headless by default, so no window opens and nothing takes focus. `--show`
opens a visible window; `--login` always does.

Typing: Jev does not generate text, it selects. The text typed is chosen from the phrases of
the goal itself (for a search query) and from `--value label=text` pairs (for form fields).
Password, card and ID fields are never typed into. On the persistent profile every typing
action asks for confirmation first, unless `--yes`.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket
from typesafe_sdk import Choice, Noul, NoulCriteria, TypeSafeClient

BROWSERS = (  # Edge first: it is the main browser on this machine
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "jev-loop" / "profile"
USD_PER_TOKEN = 0.042 / 1_000_000  # Jev input price, docs.typesafe.ai/models (output is not billed)

# Collects what a user could click or type into in the viewport right now, plus visible text as
# evidence. What is typed in a field is never read: a field is named by its label only.
PERCEIVE_JS = r"""
(() => {
  document.querySelectorAll('[data-jev]').forEach(e => e.removeAttribute('data-jev'));
  const vw = innerWidth, vh = innerHeight, seen = new Set(), els = [];
  let covered = 0;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  const sel = 'a[href],button,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],input,textarea,[role=combobox],[role=searchbox],[role=textbox]';
  for (const el of document.querySelectorAll(sel)) {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'input' && ['hidden', 'checkbox', 'radio', 'file', 'range', 'color', 'image'].includes(type)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
    const x = Math.min(Math.max(r.left + r.width / 2, 1), vw - 1), y = Math.min(Math.max(r.top + r.height / 2, 1), vh - 1);
    const top = document.elementFromPoint(x, y);
    if (top && top !== el && !el.contains(top) && !top.contains(el)) { covered++; continue; }
    const isButtonInput = tag === 'input' && ['submit', 'button', 'reset'].includes(type);
    const field = (tag === 'input' && !isButtonInput) || tag === 'textarea' || ['searchbox', 'textbox'].includes(el.getAttribute('role'));
    let text;
    if (field) {
      const lab = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
      text = clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || (lab && lab.innerText) ||
                   (el.closest('label') && el.closest('label').innerText) || el.getAttribute('title') || el.getAttribute('name'));
    } else {
      text = clean(isButtonInput ? el.value : (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title')));
    }
    if (!text || seen.has(text)) continue;
    seen.add(text);
    el.setAttribute('data-jev', els.length);
    const item = {role: tag === 'a' ? 'link' : (el.getAttribute('role') || (field ? 'text field' : tag)), text, x: Math.round(x), y: Math.round(y)};
    if (field) {
      const hint = text + ' ' + (el.getAttribute('name') || '') + ' ' + (el.id || ''), ac = el.getAttribute('autocomplete') || '';
      item.field = true;
      item.secret = type === 'password' || /password|cc-|one-time-code/.test(ac) ||
        /סיסמ|password|passcode|אשראי|card.?num|cvv|cvc|תעודת זהות|ת\.ז|מספר זהות|ssn/i.test(hint);
      item.search = type === 'search' || ['searchbox', 'combobox'].includes(el.getAttribute('role')) ||
        /חיפוש|חפש|search/i.test(hint) || !!el.closest('[role=search]');
    }
    els.push(item);
    if (els.length >= 200) break;
  }
  const texts = [];
  // Visible text nodes, grouped by their nearest block parent, in document order.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), blocks = new Map();
  for (let n = walker.nextNode(); n && blocks.size < 60; n = walker.nextNode()) {
    const p = n.parentElement;
    if (!n.nodeValue.trim() || !p || p.closest('script,style,noscript,input,textarea,[contenteditable]')) continue;
    const r = p.getBoundingClientRect();
    if (r.height < 4 || r.width < 4 || r.bottom <= 0 || r.top >= vh || getComputedStyle(p).visibility === 'hidden') continue;
    const block = p.closest('p,li,td,th,h1,h2,h3,h4,address,section,article,div') || p;
    blocks.set(block, ((blocks.get(block) || '') + ' ' + n.nodeValue).replace(/\s+/g, ' ').trim().slice(0, 240));
  }
  for (const t of blocks.values()) if (t.length > 2 && !texts.includes(t)) texts.push(t);
  const doc = document.scrollingElement;
  return JSON.stringify({url: location.href, title: document.title, elements: els, page_text: texts, covered,
                         can_scroll_down: doc.scrollTop + vh < doc.scrollHeight - 20});
})()
"""

# A consent wall or interstitial that covers the page. It is hidden, never answered: the loop
# gives no consent on the user's behalf.
HIDE_OVERLAYS_JS = r"""
(() => {
  const vw = innerWidth, vh = innerHeight, hidden = [];
  for (const [fx, fy] of [[.5, .5], [.25, .25], [.75, .25], [.25, .75], [.75, .75], [.5, .1], [.5, .9]]) {
    let el = document.elementFromPoint(vw * fx, vh * fy), wall = null;
    for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).position === 'fixed' && r.width * r.height > vw * vh * 0.5) wall = el;
    }
    if (wall) { hidden.push(wall.tagName.toLowerCase() + '.' + String(wall.className).slice(0, 40)); wall.style.setProperty('display', 'none', 'important'); }
  }
  return JSON.stringify(hidden);
})()
"""


class Browser:
    def __init__(self, url: str, temp_profile: bool, show: bool = False):
        exe = next((p for p in BROWSERS if Path(p).exists()), None)
        if exe is None:
            raise SystemExit("No Edge or Chrome found")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.temp = temp_profile
        if temp_profile:
            self.profile = tempfile.mkdtemp(prefix="jev-loop-")
        else:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self.profile = str(PROFILE_DIR)
        self.proc = subprocess.Popen(
            [exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             f"--remote-allow-origins=http://127.0.0.1:{self.port}", "--no-first-run", "--no-default-browser-check",
             "--window-size=1440,900", *([] if show else ["--headless=new"]), url],  # headless: no window, no stolen focus
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 25
        page = None
        while time.time() < deadline and page is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                    page = next((t for t in json.load(r) if t["type"] == "page" and t["url"].startswith("http")), None)
            except OSError:
                pass
            if page is None:
                time.sleep(0.3)
        if page is None:
            raise SystemExit("The browser did not expose DevTools. If a window from an earlier run of this profile "
                             "is still open, close it and try again.")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], origin=f"http://127.0.0.1:{self.port}", timeout=30)
        self._id = 0
        if not show:
            # Headless announces itself in the user agent, and some sites serve it a different page.
            ua = self.call("Browser.getVersion")["userAgent"].replace("HeadlessChrome", "Chrome")
            self.call("Network.setUserAgentOverride", userAgent=ua)
            self.call("Page.reload")

    def call(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]

    def perceive(self) -> dict:
        def read() -> dict:
            res = self.call("Runtime.evaluate", expression=PERCEIVE_JS, returnByValue=True)
            return json.loads(res["result"]["value"])

        page = read()
        if page["covered"] > 2 * len(page["elements"]):  # most of the page sits under something
            res = self.call("Runtime.evaluate", expression=HIDE_OVERLAYS_JS, returnByValue=True)
            hidden = json.loads(res["result"]["value"])
            if hidden:
                print(f"  hid blocking overlay without answering it: {', '.join(hidden)}")
                page = read()
        return page

    def settle(self, seconds: float = 2.5) -> None:
        """Wait for the document to finish loading, then a beat for late rendering."""
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                state = self.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)["result"]["value"]
            except RuntimeError:
                state = "loading"  # mid-navigation: the old execution context is gone
            if state == "complete":
                break
            time.sleep(0.3)
        time.sleep(seconds)

    def click(self, x: int, y: int) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def type_into(self, index: int, x: int, y: int, text: str, submit: bool) -> None:
        self.click(x, y)
        time.sleep(0.4)
        self.call("Runtime.evaluate", expression=(
            f"(() => {{ const e = document.querySelector('[data-jev=\"{index}\"]'); "
            "if (e) { e.focus(); if (e.select) e.select(); } })()"))
        self.call("Input.insertText", text=text)  # replaces the selection, so the field ends up holding only `text`
        if submit:
            time.sleep(0.6)
            for kind in ("keyDown", "keyUp"):
                self.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter",
                          windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))

    def scroll_down(self) -> None:
        self.call("Runtime.evaluate", expression="scrollBy(0, innerHeight * 0.8)")

    def close(self) -> None:
        try:
            self.call("Browser.close")
        except Exception:
            self.proc.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if self.temp:
            shutil.rmtree(self.profile, ignore_errors=True)


def text_candidates(goal: str, values: dict[str, str]) -> list[str]:
    """Everything the loop may type: phrases of the goal, and the values the user supplied."""
    words = [w.strip(".,;:!?()[]") for w in goal.split()]
    words = [w for w in words if w]
    phrases = list(values.values())
    for n in range(1, 7):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases[:250]


# Labels that commit something beyond viewing pages: money, an order, a message, a deletion, consent, an
# account change. Deterministic on purpose: this is a rule, not a judgment. Hebrew is matched at the start of a
# word (after an optional ו/ל prefix) and, where a longer word means something else, to the end of the word:
# "מחק" but not "מחקר", "בצע" but not "מבצעים", "פרסום" but not "פרסומת". calibration/commit_words.py tests it.
_HE = (
    r"קנ[הו]E|קנייה מהירה|קני[יה]הE(?= עכשיו)|רכוש|רכיש[הת]|הזמןE|הזמינו|הזמנ[הת]E|בצעו?E|ביצוע|תשלוםE(?! ב)|תשלום מאובטח|שלםE|שלמוE|קופהE|חייבE"
    r"|שלחו?E|שליח[הת]E|הגשE|הגישו|הגש[הת]E|מחקו?E|מחיק[הת]E|הסרE|הסר[הת]E|בטלו?E|ביטול|אשרו?E|אישורE|מאשרת?E|מסכיםE|מסכימהE|הסכמהE"
    r"|קבל הכו?ל|קבל את כל|פרסםE|פרסמוE|פרסוםE|הגבE|שתףE|שתפוE|שיתוףE|העלהE|העלא[הת]E|שמורE|שמיר[הת]E|עדכןE|עדכנוE|עדכון פרטים|החלE"
    r"|העברE|העבר[הת]E|משיכהE|משוךE|הפקדE|הפקדהE|המרה?E|הירשםE|הירשמוE|הרשםE|הרשמהE|הצטרףE|הצטרפוE|הצטרפותE|צור חשבון|יצירת חשבון|פתח חשבון"
    r"|התנתקו?E|התנתקותE|יציאהE|חתו?םE|חתימהE|תרו?םE|תרמוE|תרומהE|הצביעו|הצבעה?E|דווחו?E|דיווחE|חסו?םE|חסימהE|עקובE"
    r"|קבעו? תור|קביעת תור|זימון תור|שרייןE|שריינוE|חדשו? מנוי|חידוש מנוי|שדרגו?E|שדרוגE|הורדו?E|הורד[הת]E|סיוםE|סייםE|סיימוE"
    r"|אפס סיסמ|איפוס סיסמ|שנה סיסמ|שינוי סיסמ"
).replace("E", r"(?![א-ת])")
COMMIT = re.compile(
    r"\b(buy|pay|purchase|checkout|order now|place order|complete order|cancel order|send|submit|apply|delete|remove|cancel subscription"
    r"|confirm|i agree|agree|accept|publish|post|reply|comment|share|upload|save|update|transfer|withdraw|deposit|sign up|register|join"
    r"|create account|sign out|log ?out|subscribe|unsubscribe|donate|vote|report|block|follow|book|reserve|upgrade|renew|finish"
    r"|reset password|change password)\b"
    rf"|(?<![א-ת])לקני?יה(?![א-ת])|(?<![א-ת])[ול]?(?:{_HE})",
    re.I,
)


def commits(label: str) -> bool:
    """Does this control's label read like a commitment? A long label (a headline, a product card) is judged by
    its first three words: a verb deep inside a sentence is not what the control does."""
    return bool(COMMIT.search(label if len(label) <= 45 else " ".join(label.split()[:3])))

# Decision thresholds. Set from calibration/run.py on 141 labeled Hebrew cases; see calibration/README.md.
MIN_CONF = 0.6       # element Choice confidence below this: stop and hand the decision back
DONE_AT = 0.8        # goal_reached at or above this: the goal is met
FITS_AT = 0.5        # fits below this: the goal has nothing to do with this page, stop
SEARCH_AT = 0.7      # wants_search at or above this: type the query into a visible search box...
SEARCH_UNLESS = 0.9  # ...unless a click is at least this confident (a direct link beats searching)
VISUAL_AT = 0.7      # needs_visual at or above this: hand the screen to Claude (once per page state)


def describe(e: dict) -> str:
    """One control as Jev sees it: role, label, and its state when it has one (never a typed value)."""
    s = f"{e['role']}: {e['text']}"
    if e.get("choice"):
        s += f" (now: {e.get('value') or 'nothing'}; options: {' | '.join(e.get('options', []))})"
    elif "checked" in e:
        s += " (checked)" if e["checked"] else " (not checked)"
    elif "selected" in e:
        s += " (selected)" if e["selected"] else " (not selected)"
    elif e.get("field"):
        s += " (filled)" if e.get("filled") else " (empty)"
    return s


def plan(resp, page: dict, already_typed: bool, min_conf: float = MIN_CONF, already_looked: bool = False) -> dict:
    """Turn Jev's answers into one step. Pure: the same answers always give the same step.

    Returns {"do": "done" | "handoff" | "type" | "scroll" | "click", "index": element index or None, "why": str}.
    """
    els, answers = page["elements"], resp.answers
    kind, el = answers["kind"], answers.get("element")
    if "needs_visual" in answers and answers["needs_visual"].noul >= VISUAL_AT and not already_looked:
        return {"do": "look", "index": None, "why": f"needs eyes (needs_visual={answers['needs_visual'].noul:.2f})"}
    if kind.choice == "done" or answers["goal_reached"].noul >= DONE_AT:
        return {"do": "done", "index": None, "why": f"goal_reached={answers['goal_reached'].noul:.2f}"}
    if "fits" in answers and answers["fits"].noul < FITS_AT:
        return {"do": "handoff", "index": None, "why": f"the goal does not seem to belong on this page (fits={answers['fits'].noul:.2f})"}
    target_i = int(el.choice[1:]) if el else None
    target = els[target_i] if el else None
    open_fields = [i for i, e in enumerate(els) if e.get("field") and not e["secret"]]
    search_boxes = [i for i in open_fields if els[i]["search"]]
    p_scroll, p_type = kind.probabilities.get("scroll_down", 0.0), kind.probabilities.get("type_text", 0.0)
    if el is not None and el.confidence >= min_conf and target.get("choice"):
        return {"do": "choose", "index": target_i, "why": f"dropdown, confidence {el.confidence:.2f}"}
    clicks_field = el is not None and el.confidence >= min_conf and bool(target.get("field"))
    if clicks_field and target["secret"]:
        return {"do": "handoff", "index": target_i, "why": f"'{target['text']}' is a credential field, yours to fill"}
    use_search = (bool(search_boxes) and not already_typed and answers["wants_search"].noul >= SEARCH_AT
                  and (el is None or el.confidence < SEARCH_UNLESS or bool(target.get("field"))))
    if open_fields and (clicks_field or use_search or (kind.choice == "type_text" and p_type >= 0.5)):
        if clicks_field:
            field_i = target_i
        elif use_search:
            field_i = search_boxes[0]
        else:
            field_i = int(answers["field"].choice[1:]) if "field" in answers else open_fields[0]
        if els[field_i].get("choice"):
            return {"do": "choose", "index": field_i, "why": "dropdown"}
        return {"do": "type", "index": field_i, "why": "search" if use_search else "text field"}
    if el is None or (el.confidence < min_conf and p_scroll >= 0.3) or p_scroll >= 0.8:
        if page["can_scroll_down"]:
            return {"do": "scroll", "index": None, "why": f"p_scroll={p_scroll:.2f}"}
        return {"do": "handoff", "index": None, "why": "nothing on screen fits"}
    if el.confidence < min_conf:
        return {"do": "handoff", "index": target_i, "why": f"element confidence {el.confidence:.2f} below {min_conf}"}
    return {"do": "click", "index": target_i, "why": f"confidence {el.confidence:.2f}"}


def decide(client: TypeSafeClient, goal: str, page: dict, history: list[str], can_type: bool):
    state = {
        "goal": goal,
        "page": {"url": page["url"], "title": page["title"], "visible_text": page["page_text"]},
        "on_screen_controls": [describe(e) for e in page["elements"]],
        "actions_already_taken": history or ["none yet"],
    }
    kinds = {
        "click": "An on-screen link, button or other control should be clicked to move toward `goal`.",
        "done": "`page` already shows what `goal` asks for: the requested page is open or the requested information is "
                "visible in `page.visible_text`. Nothing more needs to be clicked.",
    }
    if can_type:
        kinds["type_text"] = ("A text field on screen should be filled now. This is the step whenever `goal` asks to search or "
                              "look something up and a search box is on screen: typing the query beats browsing categories. "
                              "Also when a form field needs its value. Not this when `actions_already_taken` shows the field was just filled.")
        kinds["click"] += " Not this when `goal` asks to search and the search box has not been used yet."
    if page["can_scroll_down"]:
        kinds["scroll_down"] = "No on-screen element helps, and the needed element or information is likely further down the page."
    questions = {
        "kind": Choice(
            instructions="A user is on the web page `page` and wants `goal`. `actions_already_taken` lists the steps so far. "
                         "What kind of step comes next?",
            criteria=kinds,
        ),
        # Jev reads controls and text, not pixels. This says when a goal needs someone to look at the screen.
        "needs_visual": Noul(
            instructions="Does `goal` need someone to look at how this page appears on screen: layout, colors, readability, "
                         "contrast, overlapping or cut-off elements, whether images or charts render correctly?",
            criteria=NoulCriteria(
                true="Answering `goal` depends on the page's appearance, which `on_screen_controls` and `page.visible_text` "
                     "cannot show.",
                false="`goal` is about navigating, clicking, typing, or information that the page's text and controls "
                      "already carry.",
            ),
        ),
        # Asked on its own: whether the goal is met is a different judgment from what to do next.
        "goal_reached": Noul(
            instructions="Is the user's `goal` already satisfied by the page that is open now, judging by `page.url`, "
                         "`page.title` and `page.visible_text`?",
            criteria=NoulCriteria(
                true="The page the goal asks to open is the one open now, or the information the goal asks for is shown "
                     "in `page.visible_text`.",
                false="A further click, typed text or scroll is still needed to get there.",
            ),
        ),
    }
    if page["elements"]:
        # A Choice always picks something. This says whether anything deserves to be picked: without it a goal
        # that does not belong on the page ends in a confident click on "home" or "search".
        questions["fits"] = Noul(
            instructions="Can the user's `goal` be pursued on this website? Judge by `page` and `on_screen_controls`.",
            criteria=NoulCriteria(
                true="The site in `page` is a sensible place for `goal`: one of `on_screen_controls` leads toward it, "
                     "or the site's own search could plausibly find it.",
                false="`goal` has nothing to do with this site or page: no control leads toward it, and searching this "
                      "site for it would be pointless.",
            ),
        )
        questions["element"] = Choice(
            instructions="A user is on the web page `page` and wants `goal`. Assuming the next step is a click, "
                         "which single on-screen element should be clicked to make progress toward `goal`?",
            criteria={f"e{i}": describe(e) for i, e in enumerate(page["elements"])},
        )
    if can_type:
        # A narrow fact about the goal alone, so a visible search box is used instead of browsing around it.
        questions["wants_search"] = Noul(
            instructions="Does `goal` ask to search for or look up something by a query, so that typing the query into a "
                         "site's search box is the direct way to do it, and `actions_already_taken` shows no query typed yet?",
        )
    fields = {f"e{i}": describe(e) for i, e in enumerate(page["elements"])
              if (e.get("field") and not e["secret"]) or e.get("choice")}
    if can_type and len(fields) > 1:
        questions["field"] = Choice(
            instructions="A user is on the web page `page` and wants `goal`. Assuming the next step is typing text, "
                         "which field should be filled or set next? Skip fields already filled or already set to what `goal` needs.",
            criteria=fields,
        )
    return client.system_one(state=state, questions=questions)


def choose_text(client: TypeSafeClient, goal: str, page: dict, field: dict, candidates: list[str], values: dict[str, str]):
    """A second request: the text depends on which field was chosen."""
    by_value = {v: k for k, v in values.items()}
    criteria = {f"t{i}": (f"{by_value[c]}: {c}" if c in by_value else c) for i, c in enumerate(candidates)}
    return client.system_one(
        state={"goal": goal, "page": {"url": page["url"], "title": page["title"]},
               "field": {"label": field["text"], "is_search_box": field["search"]}},
        questions={"text": Choice(
            instructions="The user wants `goal` and is about to type into the text field `field` on `page`. Which candidate is "
                         "exactly the text to type there? For a search box pick the search query itself: the complete name of "
                         "the thing being looked for, without command words such as 'search for' and without the site's name. "
                         "For a form field pick the value that belongs in a field with that label.",
            criteria=criteria,
        )},
    )


def choose_option(client: TypeSafeClient, goal: str, page: dict, field: dict, values: dict[str, str]):
    """A second request: which option of a dropdown the goal and the user's values call for."""
    criteria = {f"o{i}": opt for i, opt in enumerate(field["options"])}
    return client.system_one(
        state={"goal": goal, "page": {"url": page["url"], "title": page["title"]},
               "dropdown": {"label": field["text"], "now": field.get("value")},
               "values_from_the_user": values or "none given"},
        questions={"option": Choice(
            instructions="The user wants `goal` and is setting the dropdown `dropdown` on `page`. Which option should it be "
                         "set to? A value the user gave in `values_from_the_user` for this label wins; otherwise pick what "
                         "`goal` implies. If neither says anything about this dropdown, keep `dropdown.now`.",
            criteria=criteria,
        )},
    )


def confirm(ask: bool, what: str) -> bool:
    if not ask:
        return True
    return input(f"  {what}\n  proceed? [y/N] ").strip().lower() in ("y", "yes", "כ", "כן")


def run(a: argparse.Namespace) -> str:
    values = dict(v.split("=", 1) for v in a.value)
    candidates = text_candidates(a.goal, values)
    ask = not (a.yes or a.temp_profile)
    browser = Browser(a.url, a.temp_profile, show=a.show or a.keep_open)
    history: list[str] = []
    outcome, tokens = "step limit", 0
    try:
        browser.settle()
        with TypeSafeClient() as client:
            for step in range(1, a.steps + 1):
                page = browser.perceive()
                els = page["elements"]
                typed = {h for h in history if h.startswith("typed")}
                t0 = time.perf_counter()
                resp = decide(client, a.goal, page, history, can_type=any(e.get("field") and not e["secret"] for e in els))
                ms = (time.perf_counter() - t0) * 1000
                tokens += resp.usage.input_tokens
                step_plan = plan(resp, page, already_typed=bool(typed), min_conf=a.min_conf)
                do, target = step_plan["do"], (els[step_plan["index"]] if step_plan["index"] is not None else None)
                nouls = {k: round(v.noul, 2) for k, v in resp.answers.items() if hasattr(v, "noul")}
                print(f"\nstep {step}  {page['url'][:90]}\n  {len(els)} elements, decision {ms:.0f}ms, {resp.usage.input_tokens} tokens, {nouls}"
                      f"\n  -> {do}" + (f" {target['role']}: {target['text'][:70]!r}" if target else "") + f"  ({step_plan['why']})")
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
                    browser.scroll_down()
                    time.sleep(1.0)
                    continue
                if do == "type":
                    tr = choose_text(client, a.goal, page, target, candidates, values)
                    tokens += tr.usage.input_tokens
                    ans = tr.answers["text"]
                    text = candidates[int(ans.choice[1:])]
                    print(f"  text: {text!r} (conf {ans.confidence:.2f})")
                    if not target["search"] and ans.confidence < a.min_conf:
                        outcome = f"handoff: unsure what belongs in '{target['text'][:40]}' (confidence {ans.confidence:.2f})"
                        break
                    action = f"typed '{text}' into field '{target['text'][:60]}' on {page['url']}"
                    if action in typed:
                        outcome = "stall: the same text was chosen for the same field again"
                        break
                    if not confirm(ask, f"about to type {text!r} into {target['text'][:60]!r}" + (" and press Enter" if target["search"] else "")):
                        outcome = "stopped: typing was declined"
                        break
                    history.append(action + (" and pressed Enter" if target["search"] else ""))
                    browser.type_into(step_plan["index"], target["x"], target["y"], text, submit=target["search"])
                    browser.settle()
                    continue
                action = f"clicked {target['role']} '{target['text'][:80]}' on {page['url']}"
                if action in history:
                    outcome = "stall: the same click was chosen again"
                    break
                if commits(target["text"]) and not confirm(ask, f"'{target['text'][:80]}' looks like it commits something"):
                    outcome = f"stopped: '{target['text'][:60]}' looks like a commitment (buy, send, delete...), yours to click"
                    break
                history.append(action)
                browser.click(target["x"], target["y"])
                browser.settle()
        final = browser.perceive()
        print(f"\noutcome: {outcome}\nfinal page: {final['title']}\n            {final['url']}"
              f"\ncost: {tokens} input tokens = ${tokens * USD_PER_TOKEN:.5f}")
        if a.keep_open:
            input("Browser left open. Press Enter to close it...")
    finally:
        browser.close()
    return outcome


def login(url: str) -> None:
    browser = Browser(url, temp_profile=False, show=True)
    try:
        input(f"The loop's own browser profile is open ({PROFILE_DIR}).\nSign in there to whatever the loop should "
              "reach, answer any cookie prompts, then press Enter here to save and close...")
    finally:
        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("goal", nargs="?")
    ap.add_argument("--login", action="store_true", help="open the persistent profile so you can sign in, then exit")
    ap.add_argument("--temp-profile", action="store_true", help="throwaway profile: no sign-ins, deleted afterwards")
    ap.add_argument("--value", action="append", default=[], metavar="LABEL=TEXT", help="a value the loop may type into a form field")
    ap.add_argument("--yes", action="store_true", help="do not ask before typing")
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--min-conf", type=float, default=MIN_CONF, help="below this the loop stops and hands the decision back")
    ap.add_argument("--dry-run", action="store_true", help="decide one step, click and type nothing")
    ap.add_argument("--show", action="store_true", help="show the browser window (default: headless, in the background)")
    ap.add_argument("--keep-open", action="store_true", help="show the window and wait for Enter before closing it")
    args = ap.parse_args()
    if args.login:
        login(args.url)
    elif not args.goal:
        ap.error("a goal is required unless --login")
    else:
        run(args)
