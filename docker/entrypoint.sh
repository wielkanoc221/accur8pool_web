#!/bin/sh
#
# Start kontenera aplikacji.
#
# Wszystko, co MUSI się wydarzyć na docelowej maszynie, a czego nie dało
# się zrobić przy budowaniu obrazu, jest tutaj: katalogi na wolumenie
# (montowany dopiero teraz) i migracje bazy (której przy budowaniu nie
# było). Pliki statyczne są już zebrane w obrazie.
#
# Skrypt jest w /bin/sh, nie w bashu — obraz `slim` basha nie zawiera.

set -eu

# ---------------------------------------------------------------------
#  Katalogi na dane
# ---------------------------------------------------------------------
# Świeży wolumen jest pusty. Aplikacja zakłada podkatalog per użytkownik
# sama, ale korzenie obu drzew muszą istnieć, zanim przyjdzie pierwszy
# upload.
DATA_ROOT="${ACCUR8POOL_DATA_ROOT:-/data}"
mkdir -p "${DATA_ROOT}/raw_data" "${DATA_ROOT}/prepared_data"

# ---------------------------------------------------------------------
#  Czekanie na bazę
# ---------------------------------------------------------------------
# Dotyczy wyłącznie PostgreSQL-a: `depends_on` w Compose mówi tylko tyle,
# że kontener bazy wystartował, nie że baza przyjmuje połączenia. SQLite
# to plik na wolumenie — nie ma na co czekać.
if [ "${DB_ENGINE:-sqlite}" = "postgres" ] || [ "${DB_ENGINE:-sqlite}" = "postgresql" ]; then
    echo "entrypoint: czekam na bazę ${DB_HOST:-db}:${DB_PORT:-5432}…"
    attempt=1
    max_attempts="${DB_WAIT_ATTEMPTS:-30}"
    while [ "${attempt}" -le "${max_attempts}" ]; do
        if python -c "
import os, sys
import psycopg
try:
    psycopg.connect(
        host=os.environ.get('DB_HOST', 'db'),
        port=os.environ.get('DB_PORT', '5432'),
        dbname=os.environ.get('DB_NAME', 'accur8pool'),
        user=os.environ.get('DB_USER', 'accur8pool'),
        password=os.environ.get('DB_PASSWORD', ''),
        connect_timeout=3,
    ).close()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            echo "entrypoint: baza odpowiada."
            break
        fi
        if [ "${attempt}" -eq "${max_attempts}" ]; then
            echo "entrypoint: baza nie odpowiedziała po ${max_attempts} próbach — przerywam." >&2
            exit 1
        fi
        attempt=$((attempt + 1))
        sleep 2
    done
fi

# ---------------------------------------------------------------------
#  Migracje
# ---------------------------------------------------------------------
# Domyślnie włączone: pojedynczy kontener aplikacji to najprostszy
# przypadek i wtedy jest to dokładnie to, czego się oczekuje. Przy kilku
# replikach ustaw RUN_MIGRATIONS=0 i puść `migrate` raz, osobnym
# poleceniem — równoległe migracje potrafią się zakleszczyć.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "entrypoint: migracje…"
    python manage.py migrate --noinput
fi

# ---------------------------------------------------------------------
#  Konto administratora
# ---------------------------------------------------------------------
# Zakładane tylko wtedy, gdy podano komplet zmiennych, i tylko gdy konta
# o tej nazwie jeszcze nie ma — powtórny start nie nadpisze hasła
# zmienionego po pierwszym logowaniu.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    echo "entrypoint: sprawdzam konto administratora…"
    python manage.py shell -c "
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ['DJANGO_SUPERUSER_USERNAME']
if User.objects.filter(username=username).exists():
    print('entrypoint: konto %s już istnieje — zostawiam bez zmian.' % username)
else:
    User.objects.create_superuser(
        username=username,
        email=os.environ.get('DJANGO_SUPERUSER_EMAIL', ''),
        password=os.environ['DJANGO_SUPERUSER_PASSWORD'],
    )
    print('entrypoint: założono konto %s.' % username)
"
fi

# ---------------------------------------------------------------------
#  Proces docelowy
# ---------------------------------------------------------------------
# `gunicorn` bez reszty argumentów (czyli CMD z Dockerfile'a) znaczy
# „serwuj aplikację”. Każde inne polecenie idzie wprost — dzięki temu
# `docker compose run --rm web python manage.py createsuperuser` działa
# przez ten sam entrypoint, z tymi samymi katalogami i tą samą bazą.
if [ "$1" = "gunicorn" ] && [ "$#" -eq 1 ]; then
    # Liczba workerów: rzeczy ciężkie w tej aplikacji (przygotowanie
    # danych, rekonstrukcja 3D) liczy pandas/numpy/scipy, czyli CPU.
    # Stąd procesy, a nie wątki, i sensowny punkt wyjścia to 2×rdzenie+1.
    WORKERS="${GUNICORN_WORKERS:-$(python -c 'import os; print(2 * (os.cpu_count() or 1) + 1)')}"

    # Upload potrafi mieć setki megabajtów, a po nim od razu idzie
    # transform_raw_df. Domyślne 30 s gunicorna ubiłoby taki request
    # w połowie przetwarzania.
    TIMEOUT="${GUNICORN_TIMEOUT:-300}"

    echo "entrypoint: gunicorn — ${WORKERS} workerów, timeout ${TIMEOUT}s."

    # --max-requests: pandas przy dużych ramkach zostawia po sobie pamięć,
    #   której alokator nie oddaje systemowi. Restart workera co tysiąc
    #   żądań trzyma zużycie w ryzach, a jitter sprawia, że workery nie
    #   robią tego wszystkie w tej samej chwili.
    # --*-logfile -: logi na stdout/stderr, skąd zbiera je Docker.
    exec gunicorn accur8pool_web.wsgi:application \
        --bind "0.0.0.0:${PORT:-8000}" \
        --workers "${WORKERS}" \
        --timeout "${TIMEOUT}" \
        --graceful-timeout 30 \
        --max-requests 1000 \
        --max-requests-jitter 100 \
        --access-logfile - \
        --error-logfile - \
        --capture-output
fi

exec "$@"
