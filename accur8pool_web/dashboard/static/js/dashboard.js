/* ============================================================
   ACCUR8POOL — wykres i doczytywanie rozdzielczości

   Wykres startuje w rozdzielczości ekranu (decymacja min/max po
   stronie serwera). Po każdym zoomie dociągamy z serwera wycinek
   dla widocznego zakresu — im głębiej przybliżysz, tym mniej
   wierszy wpada do jednego kubełka, aż w końcu dostajesz surowe
   próbki. Nic, co byłoby widoczne, nie jest gubione.

   Ten plik NIE wie nic o segmentach. Wystawia window.a8Chart —
   cienkie API do wykresu (zoom, zaznaczanie zakresu, prostokąty),
   z którego korzysta segments.js. Podział jest celowy: rysowanie
   i decymacja zmieniają się z innych powodów niż model segmentów.
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

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

    const fig = readJson('graph-data');
    window.graphData = fig;
    window.currentDatasetId = readJson('dataset-id');

    const graphDiv = document.getElementById('graph');

    const DEFAULT_VISIBLE_COLUMNS = ['roll', 'gyrx', 'gyry', 'gyrz', 'acc_magnitude'];
    const FALLBACK_VISIBLE_COUNT = 5;

    const normalize = name => String(name).trim().toLowerCase();
    const normalizedDefaults = DEFAULT_VISIBLE_COLUMNS.map(normalize);

    let plotReady = false;
    const readyListeners = [];

    function markReady() {
        plotReady = true;
        readyListeners.splice(0).forEach(cb => {
            try { cb(); } catch (err) { console.error(err); }
        });
    }

    // ---------- inicjalizacja ----------
    if (graphDiv && fig && Array.isArray(fig.data)) {

        if (typeof Plotly === 'undefined') {
            graphDiv.innerHTML =
                '<div class="empty-state"><div class="empty-state-icon">⚠️</div>' +
                '<p>Nie udało się załadować biblioteki wykresów.<br>' +
                'Sprawdź połączenie i odśwież stronę.</p></div>';
            console.error('Plotly nie został załadowany.');
        } else {
            const matchCount = fig.data.filter(t => normalizedDefaults.includes(normalize(t.name))).length;

            fig.data.forEach((trace, i) => {
                trace.visible = matchCount > 0
                    ? (normalizedDefaults.includes(normalize(trace.name)) ? true : 'legendonly')
                    : (i < FALLBACK_VISIBLE_COUNT ? true : 'legendonly');
            });

            Plotly.newPlot(graphDiv, fig.data, fig.layout, {
                responsive: true,
                displayModeBar: false,
                scrollZoom: true
            }).then(markReady);
        }
    }

    // ============================================================
    //  DOCZYTYWANIE ROZDZIELCZOŚCI
    // ============================================================
    const datasetId = window.currentDatasetId;
    const statusEl = document.getElementById('resolution-status');

    let lastRequest = null;      // ostatni pobrany zakres — chroni przed pętlą
    let pending = null;          // timer debounce
    let inFlight = null;         // AbortController bieżącego żądania

    function setStatus(text, busy) {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.classList.toggle('is-busy', !!busy);
    }

    // Ile kubełków ma sens: obszar wykresu w pikselach. Więcej punktów
    // niż pikseli to dane, których fizycznie nie da się zobaczyć.
    function bucketsForWidth() {
        const w = graphDiv ? graphDiv.clientWidth : 1200;
        return Math.max(400, Math.min(Math.round(w * 1.5), 6000));
    }

    function visibleNames() {
        return (graphDiv.data || [])
            .filter(t => t.visible === true || t.visible === undefined)
            .map(t => t.name);
    }

    async function refine(x0, x1) {
        if (!datasetId || !plotReady) return;

        const buckets = bucketsForWidth();
        const names = visibleNames();
        if (names.length === 0) return;

        // Ten sam zakres co poprzednio (z tolerancją) — nie pytaj ponownie
        if (lastRequest &&
            Math.abs(lastRequest.x0 - x0) < 1 &&
            Math.abs(lastRequest.x1 - x1) < 1 &&
            lastRequest.count === names.length) {
            return;
        }
        lastRequest = { x0, x1, count: names.length };

        if (inFlight) inFlight.abort();
        inFlight = new AbortController();

        setStatus('Doczytywanie…', true);

        const params = new URLSearchParams({
            x0: String(Math.floor(x0)),
            x1: String(Math.ceil(x1)),
            buckets: String(buckets),
            cols: names.join(',')
        });

        try {
            const res = await fetch(
                '/api/datasets/' + encodeURIComponent(datasetId) + '/range/?' + params,
                { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: inFlight.signal }
            );
            if (!res.ok) throw new Error('Serwer zwrócił ' + res.status);

            const payload = await res.json();

            // Aktualizujemy tylko te ślady, dla których przyszły dane.
            // restyle nie rusza zoomu ani widoczności serii.
            const idx = [];
            const xs = [];
            const ys = [];

            (graphDiv.data || []).forEach((trace, i) => {
                const s = payload.series[trace.name];
                if (!s) return;
                idx.push(i);
                xs.push(s.x);
                ys.push(s.y);
            });

            if (idx.length) {
                await Plotly.restyle(graphDiv, { x: xs, y: ys }, idx);
            }

            const shown = payload.hi - payload.lo;
            setStatus(shown >= payload.total
                ? 'Cały przebieg'
                : shown.toLocaleString('pl-PL') + ' próbek w widoku', false);

        } catch (err) {
            if (err.name === 'AbortError') return;   // zastąpione nowszym żądaniem
            console.warn('Doczytywanie nie powiodło się:', err);
            setStatus('Nie udało się doczytać danych', false);
            lastRequest = null;                       // pozwól spróbować ponownie
        } finally {
            inFlight = null;
        }
    }

    function scheduleRefine(x0, x1) {
        clearTimeout(pending);
        // 220 ms: przy przeciąganiu myszą Plotly sypie zdarzeniami
        // dziesiątkami na sekundę — bez tego zalejemy serwer
        pending = setTimeout(() => refine(x0, x1), 220);
    }

    const FULL_RANGE = Number.MAX_SAFE_INTEGER / 2;

    // ============================================================
    //  ZAZNACZANIE ZAKRESU
    //
    //  dragmode 'select' + selectdirection 'h' daje poziomą ramkę
    //  zaznaczenia i zdarzenie plotly_selected z granicami na osi X.
    //  Zaletą względem „weź granice z aktualnego zoomu” jest to, że
    //  można zaznaczyć kilka zakresów bez ruszania widoku — a przy
    //  dziesiątkach uderzeń w jednym nagraniu to podstawowy sposób pracy.
    // ============================================================

    let currentRange = null;     // aktualny zoom albo null przy autorange
    let selectHandler = null;    // funkcja czekająca na zaznaczenie
    window.getVisibleRange = () => (currentRange ? Object.assign({}, currentRange) : null);
    const cancelListeners = [];

    // Nasłuch dotyczy WYŁĄCZNIE anulowania (Escape, cancelSelect). O udanym
    // zaznaczeniu wołający dowiaduje się ze swojego callbacku i tylko tam —
    // gdyby powiadomienie leciało też wtedy, wołający zdążyłby wyzerować
    // swój stan przed wykonaniem callbacku i zaznaczenie przepadłoby.
    function notifyCancelled() {
        cancelListeners.forEach(cb => {
            try { cb(); } catch (err) { console.error(err); }
        });
    }

    function clearSelectionArtifacts() {
        if (!plotReady) return;
        // 'selections: []' usuwa narysowaną ramkę, restyle przywraca pełną
        // jasność serii — Plotly w trybie zaznaczania przygasza punkty poza
        // ramką i bez tego wykres zostaje wyblakły.
        Promise.resolve()
            .then(() => Plotly.relayout(graphDiv, { dragmode: 'zoom', selections: [] }))
            .then(() => Plotly.restyle(graphDiv, { selectedpoints: null }))
            .catch(err => console.warn('Nie udało się wyczyścić zaznaczenia:', err));
    }

    function beginSelect(onPick) {
        if (!plotReady) return false;
        selectHandler = onPick;
        graphDiv.classList.add('is-selecting');
        Plotly.relayout(graphDiv, { dragmode: 'select', selectdirection: 'h' });
        return true;
    }

    function cancelSelect() {
        if (!selectHandler) return;
        selectHandler = null;
        graphDiv.classList.remove('is-selecting');
        clearSelectionArtifacts();
        notifyCancelled();
    }

    if (graphDiv && typeof graphDiv.on === 'function') {

        graphDiv.on('plotly_relayout', function (event) {
            if (event['xaxis.range[0]'] !== undefined && event['xaxis.range[1]'] !== undefined) {
                const x0 = Number(event['xaxis.range[0]']);
                const x1 = Number(event['xaxis.range[1]']);
                currentRange = { x0: x0, x1: x1 };
                scheduleRefine(x0, x1);
            } else if (event['xaxis.autorange'] === true) {
                currentRange = null;
                scheduleRefine(0, FULL_RANGE);
            }
        });

        // Włączenie serii w legendzie = nowa seria bez doczytanych danych
        graphDiv.on('plotly_legendclick', function () {
            setTimeout(() => {
                lastRequest = null;
                const r = currentRange;
                scheduleRefine(r ? r.x0 : 0, r ? r.x1 : FULL_RANGE);
            }, 60);
        });

        graphDiv.on('plotly_selected', function (event) {
            if (!selectHandler) return;
            // Zdarzenie bez zakresu to wyczyszczenie zaznaczenia (podwójne
            // kliknięcie), a nie wybór — tryb zostaje włączony.
            if (!event || !event.range || !event.range.x) return;

            const handler = selectHandler;
            const x = event.range.x;

            selectHandler = null;
            graphDiv.classList.remove('is-selecting');
            // Sprzątanie po Plotly odkładamy poza jego własny handler —
            // relayout wołany w trakcie obsługi zdarzenia bywa gubiony.
            setTimeout(clearSelectionArtifacts, 0);

            handler({ x0: Math.min(x[0], x[1]), x1: Math.max(x[0], x[1]) });
        });
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') cancelSelect();
    });

    window.resetZoom = function () {
        if (!plotReady) return;
        Plotly.relayout(graphDiv, { 'xaxis.autorange': true, 'yaxis.autorange': true });
    };

    // ============================================================
    //  API DLA segments.js
    //  Skrypty ładowane PO dashboard.js (segments.js, fullscreen.js)
    //  mogą na tym polegać — nasłuch DOMContentLoaded jest rejestrowany
    //  wcześniej, więc wykonuje się wcześniej.
    // ============================================================
    window.a8Chart = {
        div: graphDiv,
        datasetId: datasetId,

        isReady: () => plotReady,

        /** Wywołuje cb, gdy wykres jest gotowy (od razu, jeśli już jest).
         *  Przy braku wykresu cb nie zostanie wywołane nigdy. */
        onReady(cb) {
            plotReady ? cb() : readyListeners.push(cb);
        },

        /** Aktualny zakres osi X albo null, gdy widać cały przebieg. */
        getRange: () => currentRange,

        /** Przybliża do [x0, x1] z marginesem, żeby zakres nie kleił się
         *  do krawędzi wykresu. */
        zoomTo(x0, x1, padRatio) {
            if (!plotReady) return;
            const ratio = padRatio === undefined ? 0.18 : padRatio;
            const pad = Math.max((x1 - x0) * ratio, 1);
            Plotly.relayout(graphDiv, {
                'xaxis.range[0]': x0 - pad,
                'xaxis.range[1]': x1 + pad
            });
        },

        /** Włącza tryb zaznaczania. onPick({x0, x1}) leci raz; tryb
         *  wyłącza się sam, a ponowne uzbrojenie należy do wołającego. */
        beginSelect,
        cancelSelect,
        isSelecting: () => selectHandler !== null,

        /** Powiadomienie o ANULOWANIU trybu zaznaczania — głównie o tym,
         *  które przyszło z Escape, bo wołający o nim nie wie. Udane
         *  zaznaczenie zgłasza się przez callback z beginSelect. */
        onSelectCancelled(cb) {
            cancelListeners.push(cb);
        },

        /** Podmienia wszystkie prostokąty na wykresie. relayout z samymi
         *  shapes nie rusza zoomu ani widoczności serii. */
        setShapes(shapes) {
            if (!plotReady) return;
            Plotly.relayout(graphDiv, { shapes: shapes });
        },

        resetZoom: () => window.resetZoom()
    };
});