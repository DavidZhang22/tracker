import pytest

from app.tracker.discovery import Discoverer
from app.tracker.entry_identity import entry_key
from app.tracker.keywords import matches
from app.tracker.limits import MAX_LINKS
from app.tracker.models import Entry
from app.tracker.parser import merge_entries, parse_page
from app.tracker.recipes import analyze
from app.tracker.store import Store

SOURCE = "https://fiction.example/series/story"


def parse(html, **kwargs):
    return parse_page(html, SOURCE, **kwargs)[0]


def listing(rows):
    return '<h1>A story</h1><ul class="chapters">' + rows + "</ul>"


def chapter(number, *, url=False, extra="", title=None):
    label = title or f"Chapter {number}: A title"
    if url:
        label = f'<a href="{SOURCE}/chapter/{number}">{label}</a>'
    return f'<li data-chapter-id="c{number}">{label}{extra}</li>'


def test_collapsed_mixed_list_keeps_real_urls_dates_and_nonlinks():
    scan = parse(
        "<details><summary>Chapters</summary>"
        + listing(
            chapter(1, url=True, extra='<time datetime="2026-09-01">Sep 1</time>')
            + chapter(2, extra='<time datetime="2026-09-02">Sep 2</time>')
            + chapter(3)
        )
        + "</details>"
    )
    entries = {e.number: e for e in scan.entries}
    assert len(entries) == 3
    assert entries[1].url == SOURCE + "/chapter/1"
    assert entries[2].url == entries[3].url == ""
    assert entries[2].published_at.startswith("2026-09-02")
    assert entries[3].published_at is None
    assert len({entry_key(e) for e in scan.entries}) == 3
    assert "content list" in scan.methods


@pytest.mark.parametrize("tag", ["li", "div", "tr"])
def test_record_layouts_and_table_header(tag):
    rows = "".join(
        f'<{tag} class="episode-item">'
        + (f"<td>Episode {n}</td>" if tag == "tr" else f"Episode {n}")
        + f"</{tag}>"
        for n in (1, 2, 3)
    )
    scan = parse('<table id="episodes"><tr><th>Episode</th></tr>' + rows + "</table>")
    assert [e.number for e in scan.entries] == [1, 2, 3]
    assert all(not e.url for e in scan.entries)


def test_plain_numbered_catalogue_under_heading():
    scan = parse(
        "<h2>Chapters</h2><ol><li>1 A beginning</li><li>2 A departure</li></ol>"
    )
    assert [e.number for e in scan.entries] == [1, 2]


def test_volume_boundaries_and_identical_titles_are_distinct():
    scan = parse(
        "".join(
            f'<section class="chapters"><h2>Volume {n}: Name</h2><ul><li>1 Beginning</li></ul></section>'
            for n in [1, 2]
        )
    )
    assert len(scan.entries) == 2
    assert len({e.source_id for e in scan.entries}) == 2


def test_responsive_copies_do_not_change_identity():
    html = listing(chapter(1) + chapter(2))
    once, twice = parse(html), parse(html + html)
    assert [e.source_id for e in once.entries] == [e.source_id for e in twice.entries]


@pytest.mark.parametrize(
    "href", ["#", "#chapter", "javascript:alert(1)", "", "file:///secret"]
)
def test_placeholder_targets_remain_nonlinks(href):
    scan = parse(
        listing(
            f'<li><a href="{href}">Chapter 1: First</a></li><li>Chapter 2: Second</li>'
        )
    )
    assert len(scan.entries) == 2
    assert all(e.url == "" for e in scan.entries)


def test_no_navigation_statistics_prose_or_unrelated_lists():
    scan = parse("""<nav><ul class="chapters"><li>Chapter 1</li><li>Chapter 2</li></ul></nav>
        <div class="pagination"><li>1 A page</li><li>2 A page</li></div>
        <ul><li>Home</li><li>Contact</li><li>Subscribe</li></ul>
        <ol><li>1 Buy ingredients</li><li>2 Bake the cake</li></ol>
        <p>Chapter 5 is our favorite</p><p>Chapter 6 was next</p>""")
    assert scan.entries == []


