"""Run the loop's own decision on every labeled case and report how the thresholds behave.

    python calibration/run.py            # calls Jev once per case, saves the answers, prints the report
    python calibration/run.py --report   # report only, from the saved answers

It sends exactly the request the loop sends (`decide`) on the saved pages and applies the loop's
own policy (`plan`), so the numbers describe the loop as it runs. Every step lands in one of three
buckets: right, stopped (a hand-off or a harmless scroll: costs time, does no harm), or wrong.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
from typesafe_sdk import TypeSafeClient  # noqa: E402

import jev_browser_loop as loop  # noqa: E402
from cases import CASES  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results.json"
PAGES = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (HERE / "pages").glob("*.json")}


def collect() -> list[dict]:
    rows = []
    with TypeSafeClient() as client:
        for page_name, goal, kind, gold in CASES:
            page = PAGES[page_name]
            fields = [e for e in page["elements"] if e.get("field") and not e["secret"]]
            answers = loop.decide(client, goal, page, [], can_type=bool(fields)).answers
            saved = {}
            for name, a in answers.items():  # keep only what `plan` reads, so a report needs no API call
                saved[name] = {"noul": a.noul} if hasattr(a, "noul") else {
                    "choice": a.choice, "confidence": a.confidence, "probabilities": dict(a.probabilities)}
            rows.append({"page": page_name, "goal": goal, "kind": kind, "gold": gold, "answers": saved})
            print(".", end="", flush=True)
    print()
    RESULTS.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return rows


def judge(row: dict, min_conf: float) -> tuple[str, dict, str]:
    """Apply the loop's policy to one saved case. Returns (right | stopped | wrong, the step, what was acted on)."""
    page = PAGES[row["page"]]
    resp = SimpleNamespace(answers={k: SimpleNamespace(**v) for k, v in row["answers"].items()})
    step = loop.plan(resp, page, already_typed=False, min_conf=min_conf)
    target = page["elements"][step["index"]] if step["index"] is not None else None
    text = target["text"] if target else ""
    if row["kind"] == "visual":
        return ("right" if step["do"] == "look" else "wrong"), step, text
    if step["do"] in ("handoff", "scroll", "look"):  # a needless look costs a Claude call, not a wrong action
        return "stopped", step, text
    if row["kind"] == "click":
        ok = step["do"] in ("click", "type") and any(g in text for g in row["gold"])
    elif row["kind"] == "type":
        ok = step["do"] == "type" and target["search"]
    elif row["kind"] == "done":
        ok = step["do"] == "done"
    else:  # nothing on the page fits: any action is wrong
        ok = False
    return ("right" if ok else "wrong"), step, text


def report(rows: list[dict]) -> None:
    kinds = [r["kind"] for r in rows]
    print(f"{len(rows)} cases on {len({r['page'] for r in rows})} pages: {kinds.count('click')} click, {kinds.count('type')} type, "
          f"{kinds.count('done')} done, {kinds.count('none')} where nothing fits, {kinds.count('visual')} visual")
    clicks = [r for r in rows if r["kind"] == "click"]
    raw = sum(any(g in PAGES[r["page"]]["elements"][int(r["answers"]["element"]["choice"][1:])]["text"] for g in r["gold"]) for r in clicks)
    print(f"raw element choice, no thresholds: {raw}/{len(clicks)} click cases = {raw / len(clicks):.0%}")
    print(f"\nwhole policy, by MIN_CONF (others: DONE_AT={loop.DONE_AT} FITS_AT={loop.FITS_AT} SEARCH_AT={loop.SEARCH_AT} "
          f"SEARCH_UNLESS={loop.SEARCH_UNLESS} VISUAL_AT={loop.VISUAL_AT})\nMIN_CONF   right        stopped      wrong")
    for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        v = [judge(r, t)[0] for r in rows]
        n = len(v)
        mark = "  <- in use" if abs(t - loop.MIN_CONF) < 1e-9 else ""
        print(f"  {t:.1f}    {v.count('right'):3d} ({v.count('right') / n:.0%})    {v.count('stopped'):3d} ({v.count('stopped') / n:.0%})"
              f"    {v.count('wrong'):3d} ({v.count('wrong') / n:.1%}){mark}")
    print(f"\nat MIN_CONF={loop.MIN_CONF}, by kind of case:")
    for k in ("click", "type", "done", "none", "visual"):
        v = [judge(r, loop.MIN_CONF)[0] for r in rows if r["kind"] == k]
        print(f"  {k:5s} right {v.count('right'):3d}  stopped {v.count('stopped'):3d}  wrong {v.count('wrong'):3d}")
    print("\nwrong steps at the thresholds in use:")
    for r in rows:
        verdict, step, text = judge(r, loop.MIN_CONF)
        if verdict == "wrong":
            print(f"  [{r['kind']}] {r['page']}: {r['goal']!r} -> {step['do']} {text[:40]!r} ({step['why']})")
    print("\nright answers the thresholds stopped:")
    for r in rows:
        verdict, step, text = judge(r, loop.MIN_CONF)
        if verdict == "stopped" and r["kind"] != "none":
            print(f"  [{r['kind']}] {r['page']}: {r['goal']!r} -> {step['do']} ({step['why']})")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    report(json.loads(RESULTS.read_text(encoding="utf-8")) if "--report" in sys.argv else collect())
