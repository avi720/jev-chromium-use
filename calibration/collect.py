"""Collect what the loop perceives on a set of Hebrew pages, for offline calibration.

    python calibration/collect.py

Uses the DevTools loop's browser on a throwaway headless profile (no sign-ins), and saves one
JSON per page under calibration/pages/. Each file is exactly the `page` dict the loop decides on.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from jev_browser_loop import Browser  # noqa: E402

PAGES = {
    "ynet_home": "https://www.ynet.co.il",
    "ynet_article_list": "https://www.ynet.co.il/economy",
    "ksp_home": "https://ksp.co.il/web/",
    "ksp_search": "https://ksp.co.il/web/cat/?search=%D7%90%D7%95%D7%96%D7%A0%D7%99%D7%95%D7%AA",
    "wikipedia_main": "https://he.wikipedia.org/",
    "wikipedia_article": "https://he.wikipedia.org/wiki/%D7%99%D7%A8%D7%95%D7%A9%D7%9C%D7%99%D7%9D",
    "govil_home": "https://www.gov.il/he",
    "btl_home": "https://www.btl.gov.il/Pages/default.aspx",
    "rail_home": "https://www.rail.co.il/",
    "kan_home": "https://www.kan.org.il/",
    "zap_home": "https://www.zap.co.il/",
    "ivory_home": "https://www.ivory.co.il/",
    "post_home": "https://israelpost.co.il/",
    "walla_home": "https://www.walla.co.il/",
    "clalit_home": "https://www.clalit.co.il/he/Pages/default.aspx",
    "shufersal_home": "https://www.shufersal.co.il/online/he/",
    "steimatzky_home": "https://www.steimatzky.co.il/",
    "tau_home": "https://www.tau.ac.il/",
}

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    out = Path(__file__).parent / "pages"
    out.mkdir(exist_ok=True)
    for name, url in PAGES.items():
        if (out / f"{name}.json").exists():
            continue
        browser = Browser(url, temp_profile=True)
        try:
            browser.settle(4)
            page = browser.perceive()
            for e in page["elements"]:
                e.pop("x", None), e.pop("y", None)
            print(f"{name}: {len(page['elements'])} elements, {len(page['page_text'])} text blocks, title {page['title'][:50]!r}")
            if len(page["elements"]) >= 10:
                (out / f"{name}.json").write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                print("  skipped: too few elements (blocked or empty)")
        except Exception as exc:  # a site that refuses a headless browser is simply left out of the set
            print(f"{name}: failed ({type(exc).__name__}: {str(exc)[:80]})")
        finally:
            browser.close()
