from bs4 import BeautifulSoup

from app.tracker.dom import HEADINGS, first_tag, tags


def test_direct_walk_matches_soup_queries_in_nested_and_hidden_content():
    soup = BeautifulSoup(
        '<main><h1>Title<a href="">Empty target</a></h1><a>Missing target</a>'
        '<article hidden><h2><a href="/a"><b>A</b></a></h2><div><h3>Nested</h3>'
        '<a href="/b"><h4>B</h4></a><time datetime="2026-09-10">Today</time>'
        '<relative-time datetime="2026-09-11"></relative-time></div></article>'
        '<!-- <a href="/not-real">Comment</a> --><script>"<a>Script</a>"</script></main>',
        "html.parser",
    )
    for root in [soup, *soup.find_all(True)]:
        for names, attribute in [
            (HEADINGS, None),
            ({"a"}, "href"),
            ({"a"}, None),
            ({"time", "relative-time"}, None),
        ]:
            kwargs = {attribute: True} if attribute else {}
            for limit in [None, 1, 2, 12, 32]:
                expected = root.find_all(list(names), limit=limit, **kwargs)
                assert [
                    id(n) for n in tags(root, names, attribute=attribute, limit=limit)
                ] == [id(n) for n in expected]
            assert first_tag(root, names, attribute=attribute) is root.find(
                list(names), **kwargs
            )
