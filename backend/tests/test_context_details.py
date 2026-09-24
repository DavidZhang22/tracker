from bs4 import BeautifulSoup

from app.tracker.context_details import MAX_NODES, details_text


def region(html):
    return BeautifulSoup(html, "html.parser").find()


def test_selected_menu_keeps_foods_under_their_stations_without_neighbor_leak():
    soup = BeautifulSoup(
        """<main><article id="north"><a href="/north"><h2>North Hall</h2></a>
        <h3>Grill</h3><ul><li>Burger <span>with cheddar</span></li><li>Fries</li></ul>
        <h3>Salad bar</h3><ul><li>Rice</li><li>Tomatoes</li></ul></article>
        <article><h2>South Hall</h2><ul><li>Pasta</li></ul></article></main>""",
        "html.parser",
    )
    assert details_text(soup.find(id="north")) == (
        "North Hall\nGrill\nBurger with cheddar\nFries\nSalad bar\nRice\nTomatoes"
    )


def test_tooltips_cannot_crowd_out_visible_foods():
    html = '<article><h2 title="North Hall">North Hall</h2>'
    html += "".join(
        f'<p><img alt="{("Long allergen tooltip " * 30)}">Food {i}</p>'
        for i in range(12)
    )
    result = details_text(region(html + "</article>"), limit=180)
    assert result.startswith("North Hall\nFood 0\nFood 1\nFood 2\n")
    assert "Food 11" in result
    assert result.count("North Hall") == 1
    assert len(result) <= 180


def test_repeated_visible_text_is_not_removed_from_distinct_sections():
    assert details_text(
        region("<div><h3>Lunch</h3><p>Rice</p><h3>Dinner</h3><p>Rice</p></div>")
    ) == ("Lunch\nRice\nDinner\nRice")


def test_table_rows_and_mixed_inline_wording_remain_readable():
    assert (
        details_text(
            region(
                "<table><tr><th>Dish</th><th>Diet</th></tr>"
                "<tr><td>Mac <strong>and</strong> cheese</td><td>Vegetarian</td></tr>"
                "<tr><td>Soup<br>Tomato</td><td>Vegan</td></tr></table>"
            )
        )
        == "Dish | Diet\nMac and cheese | Vegetarian\nSoup\nTomato | Vegan"
    )


def test_metadata_fallback_retains_languages_and_accessible_names():
    assert details_text(
        region(
            '<div data-language="en"><img alt="Soup"><span aria-label="Vegan"></span></div>'
        )
    ) == ("English en\nSoup\nVegan")


def test_language_metadata_survives_many_tooltips():
    html = "<div>Soup" + '<i title="Very long dietary notice"></i>' * 100
    html += '<span data-language="ja"></span></div>'
    result = details_text(region(html), limit=35)
    assert result.startswith("Soup\nJapanese ja")


def test_excluded_or_hidden_subtrees_and_comments_never_become_details():
    html = """<article><p>Safe food</p><!-- hidden comment -->
    <script>run_bad_code()</script><style>.bad {}</style><template>Hidden template</template>
    <noscript>Fallback</noscript><nav><span title="Secret">Navigation</span></nav>
    <footer>Footer</footer><button>Order</button><select><option>Choice</option></select>
    <p hidden="false">Not visible</p><p aria-hidden="true">Not accessible</p>
    <p style="DISPLAY: none !important;">CSS hidden</p>
    <p style="color: red; visibility : hidden">Also hidden</p>
    <svg><title>Icon internals</title></svg><p>&lt;script&gt;literal&lt;/script&gt;</p></article>"""
    assert details_text(region(html)) == "Safe food\n<script>literal</script>"


def test_huge_dom_stops_before_nodes_beyond_the_traversal_budget():
    node = region("<div>Start" + "<i></i>" * (MAX_NODES + 10) + "Never reached</div>")
    assert details_text(node) == "Start"


def test_huge_text_depth_and_output_are_bounded():
    assert details_text(region("<p>" + "X" * 100_000 + "</p>"), limit=17) == "X" * 17
    assert details_text(region("<div>" * 100 + "Deep" + "</div>" * 100)) == ""
    assert details_text(region("<p>Food</p>"), limit=0) == ""
    assert details_text(None) == ""


def test_tooltips_attached_to_visible_labels_are_not_unattributed_facts():
    node = region(
        '<article title="Whole menu"><h2>Menu</h2>'
        '<div title="Allergens: Dairy"><span>Mozzarella Sticks</span></div>'
        '<p aria-label="Allergens: Soy">Fries</p></article>'
    )
    assert details_text(node) == "Menu\nMozzarella Sticks\nFries"


def test_icon_language_labels_survive_without_detaching_other_tooltips():
    node = region(
        '<article><span title="Japanese">日本語</span>'
        '<svg aria-label="English"><title>flag</title></svg>'
        '<p data-language="fr">Chapter 2</p>'
        '<div title="Open at noon"><i></i></div></article>'
    )
    result = details_text(node)
    assert "Japanese ja" in result
    assert "English en" in result
    assert "French fr" in result
    assert "Open at noon" in result
    assert "flag" not in result
