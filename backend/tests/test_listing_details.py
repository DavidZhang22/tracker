import json
from pathlib import Path

from bs4 import BeautifulSoup

from app.tracker.models import Entry
from app.tracker.parser import enrich_visible_context, parse_page
from app.tracker.recipes import analyze
from app.tracker.record_context import RecordContext

SOURCE = "https://liondine.com/meals/latenight"
FIXTURE = Path(__file__).parent / "fixtures/liondine-latenight.html"
DATASET = Path(__file__).parents[1] / "ml/datasets/context-expansion-v1.jsonl"


def food_labels():
    record = next(
        json.loads(line)
        for line in DATASET.read_text(encoding="utf8").splitlines()
        if json.loads(line)["family"] == "liondine.com"
    )
    return next(
        spec["required"] for spec in record["anchors"] if spec["title"] == "JJ's"
    )


def test_real_latenight_menu_keeps_every_food_in_its_hall(monkeypatch):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "cascade")
    html = FIXTURE.read_text(encoding="utf8")
    scan = parse_page(html, SOURCE)[0]
    hall = next(entry for entry in scan.entries if entry.title == "JJ's")
    assert len(scan.entries) == len({entry.url for entry in scan.entries}) == 10
    assert all(food in hall.context for food in food_labels())
    assert "Fry Station\nBoneless Wings\nMozzarella Sticks" in hall.context
    assert "12:00 PM to 10:00 AM" in hall.context
    assert "Closed for latenight" not in hall.context
    assert all(
        "Boneless Wings" not in entry.context
        for entry in scan.entries
        if entry is not hall
    )
    assert all(
        "Closed for latenight" in entry.context
        for entry in scan.entries
        if entry is not hall
    )


def test_latenight_refresh_updates_food_details_even_without_a_safe_recipe(monkeypatch):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "cascade")
    html = FIXTURE.read_text(encoding="utf8")
    _, recipe, _ = analyze(html, SOURCE)
    changed = html.replace("Boneless Wings", "Roasted Mushrooms")
    expected = parse_page(changed, SOURCE)

    actual, _, used = analyze(changed, SOURCE, recipe=recipe)
    assert actual == expected
    assert used or recipe is None
    hall = next(entry for entry in actual[0].entries if entry.title == "JJ's")
    assert "Roasted Mushrooms" in hall.context and "Boneless Wings" not in hall.context


def test_link_wrapping_heading_is_primary_evidence_for_generic_external_record(
    monkeypatch,
):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "off")
    html = '<main><section><a href="https://venue.example/central"><h2>Central Hall</h2></a><p>Soup and rice</p></section></main>'
    scan = parse_page(html, "https://catalog.example/venues")[0]
    assert [(entry.title, entry.url) for entry in scan.entries] == [
        ("Central Hall", "https://venue.example/central")
    ]


def test_fallback_enrichment_uses_existing_record_and_skips_navigation():
    soup = BeautifulSoup(
        '<header><a href="/post/1">A report</a><span>Header secret</span></header><nav><a href="/post/1">A report</a></nav><article><h2><a href="/post/1">A report</a></h2><p>Important findings</p></article><article><a href="/post/2">Another report</a><p>Neighbor secret</p></article>',
        "html.parser",
    )
    entries = [
        Entry("https://example.org/post/1", "A report"),
        Entry("https://example.org/post/3", "Script only", context="Existing context"),
    ]
    enrich_visible_context(
        entries, soup.select("a"), "https://example.org/", RecordContext(soup), set()
    )
    assert len(entries) == 2
    assert "Important findings" in entries[0].context
    assert (
        "Header secret" not in entries[0].context
        and "Neighbor secret" not in entries[0].context
    )
    assert entries[1].context == "Existing context"


def test_details_are_cached_independently_of_model_features(monkeypatch):
    import app.tracker.context_details as formatter

    soup = BeautifulSoup(
        '<article><h2><a href="/post/1">A report</a></h2><p>Soup</p><p>Rice</p></article>',
        "html.parser",
    )
    context = RecordContext(soup)
    features_before = [features for _, _, features in context.regions(soup.a)]
    calls = []
    original = formatter.details_text

    def counted(node, limit=1500):
        calls.append((id(node), limit))
        return original(node, limit)

    monkeypatch.setattr(formatter, "details_text", counted)
    first = context.text(soup.a)
    assert "Soup\nRice" in first
    assert context.text(soup.a) == first
    assert len(calls) == len(set(calls))
    assert [features for _, _, features in context.regions(soup.a)] == features_before


def test_all_hall_foods_survive_shared_url_deduplication(monkeypatch):
    monkeypatch.setenv("TRACKER_LINK_MODEL", "cascade")
    html = FIXTURE.read_text(encoding="utf8")
    soup = BeautifulSoup(html, "html.parser")
    entries = {entry.url: entry for entry in parse_page(html, SOURCE)[0].entries}
    food_count = 0
    for card in soup.select(".hall-card"):
        anchor = card.select_one(".hall-link")
        entry = entries[anchor["href"]]
        foods = [node.get_text(" ", strip=True) for node in card.select(".food-name")]
        food_count += len(foods)
        assert anchor.get_text(" ", strip=True) in entry.context
        assert all(food in entry.context for food in foods)
    assert food_count == 55
    shared = next(entry for entry in entries.values() if entry.title == "Hewitt")
    assert "Hewitt\nClosed for latenight\nDiana\n" in shared.context
    assert "Boneless Wings" not in shared.context
    assert len(entries) == 10
