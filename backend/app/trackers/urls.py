from django.urls import path

from . import views

urlpatterns = [
    path("user/create-account", views.create_account, name="create-account"),
    path("user/sign-in", views.sign_in, name="sign-in"),
    path("user/sign-out", views.sign_out, name="sign-out"),
    path("user/me", views.current_user, name="current-user"),
    path("trackers/", views.list_trackers, name="list-trackers"),
    path("trackers/create/", views.create_tracker, name="create-tracker"),
    path("trackers/<int:tracker_id>/", views.tracker_detail, name="tracker-detail"),
    path("trackers/<int:tracker_id>/refresh/", views.refresh_tracker_view, name="refresh-tracker"),
    path("trackers/<int:tracker_id>/seen/", views.mark_entries_seen, name="mark-entries-seen"),
    path("item/get-items", views.list_trackers, name="legacy-list-trackers"),
    path("item/create-item", views.create_tracker, name="legacy-create-tracker"),
]
