"""Pliki zestawów danych na dysku.

Dane użytkownika leżą w DWÓCH równoległych drzewach o tej samej strukturze
(<katalog>/<user_id>/<nazwa>.csv):

    raw_data       plik dokładnie taki, jaki przyszedł z uploadu — kopia
                   źródłowa, z której da się przygotowanie powtórzyć,
    prepared_data  ten sam plik po transform_raw_df (filtry, magnitudy,
                   jerk, roll/pitch), liczony w trakcie uploadu.

APLIKACJA CZYTA WYŁĄCZNIE Z prepared_data — wykres 2D, doczytywanie
zakresów i animacja 3D dostają dokładnie ten sam plik, więc numer wiersza
znaczy wszędzie to samo. Zestaw bez wersji przygotowanej nie jest w ogóle
pokazywany, a upload, któremu jej nie udało się policzyć, jest odrzucany
razem z plikiem surowym.

Cała wiedza o tym układzie siedzi tutaj: widoki dostają gotowe ścieżki
i nie sklejają ich same z nazw z URL-a.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from django.utils.text import get_valid_filename

from .models import Dataset

logger = logging.getLogger(__name__)


class DataPreparationUnavailable(Exception):
    """Awaria wdrożenia, nie wada wgranego pliku — patrz DatasetStorage.prepare."""


class DatasetStorage:
    """Dwa drzewa plików JEDNEGO użytkownika.

    Korzenie wchodzą przez konstruktor, a nie przez import stałej: widoki
    czytają je ze swoich zmiennych modułowych przy każdym żądaniu, dzięki
    czemu testy podmieniają katalogi jednym mock.patch.object.
    """

    def __init__(self, user, raw_root: Path, prepared_root: Path):
        self.user = user
        self.raw_dir = Path(raw_root) / str(user.pk)
        self.prepared_dir = Path(prepared_root) / str(user.pk)

    # ------------------------------------------------------------
    #  ŚCIEŻKI
    # ------------------------------------------------------------

    def path_for(self, dataset: Dataset) -> Path:
        """JEDYNY plik, z którego czyta aplikacja: wersja przygotowana.

        Nie ma tu zapasu na plik surowy. Przy dwóch źródłach o różnej
        liczbie kolumn i (przy plikach niepełnych) różnej długości nie było
        zagwarantowane, że segment zaznaczony na wykresie wskazuje
        w animacji ten sam fragment ruchu.

        Wołający i tak sprawdzają .exists(), bo plik może zniknąć z dysku
        między żądaniami.
        """
        return self.prepared_dir / dataset.filename

    def raw_path_for(self, filename: str) -> Path:
        return self.raw_dir / filename

    # ------------------------------------------------------------
    #  ODCZYT
    # ------------------------------------------------------------

    def resolve(self, filename: str | None) -> Dataset | None:
        """Dataset NALEŻĄCY DO tego użytkownika I MAJĄCY wersję przygotowaną.

        Nazwa z URL-a nigdy nie trafia wprost do ścieżki na dysku — zawsze
        przechodzi przez bazę.

        Filtr po istnieniu pliku przygotowanego realizuje zasadę „bez
        prepared zestawu nie widać”. Sam upload nie zakłada już wpisu bez
        wersji przygotowanej, ale zestawy sprzed tej zmiany (i takie,
        których plik ktoś usunął z dysku) nadal siedzą w bazie —
        a wpuszczone dalej kończyłyby się pustym wykresem zamiast
        czytelnego „nie znaleziono”.
        """
        candidates = Dataset.objects.filter(owner=self.user)
        if filename:
            candidates = candidates.filter(filename=filename)

        for dataset in candidates:
            if self.path_for(dataset).exists():
                return dataset
        return None

    def readable(self):
        """Pary (dataset, ścieżka) dla zestawów, które da się otworzyć."""
        for dataset in Dataset.objects.filter(owner=self.user):
            path = self.path_for(dataset)
            if path.exists():
                yield dataset, path

    # ------------------------------------------------------------
    #  UPLOAD
    # ------------------------------------------------------------

    def free_filename(self, original_name: str) -> str:
        """Nazwa, która nie jest jeszcze zajęta w ŻADNYM z trzech miejsc.

        Sprawdzenie samego raw_data nie wystarczało: nieudany upload
        sprząta po sobie surowy plik, a wpis w bazie ma unique_together
        (owner, filename) — bez tego kolejny plik o tej samej nazwie
        kończyłby się IntegrityError zamiast wersją „(2)”.
        """
        safe_name = get_valid_filename(Path(original_name).name)
        if not safe_name.lower().endswith(".csv"):
            safe_name += ".csv"

        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        taken = set(
            Dataset.objects.filter(owner=self.user).values_list("filename", flat=True)
        )

        filename = safe_name
        counter = 1
        while self._is_taken(filename, taken):
            counter += 1
            filename = f"{stem} ({counter}){suffix}"
        return filename

    def _is_taken(self, filename: str, taken: set) -> bool:
        return (self.raw_path_for(filename).exists()
                or (self.prepared_dir / filename).exists()
                or filename in taken)

    def save_raw(self, uploaded, filename: str) -> Path:
        """Zapisuje plik kawałkami — 300 MB nie wchodzi do pamięci naraz."""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        destination = self.raw_path_for(filename)

        with open(destination, "wb+") as target:
            for chunk in uploaded.chunks():
                target.write(chunk)
        return destination

    def prepare(self, raw_path: Path, filename: str):
        """Liczy wersję przygotowaną świeżo wgranego pliku.

        Zwraca (ścieżka, None) po udanym przygotowaniu albo (None,
        komunikat) po nieudanym. Nieudana transformacja UNIEWAŻNIA CAŁY
        UPLOAD: skoro aplikacja czyta wyłącznie wersję przygotowaną, plik
        bez niej nie miałby czego pokazać, a zestaw widniejący na liście
        i otwierający się pustym błędem jest gorszy niż odmowa przyjęcia
        pliku.

        Komunikat wraca do przeglądarki, więc niesie powód (np. których
        kolumn brakuje) — pełny ślad wyjątku zostaje w logu.
        """
        try:
            preparation = _data_preparation_module()
        except DataPreparationUnavailable:
            # To jest awaria wdrożenia, a nie wada wgranego pliku — mówimy
            # to wprost, zamiast sugerować użytkownikowi poprawianie danych.
            logger.exception("Moduł przygotowania danych jest nieosiągalny")
            return None, "Przygotowanie danych jest niedostępne na serwerze."

        try:
            path = preparation.prepare_raw_file_and_save(
                raw_path, self.prepared_dir, filename)
        except preparation.WrongColumnsException as exc:
            logger.info("Plik %s nie nadaje się do przygotowania: %s", raw_path, exc)
            return None, str(exc)
        except Exception:
            logger.exception("Nie udało się przygotować pliku: %s", raw_path)
            return None, "Nie udało się przygotować danych z tego pliku."

        return path, None


# ============================================================
#  OPIS ZESTAWU DLA PRZEGLĄDARKI
# ============================================================

def dataset_meta(dataset: Dataset, path: Path = None, records: int = None) -> dict:
    """Zestaw danych w postaci, w której widzi go interfejs."""
    if records is None and path is not None:
        records = count_records(path)
    return {
        "id": dataset.filename,
        "name": dataset.filename,
        "records": records,
        "updated_at": dataset.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        "url": f"/dashboard/{quote(dataset.filename)}/",
    }


def count_records(path: Path):
    """Liczba wierszy danych (bez nagłówka) albo None, gdy pliku nie da się
    odczytać. Liczymy same znaki końca linii — wczytywanie ramki tylko po
    to, żeby podać liczbę w kafelku, kosztowałoby sekundy przy 300 MB."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return max(sum(1 for _ in handle) - 1, 0)
    except OSError:
        return None


