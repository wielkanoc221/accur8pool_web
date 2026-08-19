# Obraz aplikacji accur8pool_web.
#
# Budowanie idzie w dwóch etapach, bo pandas, numpy, scipy i pyarrow to
# razem kilkaset megabajtów razem z narzędziami do kompilacji. Etap
# `builder` instaluje je do własnego virtualenva, a obraz końcowy dostaje
# już tylko gotowy katalog /opt/venv — bez kompilatora i bez nagłówków.
#
# Python 3.13: Django 6.0 wymaga co najmniej 3.12 (patrz requirements.txt).

# ---------------------------------------------------------------------
#  ETAP 1 — zależności
# ---------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential na wypadek pakietu bez gotowego koła (wheel) dla tej
# wersji Pythona; przy obecnym zestawie wersji wszystkie mają koła, więc
# nic się tu nie kompiluje — ten pakiet jest polisą na przyszłe bumpy
# i tak czy inaczej zostaje w etapie budowania.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Same zależności w osobnej warstwie: dopóki requirements.txt się nie
# zmienia, zmiana kodu aplikacji nie unieważnia tej (najdroższej) warstwy.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r /tmp/requirements.txt

# ---------------------------------------------------------------------
#  ETAP 2 — obraz uruchomieniowy
# ---------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

# PYTHONUNBUFFERED — logi mają iść do `docker logs` od razu, a nie wtedy,
#   gdy zapełni się bufor; inaczej przy awarii ostatnie (najciekawsze)
#   linie przepadają.
# PYTHONPATH — katalog projektu jest korzeniem ścieżki importów; stąd
#   biorą się nazwy `dashboard`, `account` i `utils` (patrz INSTALLED_APPS
#   oraz dashboard/storage.py).
# ACCUR8POOL_DATA_ROOT — dane użytkowników i baza, poza obrazem, na
#   wolumenie.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=accur8pool_web.settings \
    PYTHONPATH=/app \
    ACCUR8POOL_DATA_ROOT=/data

# curl wchodzi do obrazu wyłącznie dla HEALTHCHECK-a poniżej. libpq nie
# jest potrzebne: psycopg[binary] ma sterownik w kole.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Konto bez uprawnień roota: włamanie przez aplikację nie ma wtedy od razu
# władzy nad całym kontenerem. UID na sztywno (1000), żeby pliki na
# wolumenie miały przewidywalnego właściciela także od strony hosta.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app

# Właścicielem musi być SAM KATALOG /app, nie tylko jego zawartość:
# `collectstatic` (niżej, już spod konta app) zakłada w nim podkatalog
# staticfiles, a do tego potrzebuje prawa zapisu w katalogu nadrzędnym.
# WORKDIR tworzy go jako root, więc bez tego kroku budowanie przewraca
# się na PermissionError.
RUN mkdir -p /app && chown app:app /app
WORKDIR /app
COPY --chown=app:app accur8pool_web/ /app/
# Entrypoint zostaje własnością roota — proces aplikacji ma go wykonywać,
# a nie móc podmienić.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# Katalog na dane istnieje w obrazie tylko po to, żeby miał właściciela
# `app` zanim wolumen zostanie podmontowany.
RUN mkdir -p /data && chown app:app /data

USER app

# Pliki statyczne zbierane są PRZY BUDOWANIU, nie przy starcie: to
# artefakt kodu, taki sam dla każdego wdrożenia tego obrazu, a robienie
# tego przy każdym starcie opóźniałoby wstawanie kontenera i wymagałoby
# prawa zapisu w katalogu aplikacji.
#
# Klucz jest tu podstawiony na chwilę, bo `collectstatic` uruchamia pełne
# ładowanie ustawień, a te bez SECRET_KEY nie wstają. Nie ma go w obrazie:
# to zmienna jednego polecenia RUN, nie ENV. Prawdziwy klucz wchodzi
# dopiero przy starcie kontenera.
RUN DJANGO_SECRET_KEY=build-time-only-not-a-runtime-secret \
    DJANGO_STATIC_ROOT=/app/staticfiles \
    python manage.py collectstatic --noinput --clear

VOLUME ["/data"]

EXPOSE 8000

# Sonda pyta o /healthz/, które dotyka bazy — patrz accur8pool_web/health.py.
# Postać powłokowa (bez nawiasów kwadratowych) jest tu potrzebna, żeby
# podstawił się PORT — w postaci exec nie ma kto rozwinąć zmiennej.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent --output /dev/null "http://127.0.0.1:${PORT:-8000}/healthz/" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["gunicorn"]
