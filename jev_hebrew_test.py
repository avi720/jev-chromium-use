"""Does TypeSafe Jev pick the right element on Hebrew sites?

Each case: a goal (Hebrew and English versions), the page's clickable elements, and the
labels that count as correct. One Choice per goal, options = every element on the page.
"""

import json
import sys
import time
from pathlib import Path

from typesafe_sdk import Choice, TypeSafeClient

HERE = Path(__file__).parent
PAGES = {name: json.loads((HERE / f"elements_{name}.json").read_text(encoding="utf-8")) for name in ("ynet", "ksp")}

# (page, Hebrew goal, English goal, substrings of acceptable element texts)
CASES = [
    ("ynet", "פתח את מדור הספורט", "Open the sports section", ["ספורט"]),
    ("ynet", "מה מזג האוויר היום?", "What's the weather today?", ["מזג אוויר", "לתחזית המלאה", "23°"]),
    ("ynet", "התחבר לחשבון שלי", "Log in to my account", ["כניסה"]),
    ("ynet", "קרא את הכתבה על רעידת האדמה באילת", "Read the article about the earthquake in Eilat", ["רעידת אדמה"]),
    ("ynet", "תראה לי את כל המבזקים", "Show me all the news flashes", ["לכל המבזקים", "לעמוד המבזקים המלא", "מבזקים"]),
    ("ynet", "איך אבדיה שיחק אמש?", "How did Avdija play last night?", ["אבדיה כיכב"]),
    ("ynet", "רוצה לדווח למערכת על אירוע שראיתי", "I want to report an incident I saw to the newsroom", ["דווחו לנו"]),
    ("ynet", "פתח את הגדרות הנגישות", "Open the accessibility settings", ["נגישות"]),
    ("ynet", "כתבות על דירות ושוק הדיור", "Articles about apartments and the housing market", ['נדל"ן']),
    ("ynet", "מה קורה עם השערורייה של מנכ\"ל אנבידיה?", "What's going on with the Nvidia CEO scandal?", ["אנבידיה"]),
    ("ksp", "הוסף לעגלה את הטאבלט של שיאומי", "Add the Xiaomi tablet to the cart", ["להוסיף לעגלת טאבלט"]),
    ("ksp", "פתח את עגלת הקניות", "Open the shopping cart", ["0 עגלה", "תפריט עגלת קניות"]),
    ("ksp", "איפה הסניפים ומתי הם פתוחים?", "Where are the branches and when are they open?", ["סניפים ושעות פעילות"]),
    ("ksp", "אני רוצה לבטל הזמנה שעשיתי", "I want to cancel an order I placed", ["ביטול עסקה"]),
    ("ksp", "קנה כרטיס מתנה לחבר", "Buy a gift card for a friend", ["כרטיס מתנה"]),
    ("ksp", "השווה את פאזל הקולוסיאום למוצרים אחרים", "Compare the Colosseum puzzle with other products", ["להשוואת דגמים פאזל תלת מימדי להרכבה 74"]),
    ("ksp", "שמור במועדפים את כפפות האיגרוף", "Save the boxing gloves to favorites", ["להוסיף למועדפים כפפות איגרוף"]),
    ("ksp", "מה תנאי המשלוח?", "What are the delivery terms?", ["מדיניות הובלה"]),
    ("ksp", "אני רוצה לעבוד אצלכם", "I want to work for you", ["דרושים", 'שלחו קו"ח']),
    ("ksp", "אני מחפש מחשב נייד", "I'm looking for a laptop", ["מחשבים וסלולר", "שורת חיפוש"]),
    ("ksp", "מה המבצעים החודש?", "What are this month's deals?", ["מבצעים", "מבצעי נובמבר", "לכל המבצעים", "עבור לכלל המוצרים במבצע"]),
]


def build_question(goal: str, elements: list[list[str]]) -> Choice:
    criteria = {f"e{i}": f"{role}: {text}" for i, (role, text) in enumerate(elements)}
    return Choice(
        instructions=(
            "A user is on the web page described in `page` and wants to accomplish `goal`. "
            "Which single on-page element should be clicked next to make progress toward the goal?"
        ),
        criteria=criteria,
    )


def main() -> None:
    results = []
    with TypeSafeClient() as client:
        for page, he, en, gold in CASES:
            elements = PAGES[page]["elements"]
            for lang, goal in (("he", he), ("en", en)):
                state = {"page": {"url": PAGES[page]["url"], "title": PAGES[page]["title"]}, "goal": goal}
                t0 = time.perf_counter()
                resp = client.system_one(state=state, questions={"target": build_question(goal, elements)})
                ms = (time.perf_counter() - t0) * 1000
                ans = resp.answers["target"]
                idx = int(ans.choice[1:])
                picked = elements[idx][1]
                ok = any(g in picked for g in gold)
                top3 = sorted(ans.probabilities.items(), key=lambda kv: -kv[1])[:3]
                results.append(
                    {
                        "page": page, "lang": lang, "goal": goal, "picked": picked, "ok": ok,
                        "confidence": round(ans.confidence, 3), "ms": round(ms),
                        "tokens_in": resp.usage.input_tokens,
                        "top3": [(elements[int(k[1:])][1][:50], round(p, 3)) for k, p in top3],
                    }
                )
                print(f"{'OK ' if ok else 'XX '} [{page}/{lang}] conf={ans.confidence:.2f} {ms:5.0f}ms  {goal[:45]!r} -> {picked[:60]!r}", flush=True)

    (HERE / "jev_hebrew_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    for lang in ("he", "en"):
        rs = [r for r in results if r["lang"] == lang]
        acc = sum(r["ok"] for r in rs) / len(rs)
        conf = sum(r["confidence"] for r in rs) / len(rs)
        print(f"{lang}: accuracy {acc:.0%} ({sum(r['ok'] for r in rs)}/{len(rs)}), mean confidence {conf:.2f}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
