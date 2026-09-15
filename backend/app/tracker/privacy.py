"""Public deployment details used by the privacy notice; no secret settings."""

import os

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/api/privacy")
def privacy_details():
    return {
        "operator": os.environ.get("TRACKER_OPERATOR_NAME", "Trackify"),
        "contact": os.environ.get("TRACKER_PRIVACY_EMAIL", ""),
        "hosting": os.environ.get(
            "TRACKER_HOSTING_DESCRIPTION", "The site operator's hosting provider"
        ),
        "updated": "2026-09-15",
    }


@router.get("/.well-known/security.txt", response_class=PlainTextResponse)
def security_contact():
    contact = os.environ.get("TRACKER_PRIVACY_EMAIL", "")
    if not contact:
        return PlainTextResponse(
            "Security contact is not configured.\n", status_code=503
        )
    return f"Contact: mailto:{contact}\nExpires: 2027-03-14T00:00:00Z\nPreferred-Languages: en\n"
