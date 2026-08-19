/* ============================================================
   ACCUR8POOL — funkcje wspólne dla wszystkich podstron.
   Ładuj jako PIERWSZY skrypt: pozostałe pliki z nich korzystają.
   ============================================================ */

/** Zamienia tekst na bezpieczny HTML. Używać ZAWSZE przy wstawianiu
 *  danych z serwera przez innerHTML — nazwy plików pochodzą od
 *  użytkownika i mogą zawierać < > &. */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/** Token CSRF do fetchów zmieniających stan (POST/PUT/PATCH/DELETE). */
function getCsrfToken() {
    // Główne źródło: ukryte pole z {% csrf_token %} w szablonie.
    // Działa niezależnie od CSRF_COOKIE_HTTPONLY / CSRF_USE_SESSIONS —
    // czytanie z ciasteczka potrafi zwrócić null i wtedy serwer odrzuca
    // żądanie błędem "CSRF token has incorrect length".
    const input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;

    // Fallback: ciasteczko (działa tylko przy CSRF_COOKIE_HTTPONLY = False)
    const name = 'csrftoken';
    const cookies = document.cookie ? document.cookie.split(';') : [];
    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.startsWith(name + '=')) {
            return decodeURIComponent(cookie.substring(name.length + 1));
        }
    }
    return null;
}

/** Wołanie API aplikacji. Zwraca sparsowany JSON albo rzuca wyjątkiem
 *  z komunikatem OD SERWERA — pole `error` w odpowiedzi niesie zdanie
 *  napisane dla użytkownika, więc szkoda je zamieniać na numer statusu.
 *
 *  `body` (dowolny obiekt) leci jako JSON; przy metodach innych niż GET
 *  dokładany jest token CSRF. */
async function apiFetch(url, method, body) {
    const options = {
        method: method || 'GET',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    };

    if (options.method !== 'GET') {
        options.headers['X-CSRFToken'] = getCsrfToken();
    }
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(payload.error || 'Błąd serwera (' + response.status + ')');
    }
    return payload;
}

/** Skrót na najczęstszy przypadek: GET po dane. */
function fetchJson(url) {
    return apiFetch(url, 'GET');
}

/** Wysyła plik CSV na serwer.
 *
 *  Odpowiedź 2xx znaczy, że plik ma wersję przygotowaną — bez niej serwer
 *  w ogóle go nie przyjmuje (cała aplikacja czyta wyłącznie prepared_data).
 *  Nieudane przygotowanie wraca jako 422 z powodem w `error`, czyli tą samą
 *  drogą co pozostałe błędy.
 *
 *  Zwraca { id, name, records, updated_at, url, prepared }. */
async function uploadDatasetFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch('/api/datasets/upload/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCsrfToken() },
        body: formData
    });

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(payload.error || `Błąd serwera (${response.status})`);
    }
    return payload;
}
