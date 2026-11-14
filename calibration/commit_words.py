"""Check the commitment rule (the labels the loop will not click without asking) for gaps.

    python calibration/commit_words.py          # the two word lists against the rule
    python calibration/commit_words.py --jev    # also: Jev's judgment of every real label in calibration/pages

Two sources, because the saved pages are signed-out home pages with few commitment buttons:
1. COMMITS / BENIGN below: labels written by hand, the first should all be caught, the second none.
2. Every real label on the saved pages, judged by a Jev Noul and compared with the rule.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from jev_core import commits  # noqa: E402

# Labels of controls that commit something: money, an order, a message, a deletion, consent, an account change.
COMMITS = """
קנה עכשיו|קנו עכשיו|לקנייה|קנייה מהירה|רכישה|לרכישה|רכוש עכשיו|הזמן עכשיו|הזמינו|להזמנה|בצע הזמנה|ביצוע הזמנה|סיום הזמנה|
סיים הזמנה|שלח הזמנה|אישור הזמנה|אשר הזמנה|לתשלום|המשך לתשלום|מעבר לתשלום|שלם|שלם עכשיו|תשלום מאובטח|לקופה|המשך לקופה|
מעבר לקופה|בצע תשלום|אישור תשלום|חייב כרטיס|שלח|שליחה|שלח הודעה|שלח טופס|שלח בקשה|שלח פנייה|שליחת הטופס|הגש|הגשה|הגש בקשה|
הגשת מועמדות|הגש מועמדות|הגש תביעה|מחק|מחיקה|מחק חשבון|מחק הכל|הסר|הסרה|הסר מהרשימה|בטל הזמנה|ביטול הזמנה|בטל מנוי|
ביטול מנוי|בטל עסקה|ביטול עסקה|אשר|אישור|אני מאשר|אני מאשרת|אני מסכים|אני מסכימה|מסכים לתנאים|קבל הכל|קבל את כל העוגיות|
לאשר הכול|אשר הכל|הסכמה|פרסם|פרסום|פרסם תגובה|הגב|שלח תגובה|שתף|שיתוף|העלה|העלאה|העלה קובץ|שמור|שמירה|שמור שינויים|
עדכן|עדכון פרטים|עדכן פרטים|החל|החל שינויים|העבר|העברה|בצע העברה|העברה בנקאית|משיכה|משוך|הפקדה|הפקד|המר|המרה|
הירשם|הרשמה|הרשם עכשיו|הצטרף|הצטרפו עכשיו|הצטרפות|צור חשבון|יצירת חשבון|פתח חשבון|התנתק|התנתקות|יציאה מהחשבון|יציאה|
חתום|חתימה|חתימה דיגיטלית|תרום|תרומה|תרמו עכשיו|הצבע|הצבעה|דווח|דיווח|חסום|חסימה|עקוב|הסר עוקב|קבע תור|זימון תור|
בטל תור|ביטול תור|שריין|שריין מקום|הזמן מקום|הזמנת מקום|חדש מנוי|חידוש מנוי|שדרג|שדרוג|הורד|סיום|סיים|אפס סיסמה|שנה סיסמה|
Buy now|Buy|Pay|Pay now|Purchase|Checkout|Proceed to checkout|Place order|Complete order|Order now|Send|Send message|Submit|
Submit form|Apply|Apply now|Delete|Delete account|Remove|Cancel order|Cancel subscription|Confirm|Confirm order|I agree|Accept|
Accept all|Agree|Publish|Post|Post comment|Reply|Comment|Share|Upload|Save|Save changes|Update|Transfer|Withdraw|Deposit|
Sign up|Register|Join|Join now|Create account|Sign out|Log out|Logout|Subscribe|Unsubscribe|Donate|Vote|Report|Block|Follow|
Book now|Book|Reserve|Upgrade|Renew|Finish|Reset password|Change password
"""

# Look-alikes that only navigate or read: asking about these is noise.
BENIGN = """
תקנון|מסקנות|חדשות|כלכלה|ספורט|חיפוש|סניפים|יצירת קשר|שירות לקוחות|אודות|מבצעים|מדריכי קנייה|מדיניות הובלה|מדיניות הפרטיות|
ההזמנות שלי|היסטוריית הזמנות|מעקב משלוח|פרטי המוצר|קרא עוד|למידע נוסף|הצג עוד|לכל המוצרים|דף הבית|כניסה|התחברות|עזרה|
שאלות נפוצות|השוואת מחירים|סל קניות|עגלה|רשימת מועדפים|תשלומים|אמצעי תשלום|מחשבונים|טפסים ואישורים|הודעות לעיתונות|
גרסאות קודמות|שיחה|קריאה|נגישות|שינוי שפה|English|Home|Search|About|Contact us|Help|Read more|Next|Previous|Menu|
Orders|My account|Cart|Wishlist|Privacy policy|Terms of use|Careers|Blog|Postcards|Reports archive
"""


def words(block: str) -> list[str]:
    return [w.strip() for w in block.replace("\n", "").split("|") if w.strip()]


def check_lists() -> None:
    wanted, benign = words(COMMITS), words(BENIGN)
    missed = [w for w in wanted if not commits(w)]
    noise = [w for w in benign if commits(w)]
    print(f"commitment labels caught: {len(wanted) - len(missed)}/{len(wanted)}")
    print("  missed: " + (" | ".join(missed) or "none"))
    print(f"benign labels left alone: {len(benign) - len(noise)}/{len(benign)}")
    print("  flagged anyway: " + (" | ".join(noise) or "none"))


def check_real_labels() -> None:
    from typesafe_sdk import Noul, NoulCriteria, TypeSafeClient

    labels = sorted({e["text"] for p in (Path(__file__).parent / "pages").glob("*.json")
                     for e in json.loads(p.read_text(encoding="utf-8"))["elements"] if not e.get("field")})
    scores: dict[str, float] = {}
    with TypeSafeClient() as client:
        for start in range(0, len(labels), 40):
            batch = {f"L{i}": lab for i, lab in enumerate(labels[start:start + 40])}
            resp = client.system_one(state={"labels": batch}, questions={
                k: Noul(
                    instructions=f"A signed-in user is on a website. Would clicking the control labeled `labels.{k}` by itself "
                                 "commit something that is hard to undo or that reaches beyond viewing pages?",
                    criteria=NoulCriteria(
                        true="The click itself pays, places or cancels an order, sends a message or form, deletes, publishes, "
                             "gives consent, signs up, signs out, or changes account data.",
                        false="The click only navigates, opens a page or menu, filters, searches or shows information.",
                    ),
                ) for k in batch})
            scores.update({batch[k]: a.noul for k, a in resp.answers.items()})
    flagged = {lab for lab in labels if commits(lab)}
    print(f"\n{len(labels)} real labels from the saved pages; the rule flags {len(flagged)}")
    print("Jev says commitment (>= 0.5) but the rule does not flag:")
    for lab in sorted((lab for lab in labels if scores[lab] >= 0.5 and lab not in flagged), key=lambda x: -scores[x]):
        print(f"  {scores[lab]:.2f}  {lab[:70]}")
    print("the rule flags but Jev says navigation (< 0.3):")
    for lab in sorted((lab for lab in flagged if scores[lab] < 0.3), key=lambda x: scores[x]):
        print(f"  {scores[lab]:.2f}  {lab[:70]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    check_lists()
    if "--jev" in sys.argv:
        check_real_labels()
