from django.conf import settings
from django.db import models


class Dataset(models.Model):
    """Właściciel pliku CSV leżącego w DATA_DIR/<owner_id>/<filename>.
    Sama zawartość pliku NIE jest w bazie — tu trzymamy tylko kto jest
    jego właścicielem, pod jego oryginalną nazwą."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="datasets",
    )
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        unique_together = [("owner", "filename")]

    def __str__(self):
        return f"{self.filename} ({self.owner})"
