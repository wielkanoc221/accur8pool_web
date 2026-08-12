from django.urls import path

from . import views

urlpatterns = [
    # Ten sam 'name' dla dwóch wzorców to celowe — Django samo wybierze
    # właściwy w zależności od tego, czy podasz filename w {% url %}.
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/<str:filename>/", views.dashboard, name="dashboard"),
    path("logout", views.logout_view, name="logout"),
    path("datasets/", views.datasets_view, name="datasets"),

    # Endpointy API są klasami: metoda HTTP wybiera operację, a Django
    # rozdziela ją samo (GET/POST, PATCH/DELETE, PUT/DELETE).
    path("api/datasets/",
         views.DatasetListView.as_view(),
         name="api_datasets"),
    path("api/datasets/upload/",
         views.DatasetUploadView.as_view(),
         name="api_upload_dataset"),
    path("api/datasets/<str:filename>/range/",
         views.DatasetRangeView.as_view(),
         name="api_dataset_range"),
    path("api/datasets/<str:filename>/motion3d/",
         views.Motion3DView.as_view(),
         name="api_dataset_motion3d"),
    path("api/datasets/<str:filename>/segments/",
         views.SegmentListView.as_view(),
         name="api_segments"),
    path("api/datasets/<str:filename>/segments/<int:segment_id>/",
         views.SegmentDetailView.as_view(),
         name="api_segment_detail"),
    path("api/datasets/<str:filename>/segments/<int:segment_id>/phases/<str:phase>/",
         views.SegmentPhaseView.as_view(),
         name="api_segment_phase"),
]
