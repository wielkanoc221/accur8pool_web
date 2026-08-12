"""Widoki dashboardu — cienka warstwa nad HTTP.

Każdy widok robi dokładnie trzy rzeczy: sprawdza wejście, woła jedną
usługę i zamienia jej wynik na odpowiedź. Cała reszta ma swoje miejsce:

    storage.py         pliki użytkownika (raw_data / prepared_data)
    series.py          wczytanie kolumn i decymacja do rozdzielczości ekranu
    charts.py          wygląd wykresu 2D
    segments.py        segmenty, fazy i walidacja zaznaczenia
    motion_service.py  parametry i pamięć podręczna animacji 3D
    motion3d/          sama rekonstrukcja ruchu

Endpointy API są klasami, bo każdy z nich obsługuje kilka metod HTTP —
Django rozdziela je samo, więc znika ręczne rozgałęzianie po
request.method i powtarzane HttpResponseNotAllowed. Strony (dashboard,
lista zestawów, wylogowanie) zostają funkcjami: nie mają czego dzielić.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from . import charts, motion3d
from .models import Dataset, Segment, SubSegment
from .motion_service import MotionOptions, scene_for_segment
from .segments import (
    RowRange,
    phase_types_payload,
    segment_range,
    segments_payload,
    trim_phases_to_segment,
)
from .series import TARGET_BUCKETS, Series
from .storage import DatasetStorage, dataset_meta

logger = logging.getLogger(__name__)

# Surowe pliki tak, jak przyszły z uploadu. To jest WYŁĄCZNIE wejście dla
# przygotowania danych — żaden widok już z tego drzewa nie czyta.
DATA_DIR = Path(
    getattr(settings, "ACCUR8POOL_DATA_DIR",
            Path(settings.BASE_DIR) / "new_data" / "raw_data")
)

# Te same pliki po transform_raw_df — pod tą samą nazwą i tym samym
# <user_id>, tyle że w drugim drzewie. TO JEST JEDYNE ŹRÓDŁO DANYCH dla
# aplikacji: wykres 2D, doczytywanie zakresów i animacja 3D widzą dokładnie
# ten sam plik.
PREPARED_DATA_DIR = Path(
    getattr(settings, "ACCUR8POOL_PREPARED_DATA_DIR",
            Path(settings.BASE_DIR) / "new_data" / "prepared_data")
)

MAX_UPLOAD_SIZE = 300 * 1024 * 1024

# Granice liczby kubełków, o którą może poprosić przeglądarka. Dolna, żeby
# wykres nie zrobił się schodkowy; górna, żeby nie wysyłać więcej punktów,
# niż ma pikseli najszerszy monitor.
MIN_BUCKETS, MAX_BUCKETS = 200, 6000

# Margines doczytywania: użytkownik przesuwa wykres, więc bierzemy trochę
# poza widoczny zakres — inaczej każde drgnięcie myszą to nowe żądanie.
RANGE_MARGIN = 0.15


def storage_for(user) -> DatasetStorage:
    """Katalogi czytamy przy KAŻDYM żądaniu, a nie raz przy imporcie —
    dzięki temu testy podmieniają je jednym mock.patch.object(views, …)."""
    return DatasetStorage(user, DATA_DIR, PREPARED_DATA_DIR)


# ============================================================
#  STRONY
# ============================================================

@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
@ensure_csrf_cookie
def dashboard(request, filename=None):
    """Wykres 2D wybranego zestawu — albo powód, dla którego go nie ma."""
    storage = storage_for(request.user)
    dataset = storage.resolve(filename)

    if dataset is None:
        return _dashboard_page(
            request,
            error=(f"Nie znaleziono pliku: {filename}" if filename else None),
        )

    path = storage.path_for(dataset)
    if not path.exists():
        logger.warning("Brak pliku na dysku: %s (dataset id=%s)", path, dataset.pk)
        return _dashboard_page(request, dataset=dataset,
                               error="Plik nie istnieje na serwerze. Prześlij go ponownie.")

    try:
        series = Series.from_csv(path)
    except pd.errors.EmptyDataError:
        return _dashboard_page(request, dataset=dataset,
                               error="Plik CSV nie zawiera danych.")
    except Exception:
        logger.exception("Nie udało się wczytać CSV: %s", path)
        return _dashboard_page(request, dataset=dataset,
                               error="Nie udało się odczytać pliku CSV.")

    if not series:
        return _dashboard_page(request, dataset=dataset, columns_count=0,
                               error="Plik nie zawiera kolumn liczbowych do narysowania.")

    return _dashboard_page(
        request,
        dataset=dataset,
        graph_data=charts.build_figure(series).to_plotly_json(),
        columns_count=len(series.names),
        total_points=series.rows,
        # Same nazwy kolumn wystarczą, żeby wiedzieć, czy zakładka 3D ma
        # sens — nie ruszamy dysku drugi raz tylko po to pytanie.
        motion3d_ready=motion3d.supports(series.names),
    )


def _dashboard_page(request, dataset=None, graph_data=None, columns_count=None,
                    total_points=None, error=None, motion3d_ready=False):
    return render(request, "dashboard.html", {
        "dataset": dataset_meta(dataset) if dataset else None,
        "graph_data": graph_data,
        "columns_count": columns_count,
        "total_points": total_points,
        "error": error,
        "motion3d_ready": motion3d_ready,
    })


@login_required
@ensure_csrf_cookie
def datasets_view(request):
    return render(request, "datasets.html")


# ============================================================
#  WSPÓLNA CZĘŚĆ ENDPOINTÓW API
# ============================================================

class ApiView(LoginRequiredMixin, View):
    """Widok zwracający JSON, dostępny tylko po zalogowaniu."""

    @staticmethod
    def error(message: str, status: int) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)

    @staticmethod
    def json_body(request):
        """Ciało żądania jako dict. None = nie da się sparsować."""
        try:
            payload = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


class DatasetApiView(ApiView):
    """Endpoint działający na JEDNYM zestawie danych z URL-a.

    Rozwiązanie nazwy pliku i sprawdzenie właściciela idzie raz, w dispatch:
    zestaw cudzy albo bez wersji przygotowanej kończy się tu 404 i żadna
    metoda go nie zobaczy.
    """

    def dispatch(self, request, *args, **kwargs):
        # Kolejność jest istotna: zapytanie bazy o AnonymousUser wywraca
        # się na typie, więc brak logowania rozstrzygamy przed odczytem.
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        self.storage = storage_for(request.user)
        self.dataset = self.storage.resolve(kwargs.get("filename"))
        if self.dataset is None:
            return self.error("Nie znaleziono pliku.", 404)

        return super().dispatch(request, *args, **kwargs)

    def dataset_path(self):
        """Ścieżka do pliku albo None, gdy zniknął z dysku między żądaniami."""
        path = self.storage.path_for(self.dataset)
        return path if path.exists() else None

    def segments_response(self, status: int = 200) -> JsonResponse:
        """PEŁNA lista segmentów — także po zapisie i po usunięciu.

        Numer segmentu nie jest zapisany w bazie, tylko wyliczony
        z kolejności na osi czasu, więc dodanie uderzenia w środku nagrania
        przenumerowuje wszystkie późniejsze. Zwracanie całej listy jest tu
        tańsze niż powtarzanie tej samej logiki w przeglądarce, a lista ma
        rozmiar kilkudziesięciu rekordów, nie kilkudziesięciu tysięcy.
        """
        return JsonResponse({
            "segments": segments_payload(self.dataset),
            "phase_types": phase_types_payload(),
        }, status=status)

    def parsed_range(self, request):
        """(RowRange, None) albo (None, odpowiedź z błędem)."""
        payload = self.json_body(request)
        if payload is None:
            return None, self.error("Nieprawidłowe dane żądania.", 400)

        bounds, message = RowRange.parse(payload)
        if message:
            return None, self.error(message, 400)
        return bounds, None


class SegmentApiView(DatasetApiView):
    """Endpoint działający na JEDNYM segmencie tego zestawu.

    Segment jest szukany zawsze w obrębie datasetu użytkownika, więc samo
    podanie obcego id nic nie daje.
    """

    def load_segment(self, segment_id):
        return Segment.objects.filter(dataset=self.dataset, pk=segment_id).first()


# ============================================================
#  ZESTAWY DANYCH
# ============================================================

class DatasetListView(ApiView):
    """Lista zestawów do sidebaru i do strony „Zestawy danych”.

    Wchodzą TYLKO te, które mają wersję przygotowaną — bo tylko takie da
    się otworzyć. Zestaw bez niej byłby pozycją na liście prowadzącą do
    komunikatu o błędzie, a nie do danych.
    """

    def get(self, request):
        storage = storage_for(request.user)
        data = [dataset_meta(dataset, path=path) for dataset, path in storage.readable()]
        return JsonResponse(data, safe=False)


class DatasetUploadView(ApiView):
    """Przyjęcie pliku CSV: zapis surowego i policzenie wersji przygotowanej.

    Wersja przygotowana jest WARUNKIEM przyjęcia pliku — cała aplikacja
    czyta wyłącznie ją, więc zestaw bez niej nie miałby czego pokazać.
    """

    def post(self, request):
        uploaded = request.FILES.get("file")

        rejection = self._rejection_reason(uploaded)
        if rejection:
            return self.error(rejection, 400)

        storage = storage_for(request.user)
        filename = storage.free_filename(uploaded.name)

        try:
            raw_path = storage.save_raw(uploaded, filename)
        except OSError:
            logger.exception("Zapis pliku nieudany: %s", filename)
            return self.error("Nie udało się zapisać pliku na serwerze.", 500)

        prepared_path, problem = storage.prepare(raw_path, filename)
        if prepared_path is None:
            # Surowy plik idzie do kosza razem z uploadem. Nie ma wpisu
            # w bazie, który by go pilnował, a zostawiony zająłby nazwę
            # i kolejna próba z poprawionym plikiem wylądowałaby jako „(2)”.
            raw_path.unlink(missing_ok=True)
            return self.error(
                f"{problem} Plik nie został dodany — dane muszą dać się "
                f"przygotować, żeby dało się na nich pracować.", 422)

        dataset = Dataset.objects.create(owner=request.user, filename=filename)
        meta = dataset_meta(dataset, path=prepared_path)
        meta["prepared"] = True
        return JsonResponse(meta)

    @staticmethod
    def _rejection_reason(uploaded):
        """Powód odmowy jeszcze przed dotknięciem dysku albo None."""
        if uploaded is None:
            return "Nie przesłano żadnego pliku."
        if not uploaded.name.lower().endswith(".csv"):
            return "Dozwolone są tylko pliki .csv."
        if uploaded.size > MAX_UPLOAD_SIZE:
            return "Plik jest za duży (limit 300 MB)."

        # Pierwsze pięć wierszy wystarczy, żeby stwierdzić, czy to w ogóle
        # jest CSV — i kosztuje tyle samo przy pliku 1 MB co przy 300 MB.
        try:
            preview = pd.read_csv(uploaded, nrows=5)
        except Exception:
            return "Nie udało się odczytać pliku jako CSV."
        finally:
            uploaded.seek(0)

        return "Plik CSV nie zawiera danych." if preview.empty else None


class DatasetRangeView(DatasetApiView):
    """Wycinek danych w PEŁNEJ rozdzielczości dla widocznego zakresu osi X.

    Wołane przez dashboard.js po każdym zoomie. Dzięki temu decymacja nigdy
    nie jest stratna dla oka: im głębiej przybliżasz, tym mniej wierszy
    wpada do kubełka, aż w końcu kubełek ma jedną próbkę i dostajesz surowe
    dane.
    """

    def get(self, request, filename):
        path = self.dataset_path()
        if path is None:
            return self.error("Plik nie istnieje na serwerze.", 404)

        try:
            series = Series.from_csv(path)
        except Exception:
            logger.exception("Nie udało się wczytać CSV: %s", path)
            return self.error("Nie udało się odczytać pliku.", 500)

        try:
            x0 = float(request.GET.get("x0", 0))
            x1 = float(request.GET.get("x1", series.rows))
            buckets = int(request.GET.get("buckets", TARGET_BUCKETS))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Nieprawidłowe parametry zakresu.")

        buckets = max(MIN_BUCKETS, min(buckets, MAX_BUCKETS))
        lo, hi = self._padded_range(x0, x1)

        names = request.GET.get("cols")
        return JsonResponse({
            "lo": max(lo, 0),
            "hi": min(hi, series.rows),
            "total": series.rows,
            "series": series.payload(
                set(names.split(",")) if names else None, lo, hi, buckets),
        })

    @staticmethod
    def _padded_range(x0: float, x1: float):
        """Widoczny zakres z zapasem na przesuwanie wykresu.

        x to numer wiersza, więc zakres tnie się bezpośrednio po indeksie —
        nie trzeba niczego wyszukiwać binarnie.
        """
        span = max(x1 - x0, 1)
        return (int(math.floor(x0 - span * RANGE_MARGIN)),
                int(math.ceil(x1 + span * RANGE_MARGIN)) + 1)


# ============================================================
#  ANIMACJA 3D
# ============================================================

class Motion3DView(DatasetApiView):
    """Scena Plotly + klatki animacji dla JEDNEGO segmentu (?segment=<id>).

    Zakres bierze się wyłącznie z segmentu w bazie, nigdy z widocznego
    fragmentu wykresu — model dźwigni opisuje jedno uderzenie, więc segment
    jest częścią kontraktu, a nie wygodą.

    Czyta CSV przez motion3d.prepare(), a NIE przez Series: tam wartości są
    znormalizowane do 0–1 i jednostki fizyczne już nie istnieją, więc nie
    dałoby się z nich odtworzyć ruchu. Źródłem jest ten sam plik
    przygotowany, co dla wykresu 2D.
    """

    def get(self, request, filename):
        segment, error = self._requested_segment(request)
        if error:
            return error

        path = self.dataset_path()
        if path is None:
            return self.error("Plik nie istnieje na serwerze.", 404)

        try:
            options = MotionOptions.from_query(request.GET)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Nieprawidłowe parametry animacji 3D.")

        try:
            return JsonResponse(scene_for_segment(path, segment, options))
        except motion3d.Motion3DError as exc:
            # Dane albo zakres nie pozwalają nic policzyć — to jest
            # odpowiedź dla użytkownika, nie awaria serwera.
            return self.error(str(exc), 422)
        except Exception:
            logger.exception("Rekonstrukcja 3D nie powiodła się: %s", path)
            return self.error("Nie udało się zbudować animacji 3D.", 500)

    def _requested_segment(self, request):
        raw_id = request.GET.get("segment")
        if not raw_id:
            return None, self.error(
                "Zaznacz uderzenie na wykresie 2D — animacja liczy się dla "
                "pojedynczego segmentu.", 400)

        try:
            segment_id = int(raw_id)
        except (TypeError, ValueError):
            return None, self.error("Nieprawidłowy numer segmentu.", 400)

        segment = Segment.objects.filter(dataset=self.dataset, pk=segment_id).first()
        if segment is None:
            return None, self.error("Nie znaleziono segmentu.", 404)

        return segment, None


# ============================================================
#  SEGMENTY I FAZY
#
#  Struktura: Dataset → Segment (uderzenie) → SubSegment (faza).
#  Granice wszędzie to numery wierszy CSV, tak samo jak oś X wykresu.
# ============================================================

class SegmentListView(DatasetApiView):
    """GET — lista segmentów, POST — nowy segment z {"start", "end"}."""

    def get(self, request, filename):
        return self.segments_response()

    def post(self, request, filename):
        bounds, error = self.parsed_range(request)
        if error:
            return error

        Segment.objects.create(dataset=self.dataset,
                               start=bounds.start, end=bounds.end)
        return self.segments_response(status=201)


class SegmentDetailView(SegmentApiView):
    """PATCH — poprawia zakres segmentu, DELETE — usuwa go wraz z fazami
    (kaskada z ForeignKey)."""

    def patch(self, request, filename, segment_id):
        segment = self.load_segment(segment_id)
        if segment is None:
            return self.error("Nie znaleziono segmentu.", 404)

        bounds, error = self.parsed_range(request)
        if error:
            return error

        segment.start, segment.end = bounds.start, bounds.end
        segment.save(update_fields=["start", "end"])
        trim_phases_to_segment(segment)

        return self.segments_response()

    def delete(self, request, filename, segment_id):
        segment = self.load_segment(segment_id)
        if segment is None:
            return self.error("Nie znaleziono segmentu.", 404)

        segment.delete()
        return self.segments_response()


class SegmentPhaseView(SegmentApiView):
    """PUT — ustawia albo poprawia zakres jednej fazy, DELETE — czyści ją.

    PUT, nie POST, bo faza jest identyfikowana swoją nazwą: to samo żądanie
    wysłane dwa razy daje ten sam stan, a powtórne zaznaczenie fazy
    poprawia istniejący wpis zamiast tworzyć drugi.
    """

    def put(self, request, filename, segment_id, phase):
        segment, error = self._segment_and_phase(segment_id, phase)
        if error:
            return error

        bounds, error = self.parsed_range(request)
        if error:
            return error

        # Faza jest częścią uderzenia, więc nie może z niego wystawać.
        # Zaznaczenie „z zapasem” przycinamy do granic segmentu — odrzucamy
        # dopiero takie, które w ogóle nie zahacza o segment.
        inside = bounds.clipped_to(segment_range(segment))
        if inside is None:
            return self.error("Zaznaczony zakres leży poza segmentem.", 400)

        SubSegment.objects.update_or_create(
            segment=segment,
            phase=phase,
            defaults={"start": inside.start, "end": inside.end},
        )
        return self.segments_response()

    def delete(self, request, filename, segment_id, phase):
        segment, error = self._segment_and_phase(segment_id, phase)
        if error:
            return error

        SubSegment.objects.filter(segment=segment, phase=phase).delete()
        return self.segments_response()

    def _segment_and_phase(self, segment_id, phase):
        segment = self.load_segment(segment_id)
        if segment is None:
            return None, self.error("Nie znaleziono segmentu.", 404)
        if phase not in dict(SubSegment.PHASE_CHOICES):
            return None, self.error("Nieznana faza.", 400)
        return segment, None
