/* ============================================================
   ACCUR8POOL — wykres 2D i doczytywanie rozdzielczości

   Wykres startuje w rozdzielczości ekranu (decymacja min/max po stronie
   serwera). Po każdym zoomie dociągamy z serwera wycinek dla widocznego
   zakresu — im głębiej przybliżysz, tym mniej wierszy wpada do jednego
   kubełka, aż w końcu dostajesz surowe próbki. Nic, co byłoby widoczne,
   nie jest gubione.

   Ten plik NIE wie nic o segmentach. Wystawia window.a8Chart — cienkie
   API do wykresu (zoom, zaznaczanie zakresu, prostokąty), z którego
   korzysta segments.js. Podział jest celowy: rysowanie i decymacja
   zmieniają się z innych powodów niż model segmentów.

   Ładuj PO common.js.
   ============================================================ */

(function () {
    'use strict';

    // Serie widoczne od razu po otwarciu pliku. Reszta czeka w legendzie:
    // kilkanaście przebiegów naraz to ściana kresek, w której nie widać
    // żadnego z nich.
    const DEFAULT_VISIBLE_COLUMNS = ['roll', 'gyrx', 'gyry', 'gyrz', 'acc_magnitude'];
    const FALLBACK_VISIBLE_COUNT = 5;

    // Przy przeciąganiu myszą Plotly sypie zdarzeniami dziesiątkami na
    // sekundę — bez tego opóźnienia zalejemy serwer żądaniami.
    const REFINE_DELAY_MS = 220;

    // Margines pytania o zakres: „cały przebieg” w liczbach, bez znajomości
    // długości pliku.
    const FULL_RANGE = Number.MAX_SAFE_INTEGER / 2;

    const PLOT_CONFIG = { responsive: true, displayModeBar: false, scrollZoom: true };

    // ============================================================
    //  WYKRES
    // ============================================================

    /** Sam wykres: postawienie go, zoom i prostokąty.
     *
     *  Gotowość jest osobnym stanem, bo Plotly.newPlot kończy się
     *  asynchronicznie, a segments.js chce narysować prostokąty od razu po
     *  pobraniu listy — może zdążyć wcześniej. */
    class Chart {
        constructor(div, figure) {
            this.div = div;
            this.ready = false;
            this._readyListeners = [];
            this._draw(figure);
        }

        _draw(figure) {
            if (typeof Plotly === 'undefined') {
                this._showLibraryError();
                return;
            }

            Chart._applyDefaultVisibility(figure.data);
            Plotly.newPlot(this.div, figure.data, figure.layout, PLOT_CONFIG)
                .then(() => this._markReady());
        }

        _showLibraryError() {
            console.error('Plotly nie został załadowany.');
            this.div.innerHTML =
                '<div class="empty-state">' +
                '<div class="empty-state-icon is-error"><svg class="icon icon-lg">' +
                '<use href="#i-alert"></use></svg></div>' +
                '<p>Nie udało się załadować biblioteki wykresów.<br>' +
                'Sprawdź połączenie i odśwież stronę.</p></div>';
        }

        /** Włącza serie z listy domyślnej, a gdy plik nie ma żadnej z nich —
         *  pierwsze kilka, żeby wykres nie otwierał się pusty. */
        static _applyDefaultVisibility(traces) {
            const normalize = name => String(name).trim().toLowerCase();
            const wanted = DEFAULT_VISIBLE_COLUMNS.map(normalize);
            const matched = traces.some(trace => wanted.includes(normalize(trace.name)));

            traces.forEach((trace, index) => {
                const on = matched
                    ? wanted.includes(normalize(trace.name))
                    : index < FALLBACK_VISIBLE_COUNT;
                trace.visible = on ? true : 'legendonly';
            });
        }

        _markReady() {
            this.ready = true;
            this._readyListeners.splice(0).forEach(callback => {
                try { callback(); } catch (err) { console.error(err); }
            });
        }

        /** Wywołuje callback, gdy wykres jest gotowy (od razu, jeśli już jest).
         *  Przy braku wykresu nie zostanie wywołany nigdy. */
        onReady(callback) {
            this.ready ? callback() : this._readyListeners.push(callback);
        }

        /** Nazwy serii włączonych w legendzie — tylko o nie pytamy serwer. */
        visibleNames() {
            return (this.div.data || [])
                .filter(trace => trace.visible === true || trace.visible === undefined)
                .map(trace => trace.name);
        }

        /** Ile kubełków ma sens: obszar wykresu w pikselach. Więcej punktów
         *  niż pikseli to dane, których fizycznie nie da się zobaczyć. */
        bucketsForWidth() {
            const width = this.div.clientWidth || 1200;
            return Math.max(400, Math.min(Math.round(width * 1.5), 6000));
        }

        /** Podmienia dane wskazanych śladów. restyle nie rusza zoomu ani
         *  widoczności serii. */
        updateTraces(indices, xs, ys) {
            return Plotly.restyle(this.div, { x: xs, y: ys }, indices);
        }

        /** Przybliża do [x0, x1] z marginesem, żeby zakres nie kleił się do
         *  krawędzi wykresu. */
        zoomTo(x0, x1, padRatio) {
            if (!this.ready) return;
            const ratio = padRatio === undefined ? 0.18 : padRatio;
            const pad = Math.max((x1 - x0) * ratio, 1);
            Plotly.relayout(this.div, {
                'xaxis.range[0]': x0 - pad,
                'xaxis.range[1]': x1 + pad
            });
        }

        resetZoom() {
            if (!this.ready) return;
            Plotly.relayout(this.div, { 'xaxis.autorange': true, 'yaxis.autorange': true });
        }

        /** Podmienia wszystkie prostokąty na wykresie. */
        setShapes(shapes) {
            if (!this.ready) return;
            Plotly.relayout(this.div, { shapes: shapes });
        }

        on(event, handler) {
            if (typeof this.div.on === 'function') this.div.on(event, handler);
        }
    }

    // ============================================================
    //  DOCZYTYWANIE ROZDZIELCZOŚCI
    // ============================================================

    /** Pilnuje, żeby widoczny fragment był zawsze w najlepszej możliwej
     *  rozdzielczości — i żeby nie kosztowało to serwera więcej niż jedno
     *  żądanie na gest. */
    class ResolutionLoader {
        constructor(chart, datasetId, statusEl) {
            this.chart = chart;
            this.datasetId = datasetId;
            this.statusEl = statusEl;

            this._lastRequest = null;   // ostatni pobrany zakres — chroni przed pętlą
            this._timer = null;         // debounce
            this._inFlight = null;      // AbortController bieżącego żądania
        }

        /** Odkłada doczytanie do końca gestu. */
        schedule(x0, x1) {
            clearTimeout(this._timer);
            this._timer = setTimeout(() => this.refine(x0, x1), REFINE_DELAY_MS);
        }

        /** Wymusza ponowne pytanie o ten sam zakres — po włączeniu serii
         *  w legendzie dane dla niej jeszcze nie przyszły. */
        forget() {
            this._lastRequest = null;
        }

        async refine(x0, x1) {
            if (!this.datasetId || !this.chart.ready) return;

            const names = this.chart.visibleNames();
            if (names.length === 0) return;
            if (this._alreadyLoaded(x0, x1, names.length)) return;

            this._lastRequest = { x0: x0, x1: x1, count: names.length };
            const signal = this._restartRequest();
            this._setStatus('Doczytywanie…', true);

            try {
                const payload = await this._fetchRange(x0, x1, names, signal);
                await this._applyPayload(payload);
                this._reportCoverage(payload);
            } catch (err) {
                if (err.name === 'AbortError') return;   // zastąpione nowszym żądaniem
                console.warn('Doczytywanie nie powiodło się:', err);
                this._setStatus('Nie udało się doczytać danych', false);
                this.forget();                           // pozwól spróbować ponownie
            } finally {
                this._inFlight = null;
            }
        }

        /** Ten sam zakres co poprzednio (z tolerancją jednego wiersza). */
        _alreadyLoaded(x0, x1, count) {
            const last = this._lastRequest;
            return Boolean(last &&
                Math.abs(last.x0 - x0) < 1 &&
                Math.abs(last.x1 - x1) < 1 &&
                last.count === count);
        }

        _restartRequest() {
            if (this._inFlight) this._inFlight.abort();
            this._inFlight = new AbortController();
            return this._inFlight.signal;
        }

        async _fetchRange(x0, x1, names, signal) {
            const params = new URLSearchParams({
                x0: String(Math.floor(x0)),
                x1: String(Math.ceil(x1)),
                buckets: String(this.chart.bucketsForWidth()),
                cols: names.join(',')
            });

            const response = await fetch(
                '/api/datasets/' + encodeURIComponent(this.datasetId) + '/range/?' + params,
                { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: signal }
            );
            if (!response.ok) throw new Error('Serwer zwrócił ' + response.status);
            return response.json();
        }

        /** Aktualizujemy tylko te ślady, dla których przyszły dane. */
        _applyPayload(payload) {
            const indices = [], xs = [], ys = [];

            (this.chart.div.data || []).forEach((trace, index) => {
                const series = payload.series[trace.name];
                if (!series) return;
                indices.push(index);
                xs.push(series.x);
                ys.push(series.y);
            });

            return indices.length ? this.chart.updateTraces(indices, xs, ys) : null;
        }

        _reportCoverage(payload) {
            const shown = payload.hi - payload.lo;
            this._setStatus(shown >= payload.total
                ? 'Cały przebieg'
                : shown.toLocaleString('pl-PL') + ' próbek w widoku', false);
        }

        _setStatus(text, busy) {
            if (!this.statusEl) return;
            this.statusEl.textContent = text;
            this.statusEl.classList.toggle('is-busy', !!busy);
        }
    }

    // ============================================================
    //  ZAZNACZANIE ZAKRESU
    //
    //  dragmode 'select' + selectdirection 'h' daje poziomą ramkę
    //  zaznaczenia i zdarzenie plotly_selected z granicami na osi X.
    //  Zaletą względem „weź granice z aktualnego zoomu” jest to, że można
    //  zaznaczyć kilka zakresów bez ruszania widoku — a przy dziesiątkach
    //  uderzeń w jednym nagraniu to podstawowy sposób pracy.
    // ============================================================

    class RangeSelector {
        constructor(chart) {
            this.chart = chart;
            this._handler = null;            // funkcja czekająca na zaznaczenie
            this._cancelListeners = [];
        }

        get active() {
            return this._handler !== null;
        }

        /** Włącza tryb zaznaczania. onPick({x0, x1}) leci raz; tryb wyłącza
         *  się sam, a ponowne uzbrojenie należy do wołającego. */
        begin(onPick) {
            if (!this.chart.ready) return false;
            this._handler = onPick;
            this.chart.div.classList.add('is-selecting');
            Plotly.relayout(this.chart.div, { dragmode: 'select', selectdirection: 'h' });
            return true;
        }

        cancel() {
            if (!this.active) return;
            this._handler = null;
            this._leaveSelectMode();
            this._notifyCancelled();
        }

        /** Powiadomienie o ANULOWANIU trybu — głównie o tym, które przyszło
         *  z Escape, bo wołający o nim nie wie. Udane zaznaczenie zgłasza się
         *  przez callback z begin(): gdyby leciało też tędy, wołający zdążyłby
         *  wyzerować swój stan przed wykonaniem callbacku i zaznaczenie
         *  przepadłoby. */
        onCancelled(callback) {
            this._cancelListeners.push(callback);
        }

        /** Obsługa zdarzenia plotly_selected. */
        handleSelection(event) {
            if (!this.active) return;
            // Zdarzenie bez zakresu to wyczyszczenie zaznaczenia (podwójne
            // kliknięcie), a nie wybór — tryb zostaje włączony.
            if (!event || !event.range || !event.range.x) return;

            const handler = this._handler;
            const x = event.range.x;

            this._handler = null;
            this.chart.div.classList.remove('is-selecting');
            // Sprzątanie po Plotly odkładamy poza jego własny handler —
            // relayout wołany w trakcie obsługi zdarzenia bywa gubiony.
            setTimeout(() => this._clearArtifacts(), 0);

            handler({ x0: Math.min(x[0], x[1]), x1: Math.max(x[0], x[1]) });
        }

        _leaveSelectMode() {
            this.chart.div.classList.remove('is-selecting');
            this._clearArtifacts();
        }

        /** Usuwa ramkę zaznaczenia i przywraca pełną jasność serii — Plotly
         *  w trybie zaznaczania przygasza punkty poza ramką i bez tego wykres
         *  zostaje wyblakły. */
        _clearArtifacts() {
            if (!this.chart.ready) return;
            Promise.resolve()
                .then(() => Plotly.relayout(this.chart.div,
                                            { dragmode: 'zoom', selections: [] }))
                .then(() => Plotly.restyle(this.chart.div, { selectedpoints: null }))
                .catch(err => console.warn('Nie udało się wyczyścić zaznaczenia:', err));
        }

        _notifyCancelled() {
            this._cancelListeners.forEach(callback => {
                try { callback(); } catch (err) { console.error(err); }
            });
        }
    }

    // ============================================================
    //  START
    // ============================================================

    function readJson(id) {
        const el = document.getElementById(id);
        if (!el) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (err) {
            console.error('Nie udało się sparsować #' + id, err);
            return null;
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const figure = readJson('graph-data');
        window.graphData = figure;
        window.currentDatasetId = readJson('dataset-id');

        const graphDiv = document.getElementById('graph');
        if (!graphDiv || !figure || !Array.isArray(figure.data)) return;

        const chart = new Chart(graphDiv, figure);
        const loader = new ResolutionLoader(chart, window.currentDatasetId,
                                            document.getElementById('resolution-status'));
        const selector = new RangeSelector(chart);

        let currentRange = null;     // aktualny zoom albo null przy autorange

        chart.on('plotly_relayout', function (event) {
            if (event['xaxis.range[0]'] !== undefined && event['xaxis.range[1]'] !== undefined) {
                currentRange = { x0: Number(event['xaxis.range[0]']),
                                 x1: Number(event['xaxis.range[1]']) };
                loader.schedule(currentRange.x0, currentRange.x1);
            } else if (event['xaxis.autorange'] === true) {
                currentRange = null;
                loader.schedule(0, FULL_RANGE);
            }
        });

        // Włączenie serii w legendzie = nowa seria bez doczytanych danych.
        // Plotly zmienia `visible` dopiero po swoim handlerze, stąd zwłoka.
        chart.on('plotly_legendclick', function () {
            setTimeout(() => {
                loader.forget();
                loader.schedule(currentRange ? currentRange.x0 : 0,
                                currentRange ? currentRange.x1 : FULL_RANGE);
            }, 60);
        });

        chart.on('plotly_selected', event => selector.handleSelection(event));

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') selector.cancel();
        });

        window.resetZoom = () => chart.resetZoom();
        window.getVisibleRange = () => (currentRange ? Object.assign({}, currentRange) : null);

        // ============================================================
        //  API DLA segments.js i motion3d.js
        //  Skrypty ładowane PO dashboard.js mogą na tym polegać — nasłuch
        //  DOMContentLoaded jest rejestrowany wcześniej, więc wykonuje się
        //  wcześniej.
        // ============================================================
        window.a8Chart = {
            div: graphDiv,
            datasetId: window.currentDatasetId,

            isReady: () => chart.ready,
            onReady: callback => chart.onReady(callback),

            /** Aktualny zakres osi X albo null, gdy widać cały przebieg. */
            getRange: () => currentRange,

            zoomTo: (x0, x1, padRatio) => chart.zoomTo(x0, x1, padRatio),
            resetZoom: () => chart.resetZoom(),
            setShapes: shapes => chart.setShapes(shapes),

            beginSelect: onPick => selector.begin(onPick),
            cancelSelect: () => selector.cancel(),
            isSelecting: () => selector.active,
            onSelectCancelled: callback => selector.onCancelled(callback)
        };
    });
})();
