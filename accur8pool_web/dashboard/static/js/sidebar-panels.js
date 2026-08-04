document.addEventListener('DOMContentLoaded', function () {
    const track = document.getElementById('nav-menu-track');
    const openBtn = document.getElementById('open-files-panel');
    const backBtn = document.getElementById('back-to-main');

    if (!track || !openBtn || !backBtn) return; // strona bez sidebaru z panelami

    let filesLoaded = false;

    openBtn.addEventListener('click', function () {
        track.classList.add('show-files');
        if (!filesLoaded) {
            loadSidebarFiles();
        }
    });

    backBtn.addEventListener('click', function () {
        track.classList.remove('show-files');
    });

    const uploadInput = document.getElementById('sidebar-upload-input');
    if (uploadInput) {
        uploadInput.addEventListener('change', async function () {
            const file = uploadInput.files[0];
            if (!file) return;

            try {
                await uploadDatasetFile(file);
                loadSidebarFiles(); // odśwież listę o nowo dodany plik
            } catch (err) {
                alert(err.message);
            } finally {
                uploadInput.value = '';
            }
        });
    }

    // Pobiera z serwera listę dostępnych plików/zestawów danych i wstawia
    // je do panelu w sidebarze. Ten sam endpoint co na stronie "Zestawy danych":
    // GET /api/datasets/ -> [{ id, name, records, updated_at, url }, ...]
    async function loadSidebarFiles() {
        const list = document.getElementById('sidebar-files-list');
        list.innerHTML = '<div class="nav-panel-empty">Ładowanie…</div>';

        try {
            const response = await fetch('/api/datasets/', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });

            if (!response.ok) {
                throw new Error(`Błąd serwera (${response.status})`);
            }

            const datasets = await response.json();
            filesLoaded = true;

            if (!datasets || datasets.length === 0) {
                list.innerHTML = '<div class="nav-panel-empty">Brak dostępnych plików.</div>';
                return;
            }

            // Kliknięcie w plik przeładowuje TĘ SAMĄ kartę (target="_self"
            // nadpisuje <base target="_blank"> ustawiony dla reszty strony)
            // i wczytuje pod tym adresem nowy dashboard.
            list.innerHTML = datasets.map(ds => `
                <a href="${escapeHtml(ds.url)}" target="_self"
                   class="nav-item nav-file-item${ds.id === window.currentDatasetId ? ' active' : ''}">
                    <span class="nav-icon">📄</span>
                    <span class="nav-file-name">${escapeHtml(ds.name)}</span>
                </a>
            `).join('');
        } catch (err) {
            filesLoaded = false;
            list.innerHTML = `<div class="nav-panel-empty">Nie udało się pobrać listy plików.<br>${escapeHtml(err.message)}</div>`;
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});