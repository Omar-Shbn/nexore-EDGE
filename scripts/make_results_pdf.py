"""Render docs/RESULTS.md to a standalone PDF (docs/RESULTS.pdf).

Markdown -> styled HTML -> Edge/Chrome headless print. Relative links are
rewritten to the live site / repo so they work from a PDF in Google Drive.

  python scripts/make_results_pdf.py [--site URL] [--repo URL]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

CSS = """
@page { size: A4; margin: 16mm 15mm; }
* { box-sizing: border-box; }
body { font: 11pt/1.5 'Segoe UI', Roboto, Arial, sans-serif; color: #14192b; }
h1 { font-size: 24pt; line-height: 1.15; margin: 0 0 4pt; color: #1d3f94; }
h2 { font-size: 15pt; margin: 20pt 0 6pt; color: #1d3f94; break-after: avoid; }
p, li { margin: 5pt 0; }
a { color: #1f5fd6; }
img { display: block; max-width: 100%; max-height: 105mm; margin: 8pt auto;
      object-fit: contain; break-inside: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt;
        break-inside: avoid; }
th { background: #1d3f94; color: #fff; text-align: left; }
th, td { border: 1px solid #c8d0e6; padding: 4pt 6pt; vertical-align: top; }
tr:nth-child(even) td { background: #f2f5fc; }
td:first-child { white-space: nowrap; }
code { font-family: Consolas, monospace; background: #eef1fa; padding: 0 3pt; }
hr { border: 0; border-top: 1px solid #c8d0e6; margin: 14pt 0; }
.byline { color: #4a5272; margin: 0 0 10pt; }
"""


def find_browser() -> str:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    sys.exit("no Edge/Chrome found for headless printing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="https://omar-shbn.github.io/nexore-EDGE/")
    ap.add_argument("--repo", default="https://github.com/Omar-Shbn/nexore-EDGE")
    ap.add_argument("--out", default=str(DOCS / "RESULTS.pdf"))
    a = ap.parse_args()

    md = (DOCS / "RESULTS.md").read_text(encoding="utf-8")
    md = md.replace("(index.html)", f"({a.site})")
    md = md.replace("(assets/demo.gif)", f"({a.repo}/blob/main/docs/assets/demo.gif)")
    md = re.sub(r"\(assets/([\w\.]+\.(?:md|csv))\)",
                lambda m: f"({a.repo}/blob/main/docs/assets/{m.group(1)})", md)
    md = md.replace(
        "**Interactive replay:** open [`index.html`](" + a.site + ")",
        "**Live interactive dashboard:** [" + a.site + "](" + a.site + ")")
    # byline under the H1
    md = md.replace("\n\nEverything on this page",
                    "\n\n<p class=\"byline\">ATP 2026 · EDGE Group track · Team Nexore · "
                    f"code: <a href=\"{a.repo}\">{a.repo}</a></p>\n\nEverything on this page", 1)

    body = markdown.markdown(md, extensions=["tables", "fenced_code", "md_in_html"])
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<base href='{DOCS.as_uri()}/'><title>Results</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")
    tmp = ROOT / "results" / "_results_print.html"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(html, encoding="utf-8")

    out = Path(a.out)
    subprocess.run([find_browser(), "--headless=new", "--disable-gpu",
                    "--no-pdf-header-footer", "--virtual-time-budget=4000",
                    f"--print-to-pdf={out}", tmp.as_uri()],
                   check=True, capture_output=True)
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
