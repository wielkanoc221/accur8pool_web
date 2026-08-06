from django.contrib import admin

from .models import Dataset, Segment, SubSegment


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("filename", "owner", "uploaded_at")
    list_filter = ("owner",)
    search_fields = ("filename", "owner__username")
    ordering = ("-uploaded_at",)
    readonly_fields = ("uploaded_at",)


class SubSegmentInline(admin.TabularInline):
    model = SubSegment
    extra = 0
    fields = ("phase", "start", "end")


@admin.register(Segment)
class SegmentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "dataset", "start", "end", "length", "created_at")
    list_filter = ("dataset__owner",)
    search_fields = ("dataset__filename",)
    readonly_fields = ("created_at",)
    inlines = [SubSegmentInline]


@admin.register(SubSegment)
class SubSegmentAdmin(admin.ModelAdmin):
    list_display = ("phase", "segment", "start", "end", "length")
    list_filter = ("phase",)
    readonly_fields = ("created_at",)
