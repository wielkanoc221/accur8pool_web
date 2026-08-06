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


class Segment(models.Model):
    """Wycinek nagrania — u nas jedno uderzenie w bilardzie.

    Granice trzymamy jako NUMERY WIERSZY CSV, nie jako sekundy. Taka jest
    oś X wykresu i taka jest naturalna rozdzielczość pliku; przeliczenie
    na czas wymagałoby znajomości częstotliwości próbkowania, której na
    tym etapie nie zapisujemy.

    Nie ma tu pola z nazwą. Nazwa segmentu to jego kolejny numer i jest
    LICZONA przy odczycie, z pozycji na osi czasu (views._segments_payload).
    Dzięki temu lista zawsze idzie 1, 2, 3… bez dziur po usunięciu
    segmentu ze środka i bez numeru 7 stojącego przed numerem 3, gdy
    uderzenia zostały zaznaczone w innej kolejności niż występują.
    Gdyby kiedyś doszły nazwy własne, wystarczy dodać pole `label`
    i użyć go tam, gdzie jest niepuste.
    """

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Kolejność po pozycji na osi czasu — z niej bierze się numeracja.
        # pk jako drugie kryterium, żeby dwa segmenty o tym samym starcie
        # nie zamieniały się miejscami między żądaniami.
        ordering = ["start", "pk"]

    def __str__(self):
        return f"{self.dataset.filename}: {self.start}–{self.end}"

    @property
    def length(self):
        return self.end - self.start


class SubSegment(models.Model):
    """Faza wewnątrz segmentu uderzenia.

    Uderzenie rozkłada się na cztery fazy i każda może wystąpić tylko raz
    (unique_together). Dzięki temu panel pokazuje po prostu cztery
    wiersze — ustawione albo puste — a ponowne zaznaczenie tej samej fazy
    poprawia jej zakres, zamiast tworzyć duplikat.
    """

    PHASE_PREPARATION = "przygotowanie"
    PHASE_ADDRESSING = "przymierzanie"
    PHASE_STRIKE = "uderzenie"
    PHASE_FOLLOW_THROUGH = "po_uderzeniu"

    # Kolejność na tej liście to kolejność faz w czasie — używana zarówno
    # do sortowania w panelu, jak i do walidacji nazwy fazy z URL-a.
    PHASE_CHOICES = [
        (PHASE_PREPARATION, "Przygotowanie"),
        (PHASE_ADDRESSING, "Przymierzanie"),
        (PHASE_STRIKE, "Uderzenie"),
        (PHASE_FOLLOW_THROUGH, "Po uderzeniu"),
    ]

    PHASE_ORDER = [key for key, _ in PHASE_CHOICES]

    segment = models.ForeignKey(
        Segment,
        on_delete=models.CASCADE,
        related_name="subsegments",
    )
    phase = models.CharField(max_length=32, choices=PHASE_CHOICES)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start", "pk"]
        unique_together = [("segment", "phase")]

    def __str__(self):
        return f"{self.get_phase_display()} ({self.start}–{self.end})"

    @property
    def length(self):
        return self.end - self.start
