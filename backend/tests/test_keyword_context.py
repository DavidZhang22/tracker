import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.tracker.discovery import Discoverer
from app.tracker.keywords import matches, terms
from app.tracker.models import Entry
from app.tracker.parser import parse_page
from app.tracker.record_context import RecordContext, load_record_model
from tests.test_discovery import FakeFetcher

SOURCE = "https://collection.example/series"


@pytest.mark.parametrize(
    "layout",
    [
        '<div class="chapter"><div><a href="/read/{i}">Chapter {i}</a></div><div>{language} {topic}</div></div>',
        '<article><header><h3><a href="/read/{i}">Chapter {i}</a></h3></header><img alt="{language}"><span>{topic}</span></article>',
        '<li><span title="{language}"></span><div><span><a href="/read/{i}">Chapter {i}</a></span></div><div>{topic}</div></li>',
        '<div><a href="/read/{i}">Chapter {i}</a></div><div>{language} {topic}</div>',
        '<tr><td><a href="/read/{i}">Chapter {i}</a></td></tr><tr><td class="subtext">{language} {topic}</td></tr>',
    ],
)
async def test_languages_and_keywords_stay_with_the_correct_record(layout):
    html = (
        '<html lang="en"><nav>English Remote</nav><main>'
        + "".join(
            layout.format(i=i, language=lang, topic=topic)
            for i, lang, topic in [
                (1, "French", "Remote"),
                (2, "English", "Onsite"),
                (3, "English", "Remote"),
            ]
        )
        + "</main><footer>English</footer></html>"
    )
    fetcher = FakeFetcher({SOURCE: html})
    result = await Discoverer(fetcher).scan(SOURCE, keywords="ｅｎｇｌｉｓｈ, remote")
    assert [e.number for e in result.entries] == [3]
    assert fetcher.calls == [SOURCE]
    assert result.unfiltered_count == 3


def test_scoped_headings_accessible_references_and_language_attributes():
    html = '<section><h2>French</h2><div><a href="/read/1">Chapter 1</a></div><div><a href="/read/2">Chapter 2</a></div></section><section><h2>English</h2><article><a href="/read/3" aria-describedby="group">Chapter 3</a><span data-language="en">Digital</span></article><article><a href="/read/4">Chapter 4</a><span lang="ja">Print</span></article></section><div id="group">Official group</div>'
    rows = parse_page(html, SOURCE)[0].entries
    assert [e.number for e in rows if matches(e, "French")] == [1, 2]
    assert [e.number for e in rows if matches(e, "English, Official")] == [3]
    assert [e.number for e in rows if matches(e, "Japanese")] == [4]
    assert not matches(next(e for e in rows if e.number == 4), "English")


def test_keywords_are_literal_whole_terms_and_bounded():
    entry = Entry(SOURCE, "Remote control", context="English translation")
    assert matches(entry, "English, remote control")
    assert not matches(
        entry, "en"
    )  # A word fragment does not count as a language label.
    assert not matches(entry, "French")
    assert terms("English, ENGLISH") == ["english"]
    with pytest.raises(ValueError):
        terms(",".join(str(i) for i in range(11)))
    with pytest.raises(ValueError):
        terms("x" * 301)


def test_real_job_context_matches_location_without_neighbor_leakage():
    text = (Path(__file__).parent / "fixtures/jobs-table.html").read_text(
        encoding="utf8"
    )
    rows = parse_page(text, "https://github.com/SimplifyJobs/New-Grad-Positions")[
        0
    ].entries
    texas = [e for e in rows if matches(e, "Texas")]
    assert texas and all("Dell Technologies" in e.title for e in texas)
    assert not any(matches(e, "Texas") for e in rows if "Anysphere" in e.title)


def test_malformed_neighbor_url_does_not_break_valid_context():
    rows = parse_page(
        '<article><a href="/read/1">Chapter 1</a><span>English</span><a href="http://[">Malformed utility</a></article>',
        SOURCE,
    )[0].entries
    assert len(rows) == 1 and rows[0].number == 1


async def test_structured_feed_keywords_and_empty_matches():
    payload = json.dumps(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Topics",
            "items": [
                {
                    "url": SOURCE + "/1",
                    "title": "Episode one",
                    "language": "en",
                    "tags": ["Science"],
                },
                {
                    "url": SOURCE + "/2",
                    "title": "Episode two",
                    "language": "fr",
                    "tags": ["Science"],
                },
            ],
        }
    )
    scanner = Discoverer(FakeFetcher({SOURCE: payload}))
    result = await scanner.scan(SOURCE, keywords="English, Science")
    assert [e.url for e in result.entries] == [SOURCE + "/1"]
    result = await scanner.scan(SOURCE, keywords="German")
    assert not result.entries and result.unfiltered_count == 2
    assert "No links matched" in " ".join(result.warnings)


def test_missing_record_model_does_not_borrow_global_language(monkeypatch, tmp_path):
    import app.tracker.record_context as module

    monkeypatch.setattr(module, "MODEL_PATH", tmp_path / "missing.json")
    load_record_model.cache_clear()
    try:
        soup = BeautifulSoup(
            '<html lang="en"><nav>English</nav><a href="/read/1">Chapter 1</a></html>',
            "html.parser",
        )
        assert RecordContext(soup).text(soup.a) == "Chapter 1"
    finally:
        load_record_model.cache_clear()


@pytest.mark.parametrize("corruption", ["cycle", "nan", "oversize"])
def test_record_model_rejects_corrupt_artifacts(monkeypatch, tmp_path, corruption):
    import app.tracker.record_context as module

    data = json.loads(module.MODEL_PATH.read_text())
    if "layers" in data:
        if corruption == "cycle":
            data["layers"][0]["weights"][0] = []
        elif corruption == "nan":
            data["layers"][0]["bias"][0] = float("nan")
        else:
            data["layers"] *= 20
    elif corruption == "cycle":
        data["trees"][0][0][2] = 0
    elif corruption == "nan":
        data["intercept"] = float("nan")
    else:
        data["trees"] *= 20
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(module, "MODEL_PATH", path)
    load_record_model.cache_clear()
    try:
        assert load_record_model() is None
    finally:
        load_record_model.cache_clear()
