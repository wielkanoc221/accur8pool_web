from django.contrib import admin

# Register your models here.
from django.contrib import admin

from .models import Dataset


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("filename", "owner", "uploaded_at")
    list_filter = ("owner",)
    search_fields = ("filename", "owner__username")
    ordering = ("-uploaded_at",)
    readonly_fields = ("uploaded_at",)
