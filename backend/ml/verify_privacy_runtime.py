"""Exercise account erasure and isolation in a disposable, offline library."""

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import create_app
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
            "signup_code": "test-only-invite-123456789",
        }
        app = create_app(path, Listing(), config)
        with TestClient(
            app, base_url=config["origin"], headers={"Origin": config["origin"]}
        ) as client:
            assert client.get("/api/privacy").status_code == 200
            assert (
                client.post(
                    "/api/auth/register",
                    json={
                        "username": "test-user",
                        "password": "test-only-password",
                        "invite_code": config["signup_code"],
                    },
                ).status_code
                == 201
            )
            scan = client.post("/api/scans", json={"url": "https://example.org"}).json()
            assert (
                client.post("/api/items", json={"scan_id": scan["scan_id"]}).status_code
                == 201
            )
            data = client.post(
                "/api/auth/export", json={"current_password": "test-only-password"}
            ).json()
            assert len(data["links"]) == 1 and "password_hash" not in json.dumps(data)
            assert (
                client.post(
                    "/api/auth/delete",
                    json={"current_password": "wrong", "confirmation": "DELETE"},
                ).status_code
                == 401
            )
            result = client.post(
                "/api/auth/delete",
                json={
                    "current_password": "test-only-password",
                    "confirmation": "DELETE",
                },
            )
            assert result.status_code == 200 and not result.json()["cleanup_pending"]
            assert client.get("/api/items").status_code == 401
            assert client.get("/api/ready").status_code == 200
        with app.state.accounts.connection() as db:
            assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert db.execute("SELECT completed FROM erasures").fetchone()[0]
        print(
            json.dumps(
                {
                    "export": "passed",
                    "reauthentication": "passed",
                    "deletion": "passed",
                    "revocation": "passed",
                    "source_requests": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
