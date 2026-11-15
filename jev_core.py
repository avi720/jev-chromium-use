"""The decisions behind the loop: what Jev is asked about a page, and how its answers become one step.

`decide` sends one request with several narrow questions about the page. `plan` turns the answers
into a single step with fixed thresholds, so the same answers always give the same step. The
thresholds below were set from `calibration/run.py`. `commits` is the rule for labels that commit
something, and `describe` is how a control is shown to Jev.
"""

import re

from typesafe_sdk import Choice, Noul, NoulCriteria, TypeSafeClient

BROWSERS = (  # tried in this order when the loop has to start a browser
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
USD_PER_TOKEN = 0.042 / 1_000_000  # Jev input price, docs.typesafe.ai/models (output is not billed)
# Pinned, so a new Jev release cannot change the loop's behavior unnoticed: the thresholds below were
# calibrated on this version. Re-run calibration/run.py before moving to a newer one.
JEV_MODEL = "jev-1.14.0"


def jev_client() -> TypeSafeClient:
    return TypeSafeClient(model=JEV_MODEL)


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
COMMIT_CONF = 0.9    # a click that commits (buy, send, delete...) needs at least this, unless an --approve names that control


def describe(e: dict) -> str:
    """One control as Jev sees it: role, label, and its state when it has one (never a typed value)."""
    s = f"{e['role']}: {e['text']}"
    if e.get("popup"):
        s += f" (opens a list; now: {e.get('value') or 'nothing'})"
    elif e.get("choice"):
        s += f" (now: {e.get('value') or 'nothing'}; options: {' | '.join(e.get('options', []))})"
    elif "checked" in e:
        s += " (checked)" if e["checked"] else " (not checked)"
    elif "selected" in e:
        s += " (selected)" if e["selected"] else " (not selected)"
    elif e.get("field"):
        s += " (filled)" if e.get("filled") else " (empty)"
    if e.get("offscreen"):
        s += f" [{e['offscreen']} the visible area]"
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
        kinds["scroll_down"] = ("No listed control helps, and the needed control or information is likely further down the page. "
                                "Controls marked as above or below the visible area are clickable directly: not this when one "
                                "of them is what `goal` needs.")
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
