import pytest
from bs4 import BeautifulSoup

from app.tracker.record_context import RecordContext


def rejecting_context(html):
    soup = BeautifulSoup(html, "html.parser")
    context = RecordContext(soup)
    assert context.model is not None
    context.score = lambda _: 0.0
    return soup, context


def test_long_heading_owned_card_is_recovered_without_neighbor_details():
    foods = [f"Dish number {index}" for index in range(40)]
    html = (
        '<main><div><div id="one" class="result-card"><a href="/hall/one"><h2>North Hall</h2></a><ul>'
        + "".join(f"<li><span>{food}</span></li>" for food in foods)
        + '</ul></div><div class="result-card"><a href="/hall/two"><h2>South Hall</h2></a><p>Neighbor secret</p></div></div></main>'
    )
    soup, context = rejecting_context(html)
    target = soup.select_one("#one a")
    assert context.record(target) is soup.select_one("#one")
    text = context.text(target)
    assert all(food in text for food in foods)
    assert "Neighbor secret" not in text
    assert "South Hall" not in text


@pytest.mark.parametrize(
    "tail",
    ['<a href="/other">Unrelated target</a>', "<nav>Unrelated navigation</nav>"],
)
def test_incomplete_dom_sample_cannot_prove_unique_record_ownership(tail):
    html = (
        '<main><div><div id="one" class="result-card"><a href="/hall/one"><h2>North Hall</h2></a>'
        + "<span>Body detail</span>" * 220
        + tail
        + '</div><div class="result-card"><a href="/hall/two"><h2>South Hall</h2></a><p>Neighbor secret</p></div></div></main>'
    )
    soup, context = rejecting_context(html)
    target = soup.select_one("#one a")
    assert context.record(target) is target
    assert context.text(target) == "North Hall"
