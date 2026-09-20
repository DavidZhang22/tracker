"""Authored training curriculum. These are not scraped or held-out observations."""

import json
from pathlib import Path

# Each row names a format, an example page heading, a format description, and
# a content path/title. Varying topics is intentional; subject != format.
SEEDS = {
    "comic": [
        (
            "Comics archive",
            "An illustrated comic strip about daily life.",
            "/comic/231",
            "A difficult Monday",
        ),
        (
            "Starlight manga",
            "A science fiction manga series.",
            "/manga/starlight/12",
            "Chapter 12",
        ),
        ("River City", "Read our webcomic.", "/strip/123", "123: Home again"),
        (
            "Bright Future",
            "A Korean manhwa translated in English.",
            "/series/future/chapter/15",
            "Chapter 15",
        ),
        (
            "Chapter list",
            "A funny webtoon set in a music academy.",
            "/episodes/9",
            "Episode 9",
        ),
        (
            "Strips",
            "Browse all comic strips from the beginning.",
            "/view.php?comic=42",
            "The lost hat",
        ),
        (
            "Artwork in sequence",
            "An ongoing manhua adventure.",
            "/read/13",
            "Chapter 13",
        ),
    ],
    "novel": [
        (
            "The Quiet Mage",
            "An original fantasy web novel.",
            "/chapter/10",
            "Chapter 10: Return",
        ),
        (
            "Catalog of ebooks",
            "Download novels and books to read.",
            "/ebooks/43",
            "Pride and Prejudice",
        ),
        (
            "Table of contents",
            "Serial fiction told in weekly chapters.",
            "/2025/03/01/9-10",
            "9.10",
        ),
        (
            "Collected works",
            "Complete novels from the public domain.",
            "/books/wandering",
            "The Wandering Traveller",
        ),
        (
            "Moonlit Road",
            "Read a webnovel about magic and research.",
            "/fiction/123/chapter/9",
            "Chapter 9",
        ),
        (
            "New books",
            "Browse the latest ebooks in our library.",
            "/book/451",
            "The Lost City",
        ),
        (
            "An unfinished story",
            "A serial novel about a comic artist.",
            "/chapters/2",
            "Chapter 2",
        ),
    ],
    "podcast": [
        (
            "Full Episode Archive",
            "Our weekly podcast delivers news to your earbuds.",
            "/episodes/show/27",
            "The future of cities",
        ),
        (
            "Ocean conversations",
            "Listen to our audio show about marine science.",
            "/episodes/42",
            "Deep water",
        ),
        (
            "All episodes",
            "A podcast about everyday sounds.",
            "/episodes/rain",
            "The sound of rain",
        ),
        (
            "Interviews",
            "A radio show about music and musicians.",
            "/audio/show-11",
            "Album covers",
        ),
        (
            "Software conversations podcast",
            "Programming interviews and news.",
            "/podcast/39",
            "A new release",
        ),
        (
            "Daily science",
            "Subscribe to the science podcast.",
            "/show/12",
            "The launch",
        ),
        (
            "Listen to the latest",
            "A podcast on books, comics, and films.",
            "/episodes/78",
            "New books this month",
        ),
    ],
    "music": [
        ("Music", "Stream the latest tracks and songs.", "/release/23", "Set Me Free"),
        (
            "Discography",
            "Albums and singles from the band.",
            "/album/23",
            "Into the light",
        ),
        (
            "New releases",
            "New music from our record label.",
            "/releases/artist-one",
            "Blue Sky",
        ),
        (
            "Songs",
            "Listen to our latest songs and remixes.",
            "/tracks/falling",
            "Falling",
        ),
        (
            "Recordings",
            "Complete discography of the orchestra.",
            "/album/symphony",
            "Symphony 4",
        ),
        (
            "All albums",
            "Buy or stream the latest album.",
            "/albums/summer",
            "Summer days",
        ),
        ("Audio tracks", "Instrumental music for films.", "/track/21", "Opening scene"),
    ],
    "jobs": [
        (
            "Career opportunities",
            "Explore open positions and join our team.",
            "/careers/position/323",
            "Software engineer",
        ),
        ("Hiring", "Open jobs at our research lab.", "/jobs/252", "Research scientist"),
        (
            "Remote work",
            "Find a remote job in software.",
            "/remote-jobs/engineer",
            "Senior developer",
        ),
        (
            "Vacancies",
            "Apply now for new positions.",
            "/positions/24",
            "Audio producer",
        ),
        (
            "Work with us",
            "Open roles at a comics publisher.",
            "/careers/43",
            "Illustrator",
        ),
        ("Internships", "Summer jobs for students.", "/job/11", "Engineering intern"),
        (
            "Welcome to our company",
            "Browse the current job board.",
            "/jobs/2",
            "Designer",
        ),
    ],
    "software": [
        (
            "Release history",
            "Download software releases and read the changelog.",
            "/releases/tag/v2.4",
            "v2.4.0",
        ),
        (
            "Downloads",
            "All versions of our programming tool.",
            "/downloads/release/12",
            "Download 3.12.2",
        ),
        (
            "Changelog",
            "Bug fixes and new features in each release.",
            "/changelog/1.2.3",
            "Release 1.2.3",
        ),
        ("Release notes", "Read what changed in our app.", "/releases/4.5", "4.5.0"),
        ("Available versions", "Software downloads.", "/releaselog/3.1.html", "3.1.0"),
        ("Our latest releases", "Updates to the desktop client.", "/tags/v5", "v5.0.0"),
        (
            "Version history",
            "Release notes for our podcast editing software.",
            "/releases/tag/2",
            "2.0.0",
        ),
    ],
    "course": [
        ("Weeks", "An introductory computer science course.", "/weeks/0", "Week 0"),
        (
            "Drawing course",
            "Learn with structured lessons.",
            "/lessons/14",
            "Lesson 14",
        ),
        (
            "Course catalog",
            "Find a course to learn a new skill.",
            "/courses/music",
            "Introduction to music",
        ),
        (
            "Syllabus",
            "Weekly lectures, exercises, and quizzes.",
            "/lectures/9",
            "Lecture 9",
        ),
        (
            "Learning paths",
            "Study with expert tutorials and coding exercises.",
            "/tutorials/recursion",
            "Understanding recursion",
        ),
        (
            "Training materials",
            "Online course materials for everyone.",
            "/course/statistics",
            "Statistics",
        ),
        (
            "Curriculum",
            "The complete course on comic drawing.",
            "/lessons/22",
            "Perspective",
        ),
    ],
    "research": [
        (
            "Papers",
            "Journal publications and scientific papers.",
            "/papers/123",
            "Optimal transport",
        ),
        (
            "Proceedings",
            "Research papers from our conference.",
            "/paper/2025/44",
            "A new learning algorithm",
        ),
        (
            "Artificial intelligence",
            "Recent preprints.",
            "/abs/1234.12345",
            "Learning to reason",
        ),
        (
            "Standards and drafts",
            "Technical specifications from our working groups.",
            "/TR/draft-a",
            "Storage standard",
        ),
        (
            "Publications",
            "Our research on music perception.",
            "/publications/42",
            "The science of rhythm",
        ),
        (
            "Journal of computing",
            "Select a volume to view the papers.",
            "/papers/v22",
            "Volume 22",
        ),
        (
            "Research library",
            "Publications about comic reading.",
            "/doi/12",
            "Visual storytelling",
        ),
    ],
    "events": [
        (
            "Contests",
            "Programming competitions and tournaments.",
            "/contest/213",
            "Enter",
        ),
        (
            "Exhibitions",
            "Current and upcoming museum exhibitions.",
            "/exhibitions/water",
            "The Water World",
        ),
        (
            "Events calendar",
            "Find conferences and webinars.",
            "/events/42",
            "Spring meetup",
        ),
        (
            "Upcoming shows",
            "Music events at our concert hall.",
            "/events/abc",
            "Live concert",
        ),
        ("Meetups", "Find local community events.", "/meetups/23", "Monthly gathering"),
        ("Tournaments", "Competitive games and events.", "/contests/25", "Round 25"),
        (
            "Conference calendar",
            "Attend talks and workshops at upcoming conferences.",
            "/event/326",
            "Research conference",
        ),
    ],
    "video": [
        ("Episodes", "Watch this television series.", "/watch/123", "Episode 12"),
        (
            "Video archive",
            "Video recordings of previous talks.",
            "/videos/12",
            "An introduction to rust",
        ),
        (
            "Technical sessions",
            "Watch presentations from our conference.",
            "/presentation/abc",
            "A new operating system",
        ),
        ("Films", "Watch the latest films online.", "/watch/3", "The return"),
        (
            "Channel videos",
            "Our latest video tutorials.",
            "/video/123",
            "Drawing in perspective",
        ),
        ("Recordings", "Video Streaming Portal", "/v/talk-24", "58 min"),
        ("Animated series", "Watch anime episodes here.", "/watch/show/4", "Episode 4"),
    ],
    "blog": [
        (
            "My weblog",
            "Personal notes and essays.",
            "/2025/04/01/hello",
            "A new beginning",
        ),
        (
            "Latest news",
            "News articles and analysis.",
            "/news/123",
            "Important changes",
        ),
        (
            "Essays",
            "Thoughts on science and culture.",
            "/essay/23",
            "The shape of things",
        ),
        (
            "News and releases",
            "Breaking music news and album reviews.",
            "/story/album-news",
            "Three new albums announced",
        ),
        (
            "Magazine",
            "Articles about science and technology.",
            "/articles/42",
            "A research breakthrough",
        ),
        ("Journal", "Welcome to my blog.", "/posts/14", "An eventful day"),
        (
            "Articles",
            "Tutorials and news from our editorial team.",
            "/news/guide",
            "How to create a podcast",
        ),
    ],
    "website": [
        (
            "Community links",
            "A community centered around link aggregation.",
            "https://other.example/story/3",
            "News from around the web",
        ),
        ("Welcome", "", "/item/2", "A useful resource"),
        ("Images", "A gallery of images.", "/images/4", "Ocean sunset"),
        (
            "Bookmarks",
            "Interesting sites from our contributors.",
            "https://external.example/a",
            "A favorite page",
        ),
        (
            "Recent discussions",
            "An aggregator of community links and discussion.",
            "https://elsewhere.example/blog/a",
            "A new article",
        ),
        ("Index", "", "/directory/item", "Resource"),
        (
            "Open collection",
            "A directory of links shared by our community.",
            "https://example-two.example/23",
            "An interesting story",
        ),
    ],
}


