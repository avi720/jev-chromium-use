"""Collect what the loop perceives on a set of Hebrew pages, for offline calibration.

    python calibration/collect.py

Opens each page in a background tab through the bridge extension, exactly as the loop does, and
saves one JSON per page under calibration/pages/: the `page` dict the loop decides on. Pages that
already have a file are skipped, so delete a file to collect that page again. Your browser's
cookies apply, so a site you are signed in to is saved signed in: use public pages.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from jev_ext_loop import Bridge, Tab  # noqa: E402

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
    todo = {name: url for name, url in PAGES.items() if not (out / f"{name}.json").exists()}
    if not todo:
        raise SystemExit("every page already has a file; delete one to collect it again")
    bridge = Bridge()
    try:
        for name, url in todo.items():
            tab = Tab(bridge, url, "Jev: calibration")
            try:
                tab.settle(4)
                page = tab.perceive(hide_overlays=True)  # a consent wall would hide the page being measured
                for e in page["elements"]:
                    e.pop("box", None)
                print(f"{name}: {len(page['elements'])} elements, {len(page['page_text'])} text blocks, title {page['title'][:50]!r}")
                if len(page["elements"]) >= 10:
                    (out / f"{name}.json").write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
                else:
                    print("  skipped: too few elements (blocked or empty)")
            finally:
                bridge.call("close", tabId=tab.id)
    finally:
        bridge.close()
