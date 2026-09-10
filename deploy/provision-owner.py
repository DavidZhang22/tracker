"""Initialize a closed deployment's first owner without printing credentials.

Run with the app's Python inside the container, before enabling public access.
The temporary password is written to /data/.bootstrap-owner.json with mode 0600.
"""

import json
import os
import secrets
from pathlib import Path

from app.tracker.accounts import Accounts
from app.tracker.store import Store

os.umask(0o077)
path = Path(os.environ["TRACKER_DB"])
Store(path)
accounts = Accounts(path)
with accounts.connection() as db:
    count = db.execute("SELECT count(*) FROM users").fetchone()[0]
if count:
    raise SystemExit("An owner already exists; no credentials were changed.")
secret_path = path.parent / ".bootstrap-owner.json"
password = secrets.token_urlsafe(24)
# Exclusive creation prevents accidentally replacing a previous handoff file.
with secret_path.open("x") as handoff:
    json.dump(
        {
            "url": os.environ["TRACKER_ORIGIN"],
            "username": "david",
            "password": password,
        },
        handoff,
        indent=2,
    )
accounts.create("david", password, claim_existing=True)
print("Owner created. Credentials saved privately; registration remains closed.")
