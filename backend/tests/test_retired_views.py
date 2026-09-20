import json
import sqlite3
from contextlib import closing

from app.main import create_app
from app.tracker.accounts import COOKIE
from tests.test_accounts import CONFIG, PASSWORD, client, sign_up
from tests.test_store_api import FakeDiscoverer


def test_retired_saved_views_are_not_exposed_but_still_exported_and_erased(tmp_path):
    app = create_app(tmp_path / "library.db", FakeDiscoverer(), CONFIG)
    alice = client(app)
    sign_up(alice, "alice")
    store = app.state.accounts.store(app.state.accounts.user(alice.cookies.get(COOKIE)))
    with store.connection() as db:
        db.execute(
            "INSERT INTO saved_views VALUES (?,?,?,?)",
            (
                "legacy",
                "Old view",
                json.dumps({"query": "private phrase"}),
                "2026-01-01",
            ),
        )
    assert alice.get("/api/views").status_code == 404
    assert alice.post("/api/views", json={"name": "New"}).status_code in {404, 405}
    exported = alice.post(
        "/api/auth/export", json={"current_password": PASSWORD}
    ).json()
    assert exported["saved_views"][0]["id"] == "legacy"
    response = alice.post(
        "/api/auth/delete",
        json={"current_password": PASSWORD, "confirmation": "DELETE"},
    )
    assert response.status_code == 200 and not response.json()["cleanup_pending"]
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM saved_views").fetchone()[0] == 0
