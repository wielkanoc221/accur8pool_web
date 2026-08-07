// Wspólna funkcja wysyłki pliku CSV na serwer.
// Używana zarówno na stronie /datasets/, jak i w panelu bocznym.

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

    // `prepared` mówi, czy obok surowego pliku powstała wersja
    // przygotowana (transform_raw_df). false nie jest błędem uploadu —
    // plik jest na serwerze, brakuje tylko kolumn pochodnych.
    return payload; // { id, name, records, updated_at, url, prepared }
}
