from fastapi.testclient import TestClient

from app import main
from tests.test_store_api import FakeDiscoverer


def test_only_successful_fingerprinted_assets_are_immutable(tmp_path, monkeypatch):
    build = tmp_path / "frontend/build"
    static = build / "static"
    static.mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html><title>Trackify</title>")
    (static / "index-Ab_cd-12.js").write_text("export const version = 1;")
    (static / "index-Ab_cd-12.css").write_text("body {margin: 0;}")
    (static / "plain.js").write_text("export const version = 1;")
    monkeypatch.setattr(main, "__file__", str(tmp_path / "backend/app/main.py"))
    app = main.create_app(tmp_path / "library.db", FakeDiscoverer())
    with TestClient(app) as client:
        for suffix in ["js", "css"]:
            response = client.get(f"/static/index-Ab_cd-12.{suffix}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
            revalidated = client.get(
                f"/static/index-Ab_cd-12.{suffix}",
                headers={"If-None-Match": response.headers["etag"]},
            )
            assert revalidated.status_code == 304
            assert "immutable" in revalidated.headers["cache-control"]
        for path in ["/", "/settings", "/index.html", "/api/auth/status", "/api/items"]:
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        for path in ["/static/plain.js", "/static/missing-Ab_cd-12.js"]:
            assert "immutable" not in client.get(path).headers.get("cache-control", "")
