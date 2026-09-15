"""Verify public signup against isolated storage in the production image."""

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_app
from app.tracker.accounts import COOKIE
from app.tracker.models import Entry, Scan


class Listing:
    async def scan(self, url, *args, **kwargs):
        return Scan(url, "Example", entries=[Entry(url + "/one", "One")])


def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tracker.sqlite3"
        config = {
            "required": True,
            "origin": "https://tracker.example",
            "public_signup": True,
        }
        app = create_app(path, Listing(), config)

        def client(target=app):
            return TestClient(
                target, base_url=config["origin"], headers={"Origin": config["origin"]}
            )

        password = "test-only-password"
        with client() as alice, client() as bob:
            status = alice.get("/api/auth/status").json()
            assert status["registration"] and not status["invite_required"]
            assert alice.get("/api/items").status_code == 401
            for c, name in ((alice, "alice"), (bob, "bob")):
                response = c.post(
                    "/api/auth/register", json={"username": name, "password": password}
                )
                assert response.status_code == 201, response.text
                assert all(
                    flag in response.headers["set-cookie"]
                    for flag in ("HttpOnly", "Secure", "SameSite=lax")
                )
            scan = alice.post("/api/scans", json={"url": "https://example.org"}).json()
            item = alice.post("/api/items", json={"scan_id": scan["scan_id"]}).json()
            assert bob.get("/api/items").json() == []
            assert bob.get(f"/api/items/{item['id']}").status_code == 404
            exported = alice.post(
                "/api/auth/export", json={"current_password": password}
            )
            assert "trackify-export.json" in exported.headers["content-disposition"]
            assert len(exported.json()["links"]) == 1
            assert (
                bob.post(
                    "/api/auth/delete",
                    json={"current_password": password, "confirmation": "DELETE"},
                ).status_code
                == 200
            )
            assert alice.get(f"/api/items/{item['id']}").status_code == 200
            token = alice.cookies.get(COOKIE)
        restarted = create_app(path, Listing(), config)
        with client(restarted) as c:
            c.cookies.set(COOKIE, token)
            assert c.get(f"/api/items/{item['id']}").status_code == 200
            assert (
                c.post(
                    "/api/auth/register",
                    json={"username": "carol", "password": password},
                ).status_code
                == 201
            )
            assert (
                c.post(
                    "/api/auth/register",
                    json={"username": "dennis", "password": password},
                ).status_code
                == 429
            )
            assert (
                c.post(
                    "/api/auth/login", json={"username": "alice", "password": password}
                ).status_code
                == 200
            )
        print(
            json.dumps(
                {
                    "public_signup": "passed",
                    "account_isolation": "passed",
                    "export_and_erasure": "passed",
                    "persistent_signup_limit": "passed",
                    "existing_session": "passed",
                }
            )
        )


if __name__ == "__main__":
    main()
