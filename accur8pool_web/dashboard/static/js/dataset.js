/* ============================================================
   ACCUR8POOL — strona „Zestawy danych”

   Lista wgranych plików i pole do wgrania kolejnego. Wchodzą TYLKO
   zestawy z policzoną wersją przygotowaną — serwer innych nie pokazuje,
   bo nie dałoby się ich otworzyć.

   Ładuj PO common.js (escapeHtml, uploadDatasetFile, fetchJson).
   ============================================================ */

(function () {
    'use strict';

    /** Wgrywanie pliku razem z komunikatem o postępie. */
    class UploadForm {
        constructor(input, status, onUploaded) {
            this.input = input;
            this.status = status;
            this.onUploaded = onUploaded;

            input.addEventListener('change', () => this._onChange());
        }

        async _onChange() {
            const file = this.input.files[0];
            if (!file) return;

            this._say(`Przesyłanie „${file.name}”…`, '');

            try {
                const dataset = await uploadDatasetFile(file);
                this._say(
                    `Dodano „${escapeHtml(file.name)}”. ` +
                    `<a href="${escapeHtml(dataset.url)}" target="_self">Otwórz zestaw</a>`,
                    'upload-status-ok', true);
                this.onUploaded();
            } catch (err) {
                this._say(err.message, 'upload-status-error');
            } finally {
                this.input.value = '';
            }
        }

        _say(text, modifier, isHtml) {
            this.status.className = ('upload-status ' + modifier).trim();
            if (isHtml) {
                this.status.innerHTML = text;
            } else {
                this.status.textContent = text;
            }
        }
    }

    /** Lista kart z zestawami danych.
     *
     *  Kliknięcie w kartę przeładowuje TĘ SAMĄ kartę przeglądarki
     *  (target="_self" nadpisuje <base target="_blank"> ustawiony dla reszty
     *  strony) i wczytuje pod tym adresem nowy dashboard. */
    class DatasetList {
        constructor(container) {
            this.container = container;
        }

        async reload() {
            this._showBusy();
            try {
                this._render(await fetchJson('/api/datasets/'));
            } catch (err) {
                this._showError(err.message);
            }
        }

        _render(datasets) {
            if (!datasets || datasets.length === 0) {
                this._showEmpty();
                return;
            }
            this.container.innerHTML = datasets.map(DatasetList._card).join('');
        }

        static _card(dataset) {
            const active = dataset.id === window.currentDatasetId ? ' active' : '';
            const records = dataset.records != null
                ? escapeHtml(String(dataset.records)) + ' rekordów' : '';
            const updated = dataset.updated_at ? ' · ' + escapeHtml(dataset.updated_at) : '';

            return `
        <a class="dataset-card${active}" href="${escapeHtml(dataset.url)}" target="_self">
            <div class="dataset-card-icon"><svg class="icon icon-lg"><use href="#i-file"></use></svg></div>
            <div class="dataset-card-info">
                <div class="dataset-card-name">${escapeHtml(dataset.name)}</div>
                <div class="dataset-card-meta">${records}${updated}</div>
            </div>
            <div class="dataset-card-arrow"><svg class="icon"><use href="#i-chevron-right"></use></svg></div>
        </a>`;
        }

        _showBusy() {
            this.container.innerHTML = DatasetList._state(
                'i-loader', 'Ładowanie zestawów danych…', 'is-busy');
        }

        _showEmpty() {
            this.container.innerHTML = DatasetList._state(
                'i-inbox',
                'Nie masz jeszcze żadnych zestawów danych.<br>' +
                'Prześlij pierwszy plik CSV, aby zacząć.');
        }

        _showError(message) {
            this.container.innerHTML = DatasetList._state(
                'i-alert',
                'Nie udało się pobrać zestawów danych.<br>' + escapeHtml(message),
                'is-error');
        }

        static _state(icon, html, modifier) {
            return `
        <div class="empty-state">
            <div class="empty-state-icon ${modifier || ''}">
                <svg class="icon icon-lg"><use href="#${icon}"></use></svg>
            </div>
            <p>${html}</p>
        </div>`;
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const container = document.getElementById('datasets_list');
        if (!container) return;

        const list = new DatasetList(container);
        list.reload();

        const input = document.getElementById('upload-input');
        if (input) {
            new UploadForm(input, document.getElementById('upload-status'),
                           () => list.reload());
        }
    });
})();