def test_explicit_selector_accepts_unnumbered_list_and_only_that_list():
    html = (
        '<ul id="updates"><li>A new feature</li><li>A different update</li></ul>'
        + listing(chapter(9))
    )
    scan = parse(html, selector="#updates")
    assert [e.title for e in scan.entries] == ["A new feature", "A different update"]
    assert all(not e.url for e in scan.entries)


def test_selector_container_keeps_linked_children_too():
    scan = parse(listing(chapter(1, url=True) + chapter(2)), selector=".chapters")
    assert len(scan.entries) == 2 and sum(bool(e.url) for e in scan.entries) == 1


def test_url_filter_excludes_records_without_urls():
    scan = parse(listing(chapter(1, url=True) + chapter(2)), include_path="/chapter/")
    assert len(scan.entries) == 1 and scan.entries[0].url


def test_relative_age_is_context_not_an_invented_date():
    entry = parse(listing("<li>Chapter 166: Next 3 weeks ago</li>")).entries[0]
    assert entry.title == "Chapter 166: Next"
    assert entry.published_at is None and "3 weeks ago" in entry.context


def test_language_context_is_local_to_each_record():
    scan = parse(
        listing(
            '<li lang="en">Chapter 1: First</li><li lang="es">Chapter 1: Primero</li>'
        )
    )
    assert len(scan.entries) == 2
    assert [e.title for e in scan.entries if matches(e, "English")] == [
        "Chapter 1: First"
    ]


def test_refresh_upgrade_loss_reorder_and_title_correction_preserve_state(tmp_path):
    store = Store(tmp_path / "library.db")
    first = parse(listing(chapter(1) + chapter(2)))
    item = store.create(store.save_scan(first.to_dict()), read_indices=[1])
    original = {r["number"]: r for r in store.links(item["id"])["links"]}
    store.update("links", original[2]["id"], {"favorite": True, "ignored": True})
    updated = parse(
        listing(
            chapter(3) + chapter(2, url=True, title="Chapter 2: Corrected") + chapter(1)
        )
    )
    assert store.merge(item["id"], updated.to_dict()) == 1
    row = store.links(item["id"], filter="ignored")["links"][0]
    assert row["id"] == original[2]["id"] and row["read"] and row["favorite"]
    assert row["url"].endswith("/chapter/2")
    assert store.merge(item["id"], first.to_dict()) == 0
    row = store.links(item["id"], filter="ignored")["links"][0]
    assert row["url"].endswith("/chapter/2")
    assert Store(store.path).item(item["id"])["total_count"] == 3


def test_number_identity_survives_new_chapters_and_age_changes():
    first = parse(listing("<li>Chapter 2: Next 3 weeks ago</li>")).entries[0]
    second = parse(
        listing("<li>Chapter 3: New</li><li>Chapter 2: Next 4 weeks ago</li>")
    ).entries[1]
    assert first.source_id == second.source_id


def test_parser_merges_source_identity_prefers_real_url():
    entries = merge_entries(
        [
            Entry("", "One", source_id="list:one"),
            Entry(SOURCE + "/one", "One", source_id="list:one"),
            Entry("", "One", source_id="list:one"),
        ]
    )
    assert len(entries) == 1 and entries[0].url == SOURCE + "/one"


def test_light_refresh_falls_back_safely_when_links_become_text():
    linked = listing("".join(chapter(n, url=True) for n in [1, 2, 3]))
    first, recipe, _ = analyze(linked, SOURCE)
    text = listing("".join(chapter(n) for n in [1, 2, 3, 4]))
    result, _, used = analyze(text, SOURCE, recipe=recipe)
    assert len(first[0].entries) == 3 and len(result[0].entries) == 4 and not used
    assert all(not e.url for e in result[0].entries)


@pytest.mark.asyncio
async def test_pagination_keeps_nonlinks_without_fetching_chapters(monkeypatch):
    monkeypatch.delenv("TRACKER_BROWSER_SOCKET", raising=False)

    class Pages:
        calls = []

        async def get(self, url):
            self.calls.append(url)
            if url == SOURCE:
                return url, listing(
                    chapter(1)
                ) + '<a rel="next" href="?page=2">Next</a>'
            assert url == SOURCE + "?page=2"
            return url, listing(chapter(2))

    fetcher = Pages()
    scan = await Discoverer(fetcher).scan(SOURCE)
    assert len(scan.entries) == 2
    assert fetcher.calls == [SOURCE, SOURCE + "?page=2"]


