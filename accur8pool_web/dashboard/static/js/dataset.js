document.addEventListener('DOMContentLoaded', function () {
    loadDatasets();
    setupUpload();
});

function setupUpload() {
    const input = document.getElementById('upload-input');
    const status = document.getElementById('upload-status');
    if (!input) return;

    input.addEventListener('change', async function () {
        const file = input.files[0];
        if (!file) return;

        status.textContent = `Przesyłanie „${file.name}”…`;
        status.className = 'upload-status';

        try {
            const ds = await uploadDatasetFile(file);
            status.innerHTML = `Dodano „${escapeHtml(file.name)}”. ` +
                `<a href="${escapeHtml(ds.url)}" target="_self">Otwórz zestaw</a>`;
            status.className = 'upload-status upload-status-ok';
            loadDatasets(); // odśwież listę o nowo dodany plik
        } catch (err) {
            status.textContent = err.message;
            status.className = 'upload-status upload-status-error';
        } finally {
            input.value = '';
        }
    });
}

// Pobiera z serwera listę zestawów danych użytkownika i je wyświetla.
// Zakładany kontrakt endpointu /api/datasets/ (dopasuj do swojego backendu):
// [
//   { "id": 1, "name": "Basen A — lipiec", "records": 12500,
//     "updated_at": "2026-07-30 14:20", "url": "/dashboard/1/" },
//   ...
// ]
async function loadDatasets() {
    const list = document.getElementById('datasets_list');

    list.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon is-busy">
                <svg class="icon icon-lg"><use href="#i-loader"></use></svg>
            </div>
            <p>Ładowanie zestawów danych…</p>
        </div>`;

    try {
        const response = await fetch('/api/datasets/', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });

        if (!response.ok) {
            throw new Error(`Serwer zwrócił błąd ${response.status}`);
        }

        const datasets = await response.json();
        renderDatasets(datasets);
    } catch (err) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon is-error">
                    <svg class="icon icon-lg"><use href="#i-alert"></use></svg>
                </div>
                <p>Nie udało się pobrać zestawów danych.<br>${escapeHtml(err.message)}</p>
            </div>`;
    }
}

function renderDatasets(datasets) {
    const list = document.getElementById('datasets_list');

    if (!datasets || datasets.length === 0) {
        list.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">
                    <svg class="icon icon-lg"><use href="#i-inbox"></use></svg>
                </div>
                <p>Nie masz jeszcze żadnych zestawów danych.<br>
                   Prześlij pierwszy plik CSV, aby zacząć.</p>
            </div>`;
        return;
    }

    list.innerHTML = datasets.map(ds => `
        <a class="dataset-card${ds.id === window.currentDatasetId ? ' active' : ''}"
           href="${escapeHtml(ds.url)}" target="_self">
            <div class="dataset-card-icon"><svg class="icon icon-lg"><use href="#i-file"></use></svg></div>
            <div class="dataset-card-info">
                <div class="dataset-card-name">${escapeHtml(ds.name)}</div>
                <div class="dataset-card-meta">
                    ${ds.records != null ? escapeHtml(String(ds.records)) + ' rekordów' : ''}
                    ${ds.updated_at ? ' · ' + escapeHtml(ds.updated_at) : ''}
                </div>
            </div>
            <div class="dataset-card-arrow"><svg class="icon"><use href="#i-chevron-right"></use></svg></div>
        </a>
    `).join('');
}

// Kliknięcie w kartę przeładowuje TĘ SAMĄ kartę (target="_self" nadpisuje
// <base target="_blank"> ustawiony dla reszty strony) i wczytuje pod tym
// adresem nowy dashboard.

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}