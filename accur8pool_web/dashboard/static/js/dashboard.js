/* ============================================================
   ACCUR8POOL — wykres, segmenty, doczytywanie rozdzielczości

   Wykres startuje w rozdzielczości ekranu (decymacja min/max po
   stronie serwera). Po każdym zoomie dociągamy z serwera wycinek
   dla widocznego zakresu — im głębiej przybliżysz, tym mniej
   wierszy wpada do jednego kubełka, aż w końcu dostajesz surowe
   próbki. Nic, co byłoby widoczne, nie jest gubione.
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
            }).then(() => { plotReady = true; });
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

    // ---------- stan segmentów ----------
    let currentRange = null;
    const segments = [];
    let activeSegmentId = null;

    if (graphDiv && typeof graphDiv.on === 'function') {

        graphDiv.on('plotly_relayout', function (event) {
            if (event['xaxis.range[0]'] !== undefined && event['xaxis.range[1]'] !== undefined) {
                const x0 = Number(event['xaxis.range[0]']);
                const x1 = Number(event['xaxis.range[1]']);
                currentRange = { x0: x0, x1: x1 };
                scheduleRefine(x0, x1);
            } else if (event['xaxis.autorange'] === true) {
                currentRange = null;
                scheduleRefine(0, Number.MAX_SAFE_INTEGER / 2);
            }
        });

        // Włączenie serii w legendzie = nowa seria bez doczytanych danych
        graphDiv.on('plotly_legendclick', function () {
            setTimeout(() => {
                lastRequest = null;
                const r = currentRange;
                scheduleRefine(r ? r.x0 : 0, r ? r.x1 : Number.MAX_SAFE_INTEGER / 2);
            }, 60);
        });
    }

    // ---------- segmenty ----------
    const createBtn = document.getElementById('create');
    const listEl = document.getElementById('segment_list');
    const countEl = document.getElementById('seg-count');

    if (createBtn) {
        createBtn.addEventListener('click', function () {
            if (!plotReady) return;
            if (!currentRange) {
                alert('Najpierw przybliż fragment wykresu (zoom), aby zdefiniować zakres segmentu.');
                return;
            }
            const name = prompt('Nazwa segmentu:');
            if (!name || !name.trim()) return;

            segments.push({
                id: Date.now(),
                name: name.trim(),
                x0: Math.min(currentRange.x0, currentRange.x1),
                x1: Math.max(currentRange.x0, currentRange.x1),
                color: '#3b82f6'
            });
            activeSegmentId = segments[segments.length - 1].id;
            renderSegments();
            drawSegments();
            updateCount();
        });
    }

    function renderSegments() {
        if (!listEl) return;

        if (segments.length === 0) {
            listEl.innerHTML =
                '<div class="empty-state"><div class="empty-state-icon">✂️</div>' +
                '<p>Brak segmentów.<br>Zdefiniuj pierwszy zakres na wykresie.</p></div>';
            return;
        }

        listEl.innerHTML = segments.map(seg => `
            <div class="segment-item ${seg.id === activeSegmentId ? 'active' : ''}" data-id="${seg.id}">
                <div class="seg-color" style="background:${seg.color}"></div>
                <div class="seg-info">
                    <div class="seg-name">${escapeHtml(seg.name)}</div>
                    <div class="seg-range">${seg.x0.toFixed(1)} – ${seg.x1.toFixed(1)}</div>
                </div>
                <div class="seg-actions">
                    <button type="button" class="seg-btn" data-action="delete" title="Usuń">×</button>
                </div>
            </div>
        `).join('');
    }

    if (listEl) {
        listEl.addEventListener('click', function (e) {
            const item = e.target.closest('.segment-item');
            if (!item) return;
            const id = Number(item.dataset.id);

            if (e.target.closest('[data-action="delete"]')) {
                const i = segments.findIndex(s => s.id === id);
                if (i > -1) {
                    segments.splice(i, 1);
                    if (activeSegmentId === id) activeSegmentId = null;
                    renderSegments();
                    drawSegments();
                    updateCount();
                }
                return;
            }

            activeSegmentId = id;
            renderSegments();
            drawSegments();
        });
    }

    function drawSegments() {
        if (!plotReady) return;
        Plotly.relayout(graphDiv, {
            shapes: segments.map(seg => ({
                type: 'rect', x0: seg.x0, x1: seg.x1, y0: 0, y1: 1, yref: 'paper',
                fillcolor: seg.id === activeSegmentId
                    ? 'rgba(59, 130, 246, 0.22)'
                    : 'rgba(148, 163, 184, 0.10)',
                line: { width: 0 }, layer: 'below'
            }))
        });
    }

    function updateCount() {
        if (countEl) countEl.textContent = segments.length;
    }

    window.resetZoom = function () {
        if (!plotReady) return;
        Plotly.relayout(graphDiv, { 'xaxis.autorange': true, 'yaxis.autorange': true });
    };

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});