def _data_preparation_module():
    """Moduł z transformacją danych, spod TEJ nazwy, pod którą da się go
    zaimportować.

    Ten sam katalog jest widoczny pod dwiema nazwami, zależnie od tego, co
    trafiło na sys.path: `utils.data_processing…`, gdy uruchamia się przez
    manage.py (katalog projektu jest wtedy korzeniem — stąd biorą się nazwy
    aplikacji `dashboard` i `account` w INSTALLED_APPS), albo
    `accur8pool_web.utils.data_processing…`, gdy na ścieżce jest katalog
    NAD projektem. Obie próby są tanie i wykonują się raz na upload.

    Poprzednio import był wpisany na jedną z tych postaci i cichł
    w `except Exception` razem z błędami danych. Skutek był taki, że przy
    złym ustawieniu ścieżki ŻADEN plik nie dostawał wersji przygotowanej,
    a odpowiedź uploadu meldowała tylko `prepared: false` — nie do
    odróżnienia od pliku bez wymaganych kolumn.

    Import jest leniwy (w funkcji, nie na górze modułu) celowo:
    transform_raw_df ciągnie za sobą scipy, a to zależność potrzebna
    wyłącznie tutaj — bez niej reszta dashboardu ma działać normalnie.
    """
    try:
        from utils.data_processing import prepare_raw_data
    except ImportError:
        try:
            from accur8pool_web.utils.data_processing import prepare_raw_data
        except ImportError as exc:
            raise DataPreparationUnavailable(str(exc)) from exc
    return prepare_raw_data
