import django.urls
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
    path("api/datasets/<str:filename>/range/", views.api_dataset_range, name="api_dataset_range")
]