# jev-chromium-use

**Browser automation where a small decision model, not a frontier LLM, picks every step.**

You give a goal in plain language (Hebrew works). The loop reads the page's DOM, asks
[TypeSafe](https://docs.typesafe.ai)'s Jev which control to click or what to type, and acts, in
**your own signed-in Edge**, in a background tab inside a labeled tab group, without opening a
window or taking focus. A whole task costs a fraction of a cent; a decision takes about a third of
a second.

```text
> python jev_ext_loop.py https://www.ynet.co.il "חפש באתר כתבות על רפאל"

step 1  https://www.ynet.co.il/home/0,7340,L-8,00.html
  26 elements, decision 474ms, 2595 tokens, {'goal_reached': 0.04, 'fits': 0.98}
  -> click button: 'חיפוש'  (confidence 1.00)
step 2  ...
  -> type text field: 'חיפוש'  (search)
  text: 'רפאל' (conf 0.69)
step 3  ...
  -> done  (goal_reached=0.93)
cost: Jev 9363 input tokens = $0.00039
```

<div dir="rtl">

**בעברית, בקצרה:** סוכן דפדפן שבו מודל החלטות קטן וזול (Jev של TypeSafe) בוחר כל צעד במקום
מודל שפה גדול. הוא עובד בתוך ה-Edge שלך עם החשבונות המחוברים, בכרטיסיית רקע, ומבין אתרים
בעברית. כשצריך להסתכל על המסך, הוא מעביר צילום ל-Claude או לסוכן שמריץ אותו.

</div>

> **Status: prototype.** Built and tested on Windows 11 (ARM64) with Edge 157. It has been
> calibrated on 153 labeled cases and used for one QA pass on a signed-in web app (navigation,
> sorting, form entry). That is all the real mileage it has. Read [Risks](#risks) before pointing
> it at a site where you are logged in.

**Contents:** [Quick start](#quick-start) · [How it works](#how-it-works) ·
[Usage](#usage) · [Safety rules](#safety-rules) · [Calibration](#calibration) · [Risks](#risks) ·
[Cost](#cost) · [Compared with a screenshot LLM](#compared-with-an-llm-that-reads-screenshots) · [Known limits](#known-limits)

## Quick start

```bash
git clone https://github.com/avi720/jev-chromium-use
cd jev-chromium-use
pip install -r requirements.txt
python jev_ext_loop.py --setup        # writes a token, prints the extension install steps
```

Load the `jev-edge-extension` folder in `edge://extensions` (Developer mode, then **Load
unpacked**), set `TYPESAFE_API_KEY`, and run a goal:

```bash
python jev_ext_loop.py https://www.ynet.co.il "פתח את מדור הכלכלה"
```

## How it works

```text
jev_ext_loop.py  <── WebSocket on 127.0.0.1 ──>  Jev Loop Bridge (Edge extension)
      │                                                   │
      │  one request per step                             │  acts only in the tab it opened
      ▼                                                   ▼
  TypeSafe Jev                                   background tab, in a tab group
```

Each step:

1. **Perceive.** The extension lists what a user could act on in the tab's viewport, with exact
   labels from the DOM, plus the visible text. No screenshots and no OCR, so right-to-left text and
   Hebrew arrive intact. Each control comes with its state: a dropdown with its options and the one
   chosen now, a checkbox or radio as checked or not, a tab or segmented button as selected or not,
   a text field as empty or filled. What is typed in a field is never read, only whether it holds
   something.
2. **Decide.** One request to Jev asks several narrow questions about the same page at once:
   | question | type | what it settles |
   |---|---|---|
   | `kind` | Choice | click, type, scroll or done |
   | `element` | Choice | which control, out of everything on screen |
   | `goal_reached` | Noul | is the goal already met |
   | `fits` | Noul | does the goal belong on this page at all |
   | `wants_search` | Noul | is the goal a search by query |
   | `needs_visual` | Noul | does the goal need someone to look at the screen |
   | `field` | Choice | which field or dropdown to fill next |
3. **Plan.** Plain code (`plan()` in `jev_browser_loop.py`) turns those answers into one step,
   using fixed thresholds. Same answers, same step.
4. **Act**, wait for the page, repeat. The loop ends on `done`, on a hand-off, on a repeated
   action, or at the step limit.

Jev never writes text. When something must be typed, it **selects** it: from the phrases of your
goal (a search query) or from values you pass with `--value` (form fields). A dropdown is set the
same way: Jev picks one of its options.

### Values you give are placed by code

`--value "LABEL=TEXT"` is a fact, not a suggestion. Before Jev is asked anything, code finds the
field or dropdown whose label matches `LABEL` (ignoring `*` and `:`) and puts `TEXT` there, even
when the field already shows a default, such as today's date or a dropdown on its first option.
A control that looks like it submits (see [Safety rules](#safety-rules)) is not clicked while any
of your values has not been placed. This rule exists because an earlier version let Jev decide
which fields to fill: it skipped two dropdowns and two prefilled date fields and submitted a trade
with the wrong direction and date.

### When a step needs eyes: two ways to look

Jev reads controls and text, so it cannot tell whether a chart renders, text overflows its box, or
colors are readable. When `needs_visual` is high, the loop takes a screenshot of its tab and hands
the look to someone who can see. There are two ways, chosen with `--visual`:

| `--visual` | who looks | needs |
|---|---|---|
| `agent` | whoever runs the loop: a coding agent such as Claude Code, or you | nothing extra |
| `api` | Claude through the Anthropic API (`claude-opus-5-5`, see `visual.py`) | `ANTHROPIC_API_KEY` and API credits |
| `auto` (default) | the API when credentials exist, otherwise the agent | |
| `off` | nobody; the loop never asks for a look | |

Either way the screenshot is taken through the extension's `debugger` permission, so the tab stays
in the background, even when Edge's window is covered by other windows. Edge shows a "started
debugging this browser" bar for the second it takes.

**Agent mode: the loop and the agent take turns.** This is the chain to use if you run the loop
from Claude Code (or a similar agent) and have no API credits: the agent's own model does the
looking, inside the subscription you already pay for.

1. The loop saves the screenshot under `%TEMP%\jev-loop\screenshots\` and stops with
   `handoff to the agent: this step needs eyes. Look at the screenshot: <path>`.
2. The agent opens the image and writes what it sees, in a sentence or three.
3. The agent runs the same command again with `--observation "<what it saw>"`. Jev reads the
   observation as part of the page's state and carries on: done, or the next click.

```bash
python jev_ext_loop.py https://example.app "check that the dashboard cards are readable"
# ... handoff to the agent: ... Look at the screenshot: C:\...\jev-loop\screenshots\20261114-163342-step1.jpg
python jev_ext_loop.py https://example.app "check that the dashboard cards are readable" \
    --observation "All ten cards are readable; one card's numbers are smaller than the rest."
# -> done
```

To have Claude Code follow this without being told each time, add a line like this to your
`CLAUDE.md`: *"When jev_ext_loop.py stops with 'handoff to the agent', open the screenshot it
names, describe what you see, and run the same command again with `--observation`."*

**API mode: one command, no turns.** With `ANTHROPIC_API_KEY` set (or `--visual api`), the loop
sends the screenshot to Claude itself. Claude returns a summary, a list of visual issues with
severity, and whether the goal is met; the loop prints them, adds them to its history, and Jev
keeps choosing the actions. Each page state is looked at once. A look costs about $0.01 to $0.02
(one image plus a short JSON answer at Opus prices), roughly a hundred Jev steps. To use another
model, change `MODEL` in `visual.py`.

## Requirements

- Windows with Microsoft Edge (Chrome should work with small path changes; untested)
- Python 3.12+
- A TypeSafe API key in the `TYPESAFE_API_KEY` environment variable
- Optional, for the visual hand-off: an Anthropic API key in `ANTHROPIC_API_KEY`

```bash
pip install -r requirements.txt
```

## Install

```bash
python jev_ext_loop.py --setup
```

This writes a random token to `jev-edge-extension/token.js` and prints the steps:

1. Open `edge://extensions`
2. Turn on **Developer mode**
3. Click **Load unpacked** and choose the `jev-edge-extension` folder

Keep the folder where it is: Edge loads the extension from it on every start.

## Usage

```bash
python jev_ext_loop.py <url> "<goal>" [options]
```

| option | effect |
|---|---|
| `--steps N` | step limit (default 8) |
| `--yes` | do not ask before typing. Commitment-looking clicks still ask |
| `--value "LABEL=TEXT"` | a value for the field or dropdown labeled `LABEL`; placed by code, repeatable |
| `--dry-run` | decide the first step and act on nothing |
| `--close-tab` | close the tab at the end (default: leave it open in its group) |
| `--show-page` | print the controls and text of the final page, for a report of what ended up on screen |
| `--allow-commits` | click commitment-looking controls without asking. Only for a test account |
| `--reload-extension` | make Edge reload the extension after its files were edited (no url or goal needed) |
| `--hide-overlays` | hide a dialog that covers the page instead of asking you (see below) |
| `--min-conf X` | element confidence below which the loop hands off (default 0.6) |
| `--visual MODE` | who looks when a step needs eyes: `auto`, `agent`, `api` or `off` (see above) |
| `--observation TEXT` | what someone saw on the screen in an earlier run; repeatable |

If Edge is not running, the loop starts it normally, so it opens with your own tabs. The
extension looks for the loop every 30 seconds, so the first connection can take that long.

### When the loop stops and hands off

A hand-off is the loop saying "this one is yours". It happens when:

- no control is chosen with enough confidence (it prints the top candidates),
- the goal does not seem to belong on the page (`fits` is low),
- the chosen control reads like a commitment (see [Safety rules](#safety-rules)),
- the target is a password, card or ID field,
- a dialog covers the page,
- a step needs eyes in agent mode (the screenshot path is printed; see above),
- one of your `--value` labels matches no field, or its value is not one of the dropdown's options.

### Cookie and consent dialogs

On a signed-in profile a consent wall is rare: it shows up on a site you have not visited, or
after its cookies were cleared. Answering it is a decision about your consent, so by default the
loop does not touch it. It brings the tab to the front and waits for you to answer the dialog,
then continues. With no terminal to ask in, it hands off.

If you would rather not be interrupted, `--hide-overlays` hides the covering element without
clicking anything in it. No consent is given, but the heuristic is crude (any fixed element
covering more than half the viewport) and can hide a legitimate dialog. To make hiding the
default, or to change the detection, see `Tab.perceive` in `jev_ext_loop.py` and `hideOverlays`
in `jev-edge-extension/background.js`.

## Safety rules

These are code, not model judgment:

- **Only its own tab.** The extension refuses any command for a tab it did not open.
- **Commitment labels always ask.** A control whose label reads like buy, pay, order, send,
  submit, delete, cancel, confirm, agree, publish, save, sign up, sign out and so on (English and
  Hebrew) is never clicked without a yes from you, even with `--yes`. The rule is the `COMMIT`
  pattern in `jev_browser_loop.py`.
- **No credentials.** Password, card-number, CVV and ID fields are never typed into.
- **Typing asks first**, unless `--yes`.
- **Only chosen text is typed.** The loop can type phrases from your goal or your `--value`
  pairs, nothing else. A page cannot make it type text of its own.
- **Your values before any submit.** A commitment-looking control is not clicked while a
  `--value` you gave has not been placed.
- **The bridge needs the token.** The extension acts only for a local server that presents the
  token from `token.js`; the server accepts only a connection from a browser extension.

## Calibration

`calibration/` holds the evidence for the thresholds: 17 saved Hebrew pages (news, stores,
government, health, university, Wikipedia) and 153 goals with known answers.

```bash
python calibration/collect.py     # save pages (throwaway headless profile)
python calibration/run.py         # ask Jev once per case and print the report
python calibration/run.py --report
python calibration/commit_words.py [--jev]
```

Result with the thresholds in use (`MIN_CONF=0.6`, `DONE_AT=0.8`, `FITS_AT=0.5`,
`SEARCH_AT=0.7`, `SEARCH_UNLESS=0.9`, `VISUAL_AT=0.7`), latest run:

| kind of case | n | right | stopped | not right |
|---|---|---|---|---|
| click a control | 118 | 110 | 0 | 8 |
| type a search | 8 | 8 | 0 | 0 |
| goal already met | 7 | 7 | 0 | 0 |
| nothing on the page fits | 8 | – | 8 | 0 |
| needs eyes (visual) | 12 | 12 | 0 | 0 |

- Of the 8 "not right" click cases, 7 took another reasonable route: in 4 the loop typed the goal
  into the site's search box where a direct link existed, and in 3 it declared the goal met on a
  page that arguably already showed the answer (a featured article on the home page it was asked
  to find).
- **One wrong click**: "enter my personal area" on the National Insurance site picked "order a code
  for the personal site" at confidence 0.61. In the previous run the same case stopped at 0.53, so
  it sits on the line and moves between runs. The commitment rule flags that label ("הזמנת"), so
  the loop would have asked before clicking it.
- `needs_visual` separated cleanly: the 12 visual goals scored 0.84 to 0.98, and none of the other
  141 scored as high as 0.3.
- Between 0.3 and 0.6, `MIN_CONF` barely matters. Raising it to 0.9 stops 22 cases instead of 8
  and prevents one wrong step, so confidence alone is a weak guard.
- What mattered was the `fits` question. Before it, a goal that had nothing to do with the page
  ("order a pizza" on a news site) got a confident click on "search" or "home", up to 0.95. With
  it, all 8 such cases stop.

**Commitment wording.** The first version of the rule caught 85 of 202 commitment labels and
flagged look-alikes ("מבצעים" for "בצע", "מחקר" for "מחק"). The current rule catches 202 of 202
on that list and leaves 60 of 61 benign labels alone; on 534 real labels from the saved pages it
flags 19.

Read these numbers with their limits:

- The thresholds and the `fits` question were tuned on the same cases they are reported on.
  There is no held-out set, so expect somewhat worse on new sites.
- One live run of a "nothing fits" goal scored `fits=0.44`, close to the 0.5 line.
- The pages are signed-out home and listing pages. Carts, checkouts, inboxes and settings pages
  are not in the set.
- Each case is a single decision. Multi-step runs were tested on a handful of goals only.
- Dropdowns, checkboxes, row context and the `--value` rule are not in the calibration set; they
  were tested on one real app. The agent mode of the visual hand-off was tested end to end; API
  mode was tested up to the screenshot, since no API key was available.
- The word lists for the commitment rule were written by hand, by the same author as the rule.

## Risks

1. **A wrong click on a site where you are signed in.** This is the real one. A mistake there
   can send, delete or buy. The guards above reduce it and do not remove it: the commitment
   rule reads labels, so an icon-only button or an unusual wording gets past it. Use the loop
   for navigation, search and reading; do not give it goals that end in an irreversible action.
2. **Page content goes to TypeSafe, and screenshots to Anthropic.** Every step sends the URL,
   title, up to 60 blocks of visible text and the control labels of the tab to TypeSafe. A step
   that needs eyes also sends a screenshot of the tab to Anthropic, and a screenshot shows what
   is in the fields too. Point it at your inbox or your bank and that is what is sent.
3. **A hostile page can try to steer it.** Page text is part of what Jev judges, so a page can
   bias which control gets clicked. The damage is bounded: it can only click what is on that
   page and type only your own phrases.
4. **The extension is broad.** It has access to all sites and the `debugger` permission (for
   screenshots of a background tab), limited by its own code to the tabs it opened and gated by
   the token. Malware already running as you can get around that, and could take your browser
   cookies without it.
5. **Annoyances.** Edge warns about developer-mode extensions. Typing is simulated with DOM
   events, and some sites will not react to it. `target="_blank"` links are forced to open in
   the same tab.

## Cost

Jev is billed at $0.042 per million input tokens; output is not billed. A step is 2,000 to 4,500
input tokens, so about $0.0001. Navigation runs cost $0.0002 to $0.0005; filling an eight-field
form with your values cost $0.0003, because code places the values and Jev is asked only about the
submit. A look by Claude costs about $0.01 to $0.02, roughly a hundred Jev steps.

### Compared with an LLM that reads screenshots

The usual way to build this is a loop where a large model gets a screenshot at every step and
answers with the next action. Here is what the same two tasks would cost that way. **The Jev
column is measured** (the runs above). **The other columns are estimates**: no API key was
available to measure them, so they come from list prices and the assumptions below the table.

| task | decisions | Jev (measured) | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 |
|---|---|---|---|---|---|
| search ynet for articles about Rafael | 3 | **$0.0004** | $0.0015–0.002 (4–6×) | $0.03–0.04 (80–110×) | $0.06–0.09 (160–220×) |
| fill a 6-field form and submit, Jev choosing every field | 8 | **$0.0019** | $0.004–0.01 (2–5×) | $0.09–0.20 (47–107×) | $0.18–0.40 (95–214×) |
| one decision | 1 | **~$0.0001** | ~$0.0005 | ~$0.01 | ~$0.02 |

Each range runs from a loop that sends only the latest screenshot to one that keeps every earlier
screenshot in the conversation, which is how a simple loop is usually written.

Assumptions, per step of the screenshot loop:

- **Screenshot:** about 2,000 input tokens. The QA screenshots were 1428 × 1052 px, and image
  tokens are roughly width × height / 750.
- **Fixed prompt:** about 1,500 tokens (system prompt, the tool definition, the goal).
- **History:** about 150 tokens of text per earlier step.
- **Output:** about 300 tokens per step (the action, plus thinking, which cannot be turned off on
  Opus 5.5).
- **Same number of decisions as Jev.** That is generous to the LLM: a screenshot agent usually
  needs a click and then a separate type action for each field.
- **No prompt caching.** Caching the fixed prompt would cut the LLM columns by perhaps a fifth;
  the screenshots, which change every step, are most of the cost.
- **Prices:** Anthropic list prices per million tokens, input / output: Opus 5.5 $4 / $20,
  Sonnet 5.5 $2 / $10, Haiku 5.5 $0.10 / $0.50. Jev: $0.042 per million input tokens, output
  free.

What the table says, and what it doesn't:

- **Against Opus and Sonnet, Jev is about 50 to 200 times cheaper.** That is the comparison the
  approach was built for.
- **Against Haiku 5.5 the gap is small, 2 to 6 times.** Haiku is cheap enough that cost alone is
  not a strong reason to choose Jev over it. What Jev gives on top is a calibrated probability for
  every option, which is what the thresholds and hand-offs in this project are built on, and it
  reads the page's real text instead of pixels. Whether Haiku would pick the right control on
  these Hebrew pages as often as Jev did was not tested.
- **Speed:** a Jev decision took 200 to 500 ms here. A screenshot model call usually takes
  several seconds; [typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use)
  measured 5.2 s for Claude Opus 5 against 0.13 to 0.38 s for Jev on the same screenshot.
- **Steps that need eyes cost the same either way.** When `needs_visual` fires, this project
  also sends a screenshot to Claude (or hands it to the agent running the loop).
- **The $0.0003 form run is left out on purpose.** There, code placed your `--value` entries and
  Jev decided only the submit. A screenshot loop could place known values with code too, so that
  saving belongs to the design, not to Jev.

## Repository layout

| path | what |
|---|---|
| `jev_ext_loop.py` | **the main loop**: drives your signed-in Edge through the extension |
| `jev-edge-extension/` | the bridge extension (Manifest V3) |
| `visual.py` | the hand-off to Claude when a step needs eyes |
| `jev_browser_loop.py` | shared decision code (`decide`, `plan`, `commits`, thresholds), and a standalone loop over the DevTools protocol on a separate profile (`--temp-profile`, or a persistent one with `--login`); headless by default |
| `calibration/` | saved pages, labeled cases, and the scripts behind the numbers above |
| `jev_hebrew_test.py`, `elements_*.json`, `jev_hebrew_results.json` | the first experiment: the same 21 goals in Hebrew and English against two sites (18/21 in both) |

Why an extension, and not something simpler:

- The DevTools loop cannot use your everyday profile: Chromium ignores the remote-debugging port
  on the default profile since version 136.
- Driving Edge from outside, through the OS accessibility tree (tried with
  [agent-desktop](https://github.com/vercel-labs/agent-desktop)), reads only the active tab of a
  window that is not fully covered, and cannot create a tab group without real mouse input. That
  experiment was removed from the repository.

## Known limits

- Viewport only: controls below the fold need a scroll step first.
- One tab; no iframes, no shadow DOM.
- Controls that share a label (a "delete" button on every row) are told apart by the text of
  their row. When even the row text is identical, only the first is offered.
- Dropdowns are handled when they are native `<select>` elements. A custom dropdown (a button that
  opens a list) works only as a sequence of clicks that Jev has to choose, and `--value` does not
  reach it.
- The DevTools loop (`jev_browser_loop.py`) does not see dropdowns or checkboxes and has no visual
  hand-off; those are in the extension loop only.
- Text can only come from the goal or `--value`, so the loop cannot compose a message.
- English and Hebrew wording only in the commitment rule.

## Credits

The approach, a cheap classifier choosing among deterministic screen state instead of a large
model reading screenshots, is the idea behind
[awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use), which this
project started from as a question: does it work on Windows and on Hebrew sites? No code was
taken from it. Decisions are made by [TypeSafe](https://docs.typesafe.ai)'s Jev. The design and
code here were written with Claude Code.

## License

[MIT](LICENSE)
