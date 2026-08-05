import logging
import math
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.utils.text import get_valid_filename
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .models import Dataset

logger = logging.getLogger(__name__)

DATA_DIR = Path(
    getattr(settings, "ACCUR8POOL_DATA_DIR", Path(settings.BASE_DIR) / "new_data" / "raw_data")
)

TARGET_BUCKETS = 2500

MAX_UPLOAD_SIZE = 300 * 1024 * 1024


def _user_dir(user):
    return DATA_DIR / str(user.pk)


def _resolve_dataset(user, filename):
    """Zwraca Dataset NALEŻĄCY DO user. Nazwa z URL-a nigdy nie trafia
    wprost do ścieżki na dysku — zawsze przechodzi przez bazę."""
    qs = Dataset.objects.filter(owner=user)
    if filename:
        return qs.filter(filename=filename).first()
    return qs.first()


def _count_records(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except OSError:
        return None


def _dataset_meta(dataset, path=None, records=None):
    if records is None and path is not None:
        records = _count_records(path)
    return {
        "id": dataset.filename,
        "name": dataset.filename,
        "records": records,
        "updated_at": dataset.uploaded_at.strftime("%Y-%m-%d %H:%M"),
        "url": f"/dashboard/{quote(dataset.filename)}/",
    }


# ============================================================
#  WCZYTYWANIE I CACHE
# ============================================================

@lru_cache(maxsize=2)
def _load_series_cached(path_str, mtime, size):
    """Wczytuje CSV i zwraca gotowe do rysowania, znormalizowane serie.

    Klucz cache zawiera mtime i rozmiar pliku, więc podmiana pliku na
    dysku unieważnia wpis sama z siebie — nie trzeba niczego czyścić.

    maxsize=2 jest celowo małe: jedna ramka z pliku 300 MB potrafi zająć
    kilka GB RAM-u. Trzymamy float32 zamiast float64 (połowa pamięci,
    a i tak rysujemy z dokładnością do piksela) i wyłącznie kolumny
    liczbowe.
    """
    df = pd.read_csv(path_str)

    numeric = df.select_dtypes(include=["number"])
    numeric = numeric.drop(columns=[c for c in ("index",) if c in numeric.columns])

    names = list(numeric.columns)
    if not names:
        return {"names": [], "values": np.empty((0, 0), dtype=np.float32), "n": 0}

    values = numeric.to_numpy(dtype=np.float32, copy=True)

    col_min = np.nanmin(values, axis=0)
    col_max = np.nanmax(values, axis=0)
    span = col_max - col_min
    flat = span == 0
    span[flat] = 1.0
    values = (values - col_min) / span
    values[:, flat] = 0.0

    return {"names": names, "values": values, "n": values.shape[0]}


def _load_series(path: Path):
    st = path.stat()
    return _load_series_cached(str(path), st.st_mtime_ns, st.st_size)


# ============================================================
#  DECYMACJA MIN/MAX
# ============================================================

def _minmax_indices(column: np.ndarray, lo: int, hi: int, buckets: int) -> np.ndarray:
    """Indeksy punktów do narysowania dla jednej serii w zakresie [lo, hi).

    Dzielimy zakres na kubełki i z każdego bierzemy MINIMUM I MAKSIMUM.
    To jest cała różnica względem brania co n-tego wiersza: pojedynczy
    pik trwający jedną próbkę zostaje zachowany co do wartości, bo jest
    ekstremum swojego kubełka. Przy stride ten sam pik znika, jeśli nie
    trafi akurat w wielokrotność kroku — a przy danych z akcelerometru
    to właśnie piki są tym, na co się patrzy.

    Ta sama metoda jest używana w oscyloskopach cyfrowych i edytorach
    audio do rysowania przebiegów.
    """
    n = hi - lo
    if n <= 0:
        return np.empty(0, dtype=np.int64)

    # Mniej punktów niż miejsca na wykresie — rysujemy wszystko bez zmian
    if n <= buckets * 2:
        return np.arange(lo, hi, dtype=np.int64)

    edges = np.linspace(lo, hi, buckets + 1).astype(np.int64)
    out = np.empty(buckets * 2, dtype=np.int64)

    for i in range(buckets):
        s, e = edges[i], edges[i + 1]
        if e <= s:
            out[2 * i] = out[2 * i + 1] = s
            continue
        seg = column[s:e]
        if np.all(np.isnan(seg)):
            out[2 * i] = out[2 * i + 1] = s
            continue
        out[2 * i] = s + int(np.nanargmin(seg))
        out[2 * i + 1] = s + int(np.nanargmax(seg))

    # unique sortuje i usuwa duplikaty (kubełek, w którym min == max)
    return np.unique(out)


def _series_payload(series, name_filter=None, lo=0, hi=None, buckets=TARGET_BUCKETS):
    """Zwraca {nazwa: {"x": [...], "y": [...]}} po decymacji."""
    n = series["n"]
    hi = n if hi is None else min(hi, n)
    lo = max(0, lo)

    out = {}
    for j, name in enumerate(series["names"]):
        if name_filter is not None and name not in name_filter:
            continue
        col = series["values"][:, j]
        idx = _minmax_indices(col, lo, hi, buckets)
        out[name] = {
            "x": idx.tolist(),
            # NaN nie przechodzi przez JSON — None rysuje się jako przerwa
            "y": [None if math.isnan(v) else float(v) for v in col[idx]],
        }
    return out


def _build_figure(series):
    """Figura startowa: cały przebieg w rozdzielczości ekranu."""
    payload = _series_payload(series)

    fig = go.Figure()
    for name, s in payload.items():
        # Scattergl zamiast Scatter — rysowanie idzie przez WebGL na karcie
        # graficznej. Zwykły Scatter przy kilkunastu seriach po kilka tysięcy
        # punktów zamula przewijanie i zoom, szczególnie na telefonie.
        fig.add_trace(go.Scattergl(
            x=s["x"], y=s["y"], name=name, mode="lines",
            line=dict(width=1.2),
            hovertemplate="%{y:.4f}<extra>" + name + "</extra>",
        ))

    fig.update_layout(
        template="plotly_white",
        hovermode="closest",
        margin=dict(l=48, r=16, t=16, b=40),
        xaxis=dict(title="Indeks"),
        yaxis=dict(title="Wartość znormalizowana"),
        legend=dict(orientation="v", x=1.02, y=1, xanchor="left", yanchor="top"),
        uirevision="keep",  # zoom przeżywa aktualizacje danych
    )
    return fig


# ============================================================
#  WIDOKI
# ============================================================

def _render_dashboard(request, dataset=None, graph_data=None, columns_count=None,
                      total_points=None, error=None):
    return render(request, "dashboard.html", {
        "dataset": _dataset_meta(dataset) if dataset else None,
        "graph_data": graph_data,
        "columns_count": columns_count,
        "total_points": total_points,
        "error": error,
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
@ensure_csrf_cookie
def dashboard(request, filename=None):
    dataset = _resolve_dataset(request.user, filename)

    if dataset is None:
        return _render_dashboard(
            request,
            error=(f"Nie znaleziono pliku: {filename}" if filename else None),
        )

    data_path = _user_dir(request.user) / dataset.filename

    if not data_path.exists():
        logger.warning("Brak pliku na dysku: %s (dataset id=%s)", data_path, dataset.pk)
        return _render_dashboard(request, dataset=dataset,
                                 error="Plik nie istnieje na serwerze. Prześlij go ponownie.")

    try:
        series = _load_series(data_path)
    except pd.errors.EmptyDataError:
        return _render_dashboard(request, dataset=dataset, error="Plik CSV nie zawiera danych.")
    except Exception:
        logger.exception("Nie udało się wczytać CSV: %s", data_path)
        return _render_dashboard(request, dataset=dataset,
                                 error="Nie udało się odczytać pliku CSV.")

    if not series["names"]:
        return _render_dashboard(request, dataset=dataset, columns_count=0,
                                 error="Plik nie zawiera kolumn liczbowych do narysowania.")

    fig = _build_figure(series)

    return _render_dashboard(
        request,
        dataset=dataset,
        graph_data=fig.to_plotly_json(),
        columns_count=len(series["names"]),
        total_points=series["n"],
    )


@login_required
def api_dataset_range(request, filename):
    """Doczytuje wycinek danych w PEŁNEJ rozdzielczości dla widocznego
    zakresu osi X. Wołane przez dashboard.js po każdym zoomie.

    Dzięki temu decymacja nigdy nie jest stratna dla oka: im głębiej
    przybliżasz, tym mniej wierszy wpada do kubełka, aż w końcu kubełek
    ma jedną próbkę i dostajesz surowe dane. Rozdzielczość jest zawsze
    maksymalna z możliwych do wyświetlenia."""
    dataset = _resolve_dataset(request.user, filename)
    if dataset is None:
        return JsonResponse({"error": "Nie znaleziono pliku."}, status=404)

    path = _user_dir(request.user) / dataset.filename
    if not path.exists():
        return JsonResponse({"error": "Plik nie istnieje na serwerze."}, status=404)

    try:
        series = _load_series(path)
    except Exception:
        logger.exception("Nie udało się wczytać CSV: %s", path)
        return JsonResponse({"error": "Nie udało się odczytać pliku."}, status=500)

    # x to numer wiersza, więc zakres tnie się bezpośrednio po indeksie —
    # nie trzeba niczego wyszukiwać binarnie.
    try:
        x0 = float(request.GET.get("x0", 0))
        x1 = float(request.GET.get("x1", series["n"]))
        buckets = int(request.GET.get("buckets", TARGET_BUCKETS))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Nieprawidłowe parametry zakresu.")

    buckets = max(200, min(buckets, 6000))

    # Margines: użytkownik przesuwa wykres, więc doczytujemy trochę poza
    # widoczny zakres — inaczej każde drgnięcie myszą to nowe żądanie.
    span = max(x1 - x0, 1)
    lo = int(math.floor(x0 - span * 0.15))
    hi = int(math.ceil(x1 + span * 0.15)) + 1

    cols = request.GET.get("cols")
    name_filter = set(cols.split(",")) if cols else None

    return JsonResponse({
        "lo": max(lo, 0),
        "hi": min(hi, series["n"]),
        "total": series["n"],
        "series": _series_payload(series, name_filter, lo, hi, buckets),
    })


@login_required
@ensure_csrf_cookie
def datasets_view(request):
    return render(request, "datasets.html")


@login_required
def api_datasets(request):
    user_dir = _user_dir(request.user)
    data = [
        _dataset_meta(ds, path=user_dir / ds.filename)
        for ds in Dataset.objects.filter(owner=request.user)
    ]
    return JsonResponse(data, safe=False)


@login_required
@require_POST
def upload_dataset(request):
    uploaded = request.FILES.get("file")

    if uploaded is None:
        return JsonResponse({"error": "Nie przesłano żadnego pliku."}, status=400)
    if not uploaded.name.lower().endswith(".csv"):
        return JsonResponse({"error": "Dozwolone są tylko pliki .csv."}, status=400)
    if uploaded.size > MAX_UPLOAD_SIZE:
        return JsonResponse({"error": "Plik jest za duży (limit 300 MB)."}, status=400)

    try:
        preview = pd.read_csv(uploaded, nrows=5)
        if preview.empty:
            return JsonResponse({"error": "Plik CSV nie zawiera danych."}, status=400)
        uploaded.seek(0)
    except Exception:
        return JsonResponse({"error": "Nie udało się odczytać pliku jako CSV."}, status=400)

    safe_name = get_valid_filename(Path(uploaded.name).name)
    if not safe_name.lower().endswith(".csv"):
        safe_name += ".csv"

    user_dir = _user_dir(request.user)
    user_dir.mkdir(parents=True, exist_ok=True)

    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    filename = safe_name
    dest_path = user_dir / filename
    counter = 1
    while dest_path.exists():
        counter += 1
        filename = f"{stem} ({counter}){suffix}"
        dest_path = user_dir / filename

    try:
        with open(dest_path, "wb+") as dest:
            for chunk in uploaded.chunks():
                dest.write(chunk)
    except OSError:
        logger.exception("Zapis pliku nieudany: %s", dest_path)
        return JsonResponse({"error": "Nie udało się zapisać pliku na serwerze."}, status=500)

    dataset = Dataset.objects.create(owner=request.user, filename=filename)
    return JsonResponse(_dataset_meta(dataset, path=dest_path))
