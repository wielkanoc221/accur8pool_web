/* ============================================================
   ACCUR8POOL — funkcje wspólne dla wszystkich podstron.
   Ładuj jako PIERWSZY skrypt.
   ============================================================ */

/** Zamienia tekst na bezpieczny HTML. Używać ZAWSZE przy wstawianiu
 *  danych z serwera przez innerHTML — nazwy plików pochodzą od
 *  użytkownika i mogą zawierać < > &. */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/** Token CSRF do fetchów zmieniających stan (POST/DELETE). */
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

/** Wspólny GET do API aplikacji. Rzuca wyjątkiem z czytelnym komunikatem,
 *  żeby każdy wywołujący nie powtarzał tej samej obsługi błędu. */
async function fetchJson(url) {
    const response = await fetch(url, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });

    if (!response.ok) {
        throw new Error(`Serwer zwrócił błąd ${response.status}`);
    }
    return response.json();
}
