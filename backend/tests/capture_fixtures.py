"""One-time fixture reduction from locally fetched pages; keeps link metadata only."""

from pathlib import Path

from bs4 import BeautifulSoup

root = Path(__file__).parent
out = root / "fixtures"
out.mkdir(exist_ok=True)
for name in ("asura", "royalroad"):
    soup = BeautifulSoup(
        (root / "live" / f"{name}.html").read_text(encoding="utf-8"), "html.parser"
    )
    if name == "asura":
        anchors = [
            a
            for a in soup.select("a[href]")
            if "/chapter/" in a["href"]
            and a.get_text(" ", strip=True).startswith("Chapter")
        ]
        content = "".join(str(a) for a in anchors)
        html = f"<h1>The Nebula's Civilization</h1><h2>140 Chapters</h2><details><summary>Chapters</summary>{content}</details>"
    else:
        content = str(soup.select_one("#chapters"))
        html = f"<h1>Mother of Learning</h1><h2>109 Chapters</h2>{content}"
    # Preserve semantic markup and dates but drop styling and client data.
    reduced = BeautifulSoup(html, "html.parser")
    for node in reduced.find_all(True):
        node.attrs = {
            k: v
            for k, v in node.attrs.items()
            if k in {"href", "id", "datetime", "title"}
        }
    (out / f"{name}.html").write_text(str(reduced), encoding="utf-8")
