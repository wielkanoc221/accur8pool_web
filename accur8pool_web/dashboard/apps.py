from django.apps import AppConfig


class TestboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"

    def ready(self):
        # Sam import podpina odbiorniki sygnałów (dashboard/signals.py).
        from . import signals  # noqa: F401
