"""Exercise CSV uploads through real HTTP in an isolated production container."""

import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn


def main():
    from app.main import create_app

    with tempfile.TemporaryDirectory() as directory:
        app = create_app(
            Path(directory) / "csv.sqlite3", auth_config={"required": False}
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        try:
            for _ in range(1000):
                if server.started:
                    break
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=30
            ) as client:
                data = (
                    "URL,Title,Posted_or_Deadline,Notes\n"
                    + "".join(
                        f"https://example.org/{i},Entry {i},Posted 2026-09-01,{'details ' * 20}\n"
                        for i in range(4999)
                    )
                ).encode()
                assert len(data) > 256_000

                def preview(**params):
                    response = client.post(
                        "/api/scans/csv",
                        params={"filename": "test.csv", **params},
                        content=data,
                        headers={"Content-Type": "text/csv"},
                    )
                    assert response.status_code == 200, response.text[:300]
                    return response.json()

                scanned = preview()
                assert len(scanned["entries"]) == 4999 and scanned["requests_made"] == 0
                response = client.post(
                    "/api/items",
                    json={"scan_id": scanned["scan_id"], "read_indices": [0, 10]},
                )
                assert response.status_code == 201, response.text[:300]
                item = response.json()
                assert item["source_type"] == "csv" and item["read_count"] == 2
                scanned = preview(item_id=item["id"])
                response = client.post(
                    f"/api/items/{item['id']}/import",
                    json={"scan_id": scanned["scan_id"]},
                )
                assert response.status_code == 200, response.text[:300]
                assert (
                    response.json()["total_count"] == 4999
                    and response.json()["read_count"] == 2
                )
                assert client.post("/api/refresh").json()["checked"] == 0
                assert client.get("/api/suggestions").status_code == 200
                assert (
                    client.post(
                        "/api/scans/csv",
                        content=b"x" * 4_000_001,
                        headers={"Content-Type": "text/csv"},
                    ).status_code
                    == 413
                )
                assert (
                    client.post("/api/scans", content=b"x" * 100_000).status_code == 413
                )
                print(
                    json.dumps(
                        {
                            "csv_http": "passed",
                            "links": 4999,
                            "reupload_duplicates": 0,
                            "read_states_preserved": 2,
                            "external_requests": 0,
                            "upload_bytes": len(data),
                        }
                    )
                )
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


if __name__ == "__main__":
    main()
