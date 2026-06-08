import json

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from unittest.mock import patch

from .models import Tracker
from .services import extract_links


class ExtractLinksTests(SimpleTestCase):
    def test_accepts_common_media_release_url_formats(self):
        cases = [
            (
                "https://medium.com/tag/technology",
                '<a href="/machina-speculatrix/life-with-a-dot-matrix-printer-abcdef123456">Life with a dot matrix printer</a>',
                "https://medium.com/machina-speculatrix/life-with-a-dot-matrix-printer-abcdef123456",
            ),
            (
                "https://wordpress.org/news/",
                '<a href="/news/2026/06/wceu-2026-recap/">What Happened at WordCamp Europe 2026</a>',
                "https://wordpress.org/news/2026/06/wceu-2026-recap/",
            ),
            (
                "https://entropymine.wordpress.com/",
                '<a href="/2026/04/05/pklite-supplement-6-min-max-alloc-part-1/">PKLITE supplement 6</a>',
                "https://entropymine.wordpress.com/2026/04/05/pklite-supplement-6-min-max-alloc-part-1/",
            ),
            (
                "https://www.tor.com/category/all-fiction/",
                '<a href="/2026/05/28/the-last-contract/">The Last Contract</a>',
                "https://www.tor.com/2026/05/28/the-last-contract/",
            ),
            (
                "https://www.royalroad.com/fictions/latest-updates",
                '<a href="/fiction/12345/sample-fiction/chapter/987654/chapter-09-games-in-the-garden">Chapter 09 - Games in the Garden</a>',
                "https://www.royalroad.com/fiction/12345/sample-fiction/chapter/987654/chapter-09-games-in-the-garden",
            ),
            (
                "https://woopread.com/series/shepherd-wizard",
                '<a href="/series/shepherd-wizard/chapter-152.2">Chapter 152.2</a>',
                "https://woopread.com/series/shepherd-wizard/chapter-152.2",
            ),
            (
                "https://www.webtoons.com/en/updates",
                '<a href="/en/canvas/no/chronicle-of-the-three-kings/viewer?episode_no=61&title_no=37151">Chronicle of the three kings</a>',
                "https://www.webtoons.com/en/canvas/no/chronicle-of-the-three-kings/viewer?episode_no=61&title_no=37151",
            ),
        ]

        for page_url, html, expected_url in cases:
            with self.subTest(expected_url=expected_url):
                links = extract_links(page_url, html)
                self.assertEqual([link.url for link in links], [expected_url])

    def test_rejects_navigation_taxonomy_profile_and_asset_links(self):
        html = """
            <a href="/tag/technology">Technology</a>
            <a href="/@writer">Writer Profile</a>
            <a href="/news/category/releases/">Releases</a>
            <a href="/fiction/12345/sample-fiction">Fiction landing page</a>
            <a href="/en/canvas/no/list?title_no=37151">Series landing page</a>
            <a href="/image/cover.jpg">Cover image</a>
            <a href="https://external.example.com/2026/06/08/story/">External story</a>
        """

        self.assertEqual(extract_links("https://medium.com/tag/technology", html), [])

    def test_extracts_real_links_from_embedded_show_all_data_before_fallback(self):
        html = """
            <html>
              <body>
                <button>Show All 4 Chapters</button>
                <p>4 chapters</p>
                <a href="/series/shepherd-wizard/chapter-1">Chapter 1</a>
                <a href="/series/shepherd-wizard/chapter-2">Chapter 2</a>
                <script>
                  window.__chapters = {
                    "special": "https:\\/\\/woopread.com\\/series\\/shepherd-wizard\\/chapter-152.2",
                    "latest": "/series/shepherd-wizard/chapter-1552"
                  };
                </script>
              </body>
            </html>
        """

        links = extract_links("https://woopread.com/series/shepherd-wizard", html)
        urls = [link.url for link in links]

        self.assertIn("https://woopread.com/series/shepherd-wizard/chapter-152.2", urls)
        self.assertIn("https://woopread.com/series/shepherd-wizard/chapter-1552", urls)
        self.assertNotIn("https://woopread.com/series/shepherd-wizard/chapter-3", urls)
        self.assertNotIn("https://woopread.com/series/shepherd-wizard/chapter-4", urls)

    def test_does_not_infer_numbered_chapters_without_expand_signal(self):
        html = """
            <p>4 chapters</p>
            <a href="/series/shepherd-wizard/chapter-1">Chapter 1</a>
            <a href="/series/shepherd-wizard/chapter-2">Chapter 2</a>
        """

        links = extract_links("https://woopread.com/series/shepherd-wizard", html)

        self.assertEqual(
            [link.url for link in links],
            [
                "https://woopread.com/series/shepherd-wizard/chapter-1",
                "https://woopread.com/series/shepherd-wizard/chapter-2",
            ],
        )


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = Client()

    def post_json(self, path, body):
        return self.client.post(
            path,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_signup_login_and_current_user(self):
        signup = self.post_json(
            "/api/user/create-account",
            {"email": "reader@example.com", "password": "strong-pass-123"},
        )
        self.assertEqual(signup.status_code, 200)
        self.assertTrue(signup.json()["success"])

        login = self.post_json(
            "/api/user/sign-in",
            {"email": "reader@example.com", "password": "strong-pass-123"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["success"])

        current_user = self.client.get("/api/user/me")
        self.assertTrue(current_user.json()["authenticated"])
        self.assertEqual(current_user.json()["data"]["email"], "reader@example.com")

    def test_tracker_routes_require_login(self):
        response = self.client.get("/api/trackers/")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["success"])

    @patch("trackers.views.refresh_tracker")
    def test_trackers_are_scoped_to_logged_in_user(self, mocked_refresh):
        mocked_refresh.return_value = []
        first = User.objects.create_user(
            username="first@example.com",
            email="first@example.com",
            password="password",
        )
        second = User.objects.create_user(
            username="second@example.com",
            email="second@example.com",
            password="password",
        )
        Tracker.objects.create(
            owner=second,
            name="Other user tracker",
            source_url="https://example.com/2026/06/story/",
        )

        self.client.force_login(first)
        create_response = self.post_json(
            "/api/trackers/create/",
            {
                "name": "My tracker",
                "sourceUrl": "https://example.com/2026/06/story/",
                "checkIntervalMinutes": 60,
            },
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertTrue(create_response.json()["success"])

        list_response = self.client.get("/api/trackers/")
        tracker_names = [tracker["name"] for tracker in list_response.json()["data"]]

        self.assertEqual(tracker_names, ["My tracker"])
        mocked_refresh.assert_called()
