from django.urls import path

from . import views

urlpatterns = [
    # Ten sam 'name' dla dwóch wzorców to celowe — Django samo wybierze
    # właściwy w zależności od tego, czy podasz filename w {% url %}.
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/<str:filename>/", views.dashboard, name="dashboard"),
    path('logout', views.logout_view, name='logout'),
    path("datasets/", views.datasets_view, name="datasets"),
    path("api/datasets/", views.api_datasets, name="api_datasets"),
    path("api/datasets/upload/", views.upload_dataset, name="api_upload_dataset"),
    path("api/datasets/<str:filename>/range/", views.api_dataset_range, name="api_dataset_range"),
    path("api/datasets/<str:filename>/motion3d/", views.api_dataset_motion3d, name="api_dataset_motion3d"),

    # Segmenty i fazy. Metoda HTTP wybiera operację (GET/POST oraz
    # PATCH/DELETE/PUT), dlatego jeden wzorzec obsługuje kilka akcji —
    # rozgałęzienie jest w widoku.
    path(
        "api/datasets/<str:filename>/segments/",
        views.api_segments,
        name="api_segments",
    ),
    path(
        "api/datasets/<str:filename>/segments/<int:segment_id>/",
        views.api_segment_detail,
        name="api_segment_detail",
    ),
    path(
        "api/datasets/<str:filename>/segments/<int:segment_id>/phases/<str:phase>/",
        views.api_segment_phase,
        name="api_segment_phase",
    ),
]