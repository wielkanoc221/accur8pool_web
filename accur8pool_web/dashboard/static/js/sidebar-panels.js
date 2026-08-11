/* ============================================================
   ACCUR8POOL — lewy sidebar

   Dwie sprawy: przełączanie panelu „menu / lista plików” oraz zwijanie
   sidebaru do szyny z ikonami. Stan zwinięcia trzyma klasa na <body>
   (steruje nią CSS) i localStorage, więc przeżywa przeładowanie strony
   i przejście między dashboardem a listą zestawów danych.
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

    // ============================================================
    //  ZWIJANIE SIDEBARU
    //  Osobno od paneli — działa też tam, gdzie paneli nie ma.
    // ============================================================
    const STORAGE_KEY = 'a8.sidebar.collapsed';
    const MOBILE_MAX = 768;
    const toggle = document.getElementById('sidebar-toggle');

    /** localStorage bywa niedostępny (tryb prywatny, zablokowane cookies) —
     *  brak pamięci stanu nie może wywalić całej nawigacji. */
    function readStored() {
        try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch (err) { return false; }
    }

    function store(collapsed) {
        try { localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch (err) { /* trudno */ }
    }

    function isCollapsed() {
        return document.body.classList.contains('sidebar-collapsed');
    }

    function setCollapsed(collapsed, remember) {
        document.body.classList.toggle('sidebar-collapsed', collapsed);
        if (toggle) {
            toggle.setAttribute('aria-expanded', String(!collapsed));
            const opis = collapsed ? 'Rozwiń menu' : 'Zwiń menu';
            toggle.setAttribute('aria-label', opis);
            toggle.setAttribute('title', opis + ' (Ctrl + B)');
        }
        if (remember !== false) store(collapsed);

        // Plotly nie wie, że kontener wykresu zmienił szerokość przez CSS —
        // bez tego wykres zostaje w starym rozmiarze aż do resize okna.
        // Przeliczenie po zakończeniu animacji szerokości (0.22s w CSS).
        setTimeout(function () {
            if (!window.Plotly) return;
            document.querySelectorAll('.plot-area, #graph').forEach(function (el) {
                if (el.data) Plotly.Plots.resize(el);
            });
        }, 260);
    }

    if (toggle) {
        // Stan z pamięci wchodzi tylko na szerokim ekranie: na telefonie
        // sidebar jest szufladą i „zwinięty” nic tam nie znaczy.
        setCollapsed(readStored() && window.innerWidth > MOBILE_MAX, false);

        toggle.addEventListener('click', function () {
            setCollapsed(!isCollapsed());
        });

        // Skrót jak w edytorach kodu — sidebar zwija się bez sięgania myszą.
        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey &&
                (e.key === 'b' || e.key === 'B')) {
                e.preventDefault();
                setCollapsed(!isCollapsed());
            }
        });
    }

    // ============================================================
    //  PANELE: MENU / LISTA PLIKÓW
    // ============================================================
    const track = document.getElementById('nav-menu-track');
    const openBtn = document.getElementById('open-files-panel');
    const backBtn = document.getElementById('back-to-main');

    if (!track || !openBtn || !backBtn) return; // strona bez sidebaru z panelami

    let filesLoaded = false;

    openBtn.addEventListener('click', function () {
        // W szynie ikon lista plików byłaby rzędem identycznych ikonek
        // dokumentów bez nazw — wejście do niej rozwija sidebar.
        if (isCollapsed()) setCollapsed(false);
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
                   title="${escapeHtml(ds.name)}"
                   class="nav-item nav-file-item${ds.id === window.currentDatasetId ? ' active' : ''}">
                    <span class="nav-icon"><svg class="icon"><use href="#i-file"></use></svg></span>
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