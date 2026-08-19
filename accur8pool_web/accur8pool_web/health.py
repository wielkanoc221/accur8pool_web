"""Sonda stanu dla kontenera, proxy i monitoringu.

Odpowiada na pytanie „czy ten proces ma sens jako cel ruchu”, a nie „czy
proces żyje”. Dlatego dotyka bazy: gunicorn potrafi odpowiadać jeszcze
długo po tym, jak baza stała się nieosiągalna, a wtedy każde prawdziwe
żądanie kończy się pięćsetką. Zapytanie jest najtańsze z możliwych
(SELECT 1), więc sonda co kilka sekund nic nie kosztuje.

Celowo nie sprawdza katalogów z danymi: brak jednego pliku zestawu to
sprawa jednego użytkownika, a nie powód, żeby wyłączyć z ruchu cały
kontener.
"""

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 — powód idzie do odpowiedzi
        # 503, a nie 500: to stan przejściowy i taki właśnie kod każe
        # load balancerowi wstrzymać ruch, nie zgłaszać awarii aplikacji.
        return JsonResponse({"status": "error", "database": str(exc)}, status=503)

    return JsonResponse({"status": "ok"})
