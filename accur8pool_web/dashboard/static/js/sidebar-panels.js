/* ============================================================
   ACCUR8POOL — lewy sidebar

   Dwie sprawy: zwijanie sidebaru do szyny z ikonami oraz panel z listą
   plików. Stan zwinięcia trzyma klasa na <body> (steruje nią CSS)
   i localStorage, więc przeżywa przeładowanie strony i przejście między
   dashboardem a listą zestawów danych.

   Ładuj PO common.js (escapeHtml, uploadDatasetFile, fetchJson).
   ============================================================ */

(function () {
    'use strict';

    const STORAGE_KEY = 'a8.sidebar.collapsed';
    const MOBILE_MAX = 768;

    // Szerokość sidebaru animuje CSS przez 0.22 s — Plotly przelicza się
    // dopiero po jej zakończeniu, inaczej złapałby rozmiar w połowie ruchu.
    const RESIZE_AFTER_MS = 260;

    // ============================================================
    //  ZWIJANIE SIDEBARU
    //  Osobno od paneli — działa też tam, gdzie paneli nie ma.
    // ============================================================

    class Sidebar {
        constructor(toggle) {
            this.toggle = toggle;

            // Stan z pamięci wchodzi tylko na szerokim ekranie: na telefonie
            // sidebar jest szufladą i „zwinięty” nic tam nie znaczy.
            this.setCollapsed(Sidebar._readStored() && window.innerWidth > MOBILE_MAX,
                              false);

            toggle.addEventListener('click', () => this.setCollapsed(!this.collapsed));

            // Skrót jak w edytorach kodu — sidebar zwija się bez sięgania myszą.
            document.addEventListener('keydown', e => {
                if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey &&
                    (e.key === 'b' || e.key === 'B')) {
                    e.preventDefault();
                    this.setCollapsed(!this.collapsed);
                }
            });
        }

        get collapsed() {
            return document.body.classList.contains('sidebar-collapsed');
        }

        setCollapsed(collapsed, remember) {
            document.body.classList.toggle('sidebar-collapsed', collapsed);
            this._updateToggle(collapsed);

            if (remember !== false) Sidebar._store(collapsed);

            // Plotly nie wie, że kontener wykresu zmienił szerokość przez CSS —
            // bez tego wykres zostaje w starym rozmiarze aż do resize okna.
            setTimeout(Sidebar._resizePlots, RESIZE_AFTER_MS);
        }

        _updateToggle(collapsed) {
            const label = collapsed ? 'Rozwiń menu' : 'Zwiń menu';
            this.toggle.setAttribute('aria-expanded', String(!collapsed));
            this.toggle.setAttribute('aria-label', label);
            this.toggle.setAttribute('title', label + ' (Ctrl + B)');
        }

        static _resizePlots() {
            if (!window.Plotly) return;
            document.querySelectorAll('.plot-area, #graph').forEach(function (el) {
                if (el.data) Plotly.Plots.resize(el);
            });
        }

        /** localStorage bywa niedostępny (tryb prywatny, zablokowane
         *  ciasteczka) — brak pamięci stanu nie może wywalić nawigacji. */
        static _readStored() {
            try {
                return localStorage.getItem(STORAGE_KEY) === '1';
            } catch (err) {
                return false;
            }
        }

        static _store(collapsed) {
            try {
                localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
            } catch (err) { /* trudno */ }
        }
    }

    // ============================================================
    //  PANEL Z LISTĄ PLIKÓW
    // ============================================================

    class FilesPanel {
        constructor(elements, sidebar) {
            this.els = elements;
            this.sidebar = sidebar;
            this.loaded = false;

            this.els.open.addEventListener('click', () => this.open());
            this.els.back.addEventListener('click', () => this.close());

            if (this.els.upload) {
                this.els.upload.addEventListener('change', () => this._onUpload());
            }
        }

        open() {
            // W szynie ikon lista plików byłaby rzędem identycznych ikonek
            // dokumentów bez nazw — wejście do niej rozwija sidebar.
            if (this.sidebar && this.sidebar.collapsed) this.sidebar.setCollapsed(false);

            this.els.track.classList.add('show-files');
            if (!this.loaded) this.reload();
        }

        close() {
            this.els.track.classList.remove('show-files');
        }

        async _onUpload() {
            const file = this.els.upload.files[0];
            if (!file) return;

            try {
                await uploadDatasetFile(file);
                this.reload();       // odśwież listę o nowo dodany plik
            } catch (err) {
                alert(err.message);
            } finally {
                this.els.upload.value = '';
            }
        }

        /** Ten sam endpoint co na stronie „Zestawy danych”. */
        async reload() {
            const list = this.els.list;
            list.innerHTML = '<div class="nav-panel-empty">Ładowanie…</div>';

            try {
                const datasets = await fetchJson('/api/datasets/');
                this.loaded = true;

                list.innerHTML = (datasets && datasets.length)
                    ? datasets.map(FilesPanel._item).join('')
                    : '<div class="nav-panel-empty">Brak dostępnych plików.</div>';
            } catch (err) {
                this.loaded = false;
                list.innerHTML = '<div class="nav-panel-empty">Nie udało się pobrać ' +
                                 'listy plików.<br>' + escapeHtml(err.message) + '</div>';
            }
        }

        /** Kliknięcie w plik przeładowuje TĘ SAMĄ kartę przeglądarki
         *  (target="_self" nadpisuje <base target="_blank"> ustawiony dla
         *  reszty strony) i wczytuje pod tym adresem nowy dashboard. */
        static _item(dataset) {
            const active = dataset.id === window.currentDatasetId ? ' active' : '';
            return `
            <a href="${escapeHtml(dataset.url)}" target="_self"
               title="${escapeHtml(dataset.name)}"
               class="nav-item nav-file-item${active}">
                <span class="nav-icon"><svg class="icon"><use href="#i-file"></use></svg></span>
                <span class="nav-file-name">${escapeHtml(dataset.name)}</span>
            </a>`;
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const toggle = document.getElementById('sidebar-toggle');
        const sidebar = toggle ? new Sidebar(toggle) : null;

        const els = {
            track: document.getElementById('nav-menu-track'),
            open: document.getElementById('open-files-panel'),
            back: document.getElementById('back-to-main'),
            list: document.getElementById('sidebar-files-list'),
            upload: document.getElementById('sidebar-upload-input')
        };

        // Strona bez sidebaru z panelami — zostaje samo zwijanie.
        if (!els.track || !els.open || !els.back) return;

        new FilesPanel(els, sidebar);
    });
})();
