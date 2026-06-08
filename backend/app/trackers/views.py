import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Entry, Tracker
from .services import refresh_tracker


def ok(data=None, **extra):
    payload = {"success": True, "data": data}
    payload.update(extra)
    return JsonResponse(payload)


def error(message, status=400):
    return JsonResponse({"success": False, "error": message}, status=status)


def tracker_json(tracker, include_entries=False):
    entries = list(tracker.entries.all())
    data = {
        "id": tracker.id,
        "name": tracker.name,
        "sourceUrl": tracker.source_url,
        "currentUrl": tracker.current_url,
        "targetUrl": tracker.target_url,
        "checkIntervalMinutes": tracker.check_interval_minutes,
        "lastCheckedAt": tracker.last_checked_at.isoformat() if tracker.last_checked_at else None,
        "entryCount": len(entries),
        "newCount": sum(1 for entry in entries if entry.is_new),
        "isDue": tracker.is_due,
    }
    if include_entries:
        data["entries"] = [entry_json(entry) for entry in entries]
    return data


def entry_json(entry):
    return {
        "id": entry.id,
        "title": entry.title,
        "url": entry.url,
        "summary": entry.summary,
        "isNew": entry.is_new,
        "firstSeenAt": entry.first_seen_at.isoformat(),
    }


def read_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def require_login(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error("Authentication required.", status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


def user_json(user):
    return {"id": user.id, "email": user.email, "username": user.username}


@csrf_exempt
@require_http_methods(["POST"])
def create_account(request):
    body = read_body(request)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return error("Email and password are required.")
    if User.objects.filter(username=email).exists():
        return error("An account with that email already exists.", status=409)

    user = User.objects.create_user(username=email, email=email, password=password)
    return ok(user_json(user))


@csrf_exempt
@require_http_methods(["POST"])
def sign_in(request):
    body = read_body(request)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = authenticate(request, username=email, password=password)

    if user is None:
        return error("Invalid email or password.", status=401)

    login(request, user)
    return ok(user_json(user))


@csrf_exempt
@require_http_methods(["POST"])
def sign_out(request):
    logout(request)
    return ok()


@require_http_methods(["GET"])
def current_user(request):
    if not request.user.is_authenticated:
        return ok(None, authenticated=False)
    return ok(user_json(request.user), authenticated=True)


@require_http_methods(["GET"])
@require_login
def list_trackers(request):
    trackers = Tracker.objects.filter(owner=request.user)
    for tracker in trackers:
        if tracker.is_due:
            try:
                refresh_tracker(tracker)
            except Exception:
                pass
    return ok([tracker_json(tracker) for tracker in trackers])


@csrf_exempt
@require_http_methods(["POST"])
@require_login
def create_tracker(request):
    body = read_body(request)
    name = (body.get("name") or "").strip()
    source_url = (body.get("sourceUrl") or body.get("originUrl") or "").strip()
    current_url = (body.get("currentUrl") or body.get("currUrl") or "").strip()
    try:
        interval = int(body.get("checkIntervalMinutes") or 60)
    except (TypeError, ValueError):
        return error("Check interval must be a whole number.")

    if not name:
        return error("Tracker name is required.")
    if not source_url:
        return error("Source URL is required.")
    if interval < 1:
        return error("Check interval must be at least one minute.")

    validator = URLValidator(schemes=["http", "https"])
    try:
        validator(source_url)
        if current_url:
            validator(current_url)
    except ValidationError:
        return error("URLs must start with http:// or https://.")

    tracker = Tracker.objects.create(
        owner=request.user,
        name=name,
        source_url=source_url,
        current_url=current_url,
        check_interval_minutes=interval,
    )
    try:
        created_entries = refresh_tracker(tracker)
    except Exception as exc:
        tracker.delete()
        return error(f"Could not read the listing page: {exc}")
    return ok(tracker_json(tracker, include_entries=True), newEntries=len(created_entries))


@require_http_methods(["GET"])
@require_login
def tracker_detail(request, tracker_id):
    tracker = get_object_or_404(Tracker, id=tracker_id, owner=request.user)
    if tracker.is_due:
        try:
            refresh_tracker(tracker)
        except Exception:
            pass
    return ok(tracker_json(tracker, include_entries=True))


@csrf_exempt
@require_http_methods(["POST"])
@require_login
def refresh_tracker_view(request, tracker_id):
    tracker = get_object_or_404(Tracker, id=tracker_id, owner=request.user)
    try:
        created_entries = refresh_tracker(tracker)
    except Exception as exc:
        return error(f"Could not refresh tracker: {exc}")
    return ok(tracker_json(tracker, include_entries=True), newEntries=len(created_entries))


@csrf_exempt
@require_http_methods(["POST"])
@require_login
def mark_entries_seen(request, tracker_id):
    tracker = get_object_or_404(Tracker, id=tracker_id, owner=request.user)
    Entry.objects.filter(tracker=tracker, is_new=True).update(is_new=False)
    return ok(tracker_json(tracker, include_entries=True))