def test_list_limit():
    scan = parse(listing("".join(chapter(n) for n in range(MAX_LINKS + 3))))
    assert len(scan.entries) == MAX_LINKS
    assert scan.coverage == "partial" and any("limit" in w for w in scan.warnings)


def test_identical_chapter_titles_with_distinct_urls_are_not_coalesced():
    scan = parse(
        listing(
            "".join(
                f'<li><a href="{SOURCE}/chapter/1?edition={n}">Chapter 1</a></li>'
                for n in [1, 2]
            )
        )
    )
    assert len(scan.entries) == 2
    assert scan.entries[0].source_id != scan.entries[1].source_id


def test_mixed_list_preserves_source_order():
    scan = parse(listing(chapter(3) + chapter(2, url=True) + chapter(1)))
    assert [e.number for e in scan.entries] == [3, 2, 1]


def test_language_badges_keep_identical_linkless_titles_separate():
    scan = parse(
        listing(
            "".join(
                f'<li><span class="title">Chapter 1</span><span>{language}</span></li>'
                for language in ["English", "Spanish"]
            )
        )
    )
    assert len(scan.entries) == 2
    assert {e.language for e in scan.entries} == {"en", "es"}


def test_unnumbered_identity_survives_url_loss():
    first = parse(
        '<ul id="updates"><li><a href="/first">First update</a></li><li><a href="/second">Second update</a></li></ul>',
        selector="#updates",
    )
    second = parse(
        '<ul id="updates"><li>First update</li><li>Second update</li></ul>',
        selector="#updates",
    )
    assert [e.source_id for e in first.entries] == [e.source_id for e in second.entries]


def test_chapter_comments_are_not_a_catalogue():
    assert not parse(
        '<div class="comments"><ul><li>Chapter 1: Great chapter</li><li>Chapter 2: More please</li></ul></div>'
    ).entries


def test_http_preview_save_refresh_and_read_controls(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class Listing:
        html = listing(chapter(1) + chapter(2))

        async def scan(self, *_args, **_kwargs):
            return parse(self.html)

    discovery = Listing()
    with TestClient(create_app(tmp_path / "api.db", discovery)) as client:
        preview = client.post("/api/scans", json={"url": SOURCE})
        assert preview.status_code == 200
        saved = client.post(
            "/api/items",
            json={"scan_id": preview.json()["scan_id"], "read_indices": [0]},
        )
        assert saved.status_code == 201
        item = saved.json()
        assert item["total_count"] == 2 and item["read_count"] == 1
        entries = client.get(f"/api/items/{item['id']}/links").json()["links"]
        second = next(e for e in entries if e["number"] == 2)
        assert (
            client.patch(
                f"/api/links/{second['id']}", json={"favorite": True, "read": True}
            ).status_code
            == 200
        )
        discovery.html = listing(chapter(1) + chapter(2, url=True) + chapter(3))
        refreshed = client.post(f"/api/items/{item['id']}/refresh").json()
        assert refreshed["ok"] and refreshed["new_count"] == 1
        entries = client.get(f"/api/items/{item['id']}/links").json()["links"]
        after = next(e for e in entries if e["number"] == 2)
        assert (
            after["id"] == second["id"]
            and after["favorite"]
            and after["read"]
            and after["url"]
        )


@pytest.mark.asyncio
async def test_cloudflare_challenge_has_specific_error_and_reuses_backoff(monkeypatch):
    import httpx

    from app.tracker.urls import DiscoveryError, SafeFetcher

    real_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            403, headers={"cf-mitigated": "challenge"}, text="Challenge"
        )

    async def addresses(_host):
        return ["93.184.216.34"]

    monkeypatch.setattr("app.tracker.urls.public_addresses", addresses)
    monkeypatch.setattr(
        "app.tracker.urls.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    fetcher = SafeFetcher(interval=0)
    for _ in range(2):
        with pytest.raises(DiscoveryError, match="Cloudflare browser verification"):
            await fetcher.get(SOURCE)
    assert len(calls) == 1
