"""Authored layout contracts, never presented as captured or held-out examples."""

from bs4 import BeautifulSoup

from app.tracker.urls import canonical_url


def pages():
    titles = (
        "Small discoveries",
        "Q&A",
        "Summer",
        "Side effects",
        "Version 3.2",
        "A new beginning",
    )
    for variant in range(60):
        source = f"https://layout-{variant}.example/blog/"
        positives, records = set(), []
        for i, title in enumerate(titles):
            path = (
                f"/blog/2025/07/13/story-{i}",
                f"/blog/story-{i}",
                f"/{i + 100}/",
                f"/posts/story-{i}",
                f"/read?id={i + 100}",
            )[variant % 5]
            host = f"https://publisher-{i}.example" if variant % 3 == 0 else source[:-6]
            target = host + path
            positives.add(canonical_url(target))
            anchor = f'<a href="{target}">{title}</a>'
            heading = (
                f"<h{variant % 4 + 2}>{anchor}</h{variant % 4 + 2}>"
                if variant % 4
                else f'<span class="title">{anchor}</span>'
            )
            date = (
                '<time datetime="2025-07-13">13 July 2025</time>'
                if variant % 2
                else "13 July 2025"
            )
            body = (
                "This paragraph discusses the story and includes a reference. " * 10
                + '<a href="https://references.example/topic">other research</a>'
            )
            tag = ("article", "li", "div", "tr", "p")[variant % 5]
            records.append(
                f'<{tag} class="entry">{heading} {date}<a rel="author" href="/authors/person-{i}">A Person</a><p>{body}</p></{tag}>'
            )
        negatives = "".join(
            f'<li><a href="/blog/{year}/{month}/">{label} {year}</a></li>'
            for year in (2023, 2024, 2025)
            for month, label in (("03", "March"), ("jul", "July"), ("12", "December"))
        )
        wrapper = "details" if variant % 2 else "section"
        document = (
            f'<nav><a href="/blog/">Blog</a><a href="/categories/">Topics</a></nav><main><{wrapper}>'
            + "".join(records)
            + f'</{wrapper}></main><div class="archives">{negatives}</div><footer><a href="/subscribe">Subscribe</a></footer>'
        )
        yield source, BeautifulSoup(document, "html.parser"), positives