def build():
    rows = []
    for label, seeds in SEEDS.items():
        for index, (title, summary, path, entry_title) in enumerate(seeds):
            base = f"https://authored-{label}-{index}.example"
            entries = [
                {
                    "title": entry_title,
                    "url": path
                    if path.startswith("https:")
                    else base + path + f"?sample={number}",
                }
                for number in range(4)
            ]
            for fallback in ("website", "blog"):
                rows.append(
                    dict(
                        source_id=f"authored-{label}-{index}-{fallback}",
                        origin="authored training example, not a public observation",
                        url=base + ("/news" if label == "blog" else "/index"),
                        title=title,
                        source_summary=summary,
                        kind=fallback,
                        entries=entries,
                        label=label,
                    )
                )
    # Counterexamples make subjects such as music, careers, and comics appear in
    # articles rather than being mistaken for the format being tracked.
    for topic in (
        "manga",
        "comics",
        "novels",
        "books",
        "music",
        "albums",
        "podcasts",
        "jobs",
        "careers",
        "research papers",
        "software releases",
        "courses",
        "conferences",
        "video games",
    ):
        for page_format in ("News", "Blog", "Magazine"):
            rows.append(
                dict(
                    source_id=f"authored-topic-{topic}-{page_format}",
                    origin="authored topic/format counterexample",
                    url="https://authored-topic.example/news",
                    title=f"{topic.title()} {page_format}",
                    source_summary=f"News articles about {topic}. Reviews, commentary and analysis from our writers.",
                    kind="blog",
                    entries=[
                        {
                            "url": f"https://authored-topic.example/articles/report-{i}",
                            "title": f"What is changing in {topic} this year",
                        }
                        for i in range(4)
                    ],
                    label="blog",
                )
            )
    return rows


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "datasets/media-curriculum.json"
    path.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf8")
