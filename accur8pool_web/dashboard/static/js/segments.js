/* ============================================================
   ACCUR8POOL — segmenty i fazy

   Struktura danych: Dataset → Segment (jedno uderzenie) → SubSegment
   (jedna z czterech faz uderzenia). Wszystko leży w bazie, więc stan
   przeżywa odświeżenie strony i jest ten sam na każdym urządzeniu.

   Panel ma dwa widoki: listę uderzeń i szczegóły jednego z nich z fazami.
   Zaznaczanie zakresu idzie przez przeciągnięcie po wykresie (a8Chart
   z dashboard.js), więc można oznaczyć kilka uderzeń z rzędu bez
   ruszania zoomu.

   Ładuj PO upload.js (getCsrfToken) i PO dashboard.js (window.a8Chart).
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {

    const panel = document.getElementById('segments-panel');
    if (!panel) return;

    const els = {
        back: document.getElementById('seg-back'),
        title: document.getElementById('seg-title'),
        subtitle: document.getElementById('seg-subtitle'),
        hint: document.getElementById('seg-hint'),
        hintText: document.getElementById('seg-hint-text'),
        toolbar: document.getElementById('seg-toolbar'),
        mark: document.getElementById('seg-mark'),
        markLabel: document.getElementById('seg-mark-label'),
        reset: document.getElementById('seg-reset'),
        error: document.getElementById('seg-error'),
        list: document.getElementById('seg-list'),
        detail: document.getElementById('seg-detail')
    };

    if (!els.list || !els.detail) return;

    // ---------- kolory ----------
    // Kolejność faz i ich nazwy przychodzą z serwera (modele są jedynym
    // źródłem prawdy). Tutaj zostaje tylko warstwa wizualna.
    const SEGMENT_COLOR = '#2563eb';

    // Skróty na prostokątach zostają tutaj — to czysta warstwa wizualna
    // wykresu 2D. KOLOR przychodzi z serwera (phase_types), bo tę samą
    // fazę maluje też animacja 3D, a scenę 3D buduje serwer. Dwie kopie
    // palety znaczyłyby, że ta sama faza ma inny kolor w każdej zakładce.
    const PHASE_SHORT = {
        przygotowanie: 'PRZYG',
        przymierzanie: 'PRZYM',
        uderzenie:     'UDER',
        po_uderzeniu:  'PO UD'
    };
    const FALLBACK_COLOR = '#475569';

    function styleFor(key) {
        const type = phaseTypes.find(t => t.key === key);
        return {
            color: (type && type.color) || FALLBACK_COLOR,
            short: PHASE_SHORT[key] || '···'
        };
    }

    // ---------- stan ----------
    const chart = window.a8Chart || null;
    const datasetId = window.currentDatasetId;

    let segments = [];
    let phaseTypes = [];
    let activeId = null;    // podświetlony na wykresie (jego fazy też widać)
    let detailId = null;    // otwarty w panelu widok faz
    let armed = null;       // czekamy na przeciągnięcie: patrz arm()
    let loaded = false;     // pierwsza odpowiedź serwera już przyszła

    const baseUrl = '/api/datasets/' + encodeURIComponent(datasetId) + '/segments/';

    // ============================================================
    //  KOMUNIKACJA Z SERWEREM
    //
    //  Każda mutacja zwraca pełną listę segmentów — numery zależą od
    //  kolejności na osi czasu, więc dodanie uderzenia w środku nagrania
    //  przenumerowuje późniejsze. Nie próbujemy tego odtwarzać lokalnie.
    // ============================================================

    async function api(method, url, body) {
        const opts = {
            method: method,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        };

        if (method !== 'GET') {
            opts.headers['X-CSRFToken'] = getCsrfToken();
        }
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }

        const res = await fetch(url, opts);
        const payload = await res.json().catch(() => ({}));

        if (!res.ok) {
            throw new Error(payload.error || 'Błąd serwera (' + res.status + ')');
        }
        return payload;
    }

    function applyPayload(payload) {
        segments = Array.isArray(payload.segments) ? payload.segments : [];

        if (Array.isArray(payload.phase_types) && payload.phase_types.length) {
            phaseTypes = payload.phase_types;
        }

        // Segment mógł zniknąć (usunięty w innej karcie) — nie zostawiamy
        // panelu otwartego na czymś, czego już nie ma.
        if (activeId !== null && !byId(activeId)) activeId = null;
        if (detailId !== null && !byId(detailId)) detailId = null;

        loaded = true;
        syncViews();
    }

    const byId = id => segments.find(s => s.id === id) || null;

    // ============================================================
    //  ZAZNACZANIE
    // ============================================================

    /** Uzbraja tryb zaznaczania. target:
     *    {kind:'segment'}                — nowe uderzenie
     *    {kind:'resize', id}             — nowy zakres istniejącego
     *    {kind:'phase', id, phase}       — zakres jednej fazy
     */
    function arm(target) {
        if (!chart || !chart.isReady()) {
            showError('Wykres nie jest gotowy — zaznaczanie jest niedostępne.');
            return;
        }

        armed = target;

        if (!chart.beginSelect(onPick)) {
            armed = null;
            return;
        }

        // Na telefonie panel segmentów zasłania wykres, po którym trzeba
        // przeciągnąć — bez tego tryb włącza się „w ciemno”.
        if (typeof window.a8CloseMobilePanels === 'function') {
            window.a8CloseMobilePanels();
        }

        render();
    }

    function disarm() {
        if (!armed) return;
        armed = null;                 // najpierw stan, potem wykres —
        if (chart) chart.cancelSelect();  // inaczej listener niżej zapętli
        render();
    }

    async function onPick(range) {
        const target = armed;
        armed = null;
        if (!target) return;

        try {
            let payload;

            if (target.kind === 'segment') {
                payload = await api('POST', baseUrl, { start: range.x0, end: range.x1 });
            } else if (target.kind === 'resize') {
                payload = await api('PATCH', baseUrl + target.id + '/',
                                    { start: range.x0, end: range.x1 });
            } else {
                payload = await api('PUT',
                    baseUrl + target.id + '/phases/' + encodeURIComponent(target.phase) + '/',
                    { start: range.x0, end: range.x1 });
            }

            clearError();
            applyPayload(payload);

            if (target.kind === 'segment') {
                // Świeże uderzenie podświetlamy (najwyższe id = ostatnio
                // dodane) i od razu uzbrajamy tryb ponownie. Przy nagraniu
                // z kilkudziesięcioma uderzeniami klikanie „Zaznacz” przed
                // każdym z nich byłoby jedyną czynnością tej pracy.
                const created = segments.reduce(
                    (best, s) => (!best || s.id > best.id) ? s : best, null
                );
                if (created) activeId = created.id;
                syncViews();
                arm({ kind: 'segment' });
            }

        } catch (err) {
            showError(err.message);
            render();
        }
    }

    if (chart) {
        // Tryb potrafi anulować też Escape obsłużony w dashboard.js —
        // wtedy trzeba zsynchronizować etykiety przycisków.
        chart.onSelectCancelled(function () {
            if (!armed) return;
            armed = null;
            render();
        });
    }

    // ============================================================
    //  RYSOWANIE NA WYKRESIE
    // ============================================================

    function rgba(hex, alpha) {
        const n = parseInt(hex.slice(1), 16);
        return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + alpha + ')';
    }

    function buildShapes() {
        const shapes = [];

        segments.forEach(seg => {
            const isActive = seg.id === activeId;
            shapes.push({
                type: 'rect',
                xref: 'x',
                yref: 'paper',      // pełna wysokość niezależnie od zoomu w osi Y
                x0: seg.start,
                x1: seg.end,
                y0: 0,
                y1: 1,
                fillcolor: rgba(SEGMENT_COLOR, isActive ? 0.13 : 0.06),
                line: {
                    width: isActive ? 1 : 0,
                    color: rgba(SEGMENT_COLOR, 0.55),
                    dash: 'dot'
                },
                layer: 'below',
                label: {
                    text: seg.name,
                    textposition: 'top center',
                    // #94a3b8 na białym tle to 2.6:1 — numer segmentu był
                    // ledwie widoczny na własnym prostokącie.
                    font: { size: 11, color: isActive ? '#1d4ed8' : '#64748b' }
                }
            });
        });

        // Fazy tylko dla podświetlonego uderzenia. Cztery kolory razy
        // kilkadziesiąt segmentów zamieniłyby wykres w tęczę, w której
        // nie widać już samego przebiegu.
        const active = byId(activeId);
        if (active) {
            active.phases.forEach(p => {
                const style = styleFor(p.phase);
                shapes.push({
                    type: 'rect',
                    xref: 'x',
                    yref: 'paper',
                    x0: p.start,
                    x1: p.end,
                    y0: 0,
                    y1: 1,
                    fillcolor: rgba(style.color, 0.15),
                    line: { width: 1, color: rgba(style.color, 0.65) },
                    layer: 'below',
                    label: {
                        text: style.short,
                        textposition: 'bottom center',
                        font: { size: 10, color: style.color }
                    }
                });
            });
        }

        return shapes;
    }

    function drawShapes() {
        if (chart) chart.setShapes(buildShapes());
    }

    // ============================================================
    //  WIDOK
    // ============================================================

    const fmt = n => Number(n).toLocaleString('pl-PL');

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function phaseLabel(key) {
        const type = phaseTypes.find(t => t.key === key);
        return type ? type.label : key;
    }

    function hintText() {
        if (armed) {
            if (armed.kind === 'segment') {
                return 'Przeciągnij po wykresie, aby zaznaczyć uderzenie. Esc anuluje.';
            }
            if (armed.kind === 'resize') {
                return 'Przeciągnij nowy zakres segmentu. Esc anuluje.';
            }
            return 'Przeciągnij zakres fazy „' + phaseLabel(armed.phase) + '”. Esc anuluje.';
        }
        if (detailId !== null) {
            return 'Wybierz fazę i przeciągnij jej zakres po wykresie.';
        }
        return 'Zaznacz uderzenie przeciągnięciem po wykresie, potem kliknij je, aby dodać fazy.';
    }

    function render() {
        const detail = detailId !== null ? byId(detailId) : null;

        // ---- nagłówek ----
        els.title.textContent = detail ? 'Segment ' + detail.name : 'Segmenty';
        els.subtitle.innerHTML = detail
            ? 'Fazy uderzenia: ' + detail.phases.length + ' / ' + phaseTypes.length
            : 'Zaznaczone uderzenia: <span id="seg-count">' + segments.length + '</span>';
        els.back.hidden = !detail;

        // ---- podpowiedź ----
        els.hintText.textContent = hintText();
        els.hint.classList.toggle('is-armed', armed !== null);

        // ---- pasek narzędzi (tylko w liście; w szczegółach ma swój) ----
        els.toolbar.hidden = detail !== null;

        const armingSegment = armed !== null && armed.kind === 'segment';
        els.markLabel.textContent = armingSegment ? 'Anuluj zaznaczanie' : 'Zaznacz uderzenie';
        els.mark.classList.toggle('is-armed', armingSegment);

        // ---- widoki ----
        els.list.hidden = detail !== null;
        els.detail.hidden = detail === null;

        detail ? renderDetail(detail) : renderList();
    }

    function renderList() {
        if (!segments.length) {
            els.list.innerHTML =
                '<div class="empty-state">' +
                '<div class="empty-state-icon"><svg class="icon icon-lg">' +
                '<use href="#i-scissors"></use></svg></div>' +
                '<p>Brak segmentów.<br>Zaznacz pierwsze uderzenie na wykresie.</p></div>';
            return;
        }

        els.list.innerHTML = segments.map(seg => `
            <div class="segment-item${seg.id === activeId ? ' active' : ''}"
                 data-id="${seg.id}" role="button" tabindex="0"
                 aria-label="Segment ${escapeHtml(seg.name)} — otwórz fazy">
                <div class="seg-color" style="background:${SEGMENT_COLOR}"></div>
                <div class="seg-info">
                    <div class="seg-name">Segment ${escapeHtml(seg.name)}</div>
                    <div class="phase-dots">
                        ${phaseDots(seg)}
                        <span class="phase-dots-label">${seg.phases.length}/${phaseTypes.length} faz</span>
                    </div>
                </div>
                <div class="seg-actions">
                    <button type="button" class="seg-btn" data-action="zoom"
                            title="Pokaż na wykresie" aria-label="Pokaż na wykresie">
                        <svg class="icon"><use href="#i-target"></use></svg>
                    </button>
                    <button type="button" class="seg-btn seg-btn-danger" data-action="delete"
                            title="Usuń segment" aria-label="Usuń segment">
                        <svg class="icon"><use href="#i-trash"></use></svg>
                    </button>
                </div>
            </div>
        `).join('');
    }

    function phaseDots(seg) {
        const set = new Set(seg.phases.map(p => p.phase));
        return phaseTypes.map(type => {
            const style = styleFor(type.key);
            const on = set.has(type.key);
            return '<span class="phase-dot' + (on ? ' is-set' : '') + '"' +
                   (on ? ' style="background:' + style.color + ';border-color:' + style.color + '"' : '') +
                   ' title="' + escapeHtml(type.label) + (on ? '' : ' — nie zaznaczono') + '"></span>';
        }).join('');
    }

    function renderDetail(seg) {
        const byPhase = new Map(seg.phases.map(p => [p.phase, p]));
        const resizing = armed !== null && armed.kind === 'resize' && armed.id === seg.id;

        els.detail.innerHTML = `
            <div class="seg-detail-head">
                <div class="seg-detail-range">
                    <strong>${fmt(seg.start)} – ${fmt(seg.end)}</strong>
                    <span>${fmt(seg.length)} próbek</span>
                </div>
                <div class="seg-detail-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-action="zoom">
                        <svg class="icon" aria-hidden="true"><use href="#i-target"></use></svg>
                        Pokaż
                    </button>
                    <button type="button" class="btn btn-ghost btn-sm${resizing ? ' is-armed' : ''}"
                            data-action="resize">
                        <svg class="icon" aria-hidden="true"><use href="#i-scissors"></use></svg>
                        ${resizing ? 'Anuluj' : 'Popraw zakres'}
                    </button>
                    <button type="button" class="btn btn-ghost btn-sm btn-danger-ghost" data-action="delete">
                        <svg class="icon" aria-hidden="true"><use href="#i-trash"></use></svg>
                        Usuń
                    </button>
                </div>
            </div>

            <div class="phase-section-title">Fazy uderzenia</div>

            <div class="phase-list">
                ${phaseTypes.map(type => renderPhaseItem(seg, type, byPhase.get(type.key))).join('')}
            </div>
        `;
    }

    function renderPhaseItem(seg, type, phase) {
        const style = styleFor(type.key);
        const isArmed = armed !== null && armed.kind === 'phase' &&
                        armed.id === seg.id && armed.phase === type.key;

        const range = phase
            ? fmt(phase.start) + ' – ' + fmt(phase.end) + ' · ' + fmt(phase.length) + ' próbek'
            : 'nie zaznaczono';

        return `
            <div class="phase-item${phase ? ' is-set' : ''}${isArmed ? ' is-armed' : ''}"
                 data-phase="${escapeHtml(type.key)}">
                <span class="phase-swatch" style="background:${style.color}"></span>
                <div class="phase-info">
                    <div class="phase-name">${escapeHtml(type.label)}</div>
                    <div class="phase-range">${range}</div>
                </div>
                <div class="phase-actions">
                    <button type="button" class="phase-btn${isArmed ? ' is-armed' : ''}" data-action="mark">
                        ${isArmed ? 'Anuluj' : (phase ? 'Popraw' : 'Zaznacz')}
                    </button>
                    ${phase
                        ? '<button type="button" class="seg-btn seg-btn-danger" data-action="clear"' +
                          ' title="Usuń fazę" aria-label="Usuń fazę">' +
                          '<svg class="icon"><use href="#i-x"></use></svg></button>'
                        : ''}
                </div>
            </div>
        `;
    }

    function showError(message) {
        if (!els.error) return;
        els.error.textContent = message;
        els.error.hidden = false;
    }

    function clearError() {
        if (els.error) els.error.hidden = true;
    }

    // ============================================================
    //  ZDARZENIA
    // ============================================================

    function focusSegment(seg) {
        // Wejście w segment kończy zaznaczanie nowych uderzeń — inaczej
        // pasek narzędzi znika (widok faz ma swój), a tryb zostaje włączony
        // i kolejne przeciągnięcie tworzy segment, choć nic już o tym nie mówi.
        disarm();
        activeId = seg.id;
        detailId = seg.id;
        if (chart) chart.zoomTo(seg.start, seg.end);
        syncViews();
    }

    async function mutate(method, url, body) {
        try {
            applyPayload(await api(method, url, body));
            clearError();
        } catch (err) {
            showError(err.message);
        }
    }

    // ---- lista ----
    els.list.addEventListener('click', function (e) {
        const item = e.target.closest('.segment-item');
        if (!item) return;

        const seg = byId(Number(item.dataset.id));
        if (!seg) return;

        const action = e.target.closest('[data-action]');

        if (action && action.dataset.action === 'delete') {
            if (!confirm('Usunąć segment ' + seg.name + ' wraz z fazami?')) return;
            if (detailId === seg.id) detailId = null;
            mutate('DELETE', baseUrl + seg.id + '/');
            return;
        }

        if (action && action.dataset.action === 'zoom') {
            activeId = seg.id;
            if (chart) chart.zoomTo(seg.start, seg.end);
            syncViews();
            return;
        }

        focusSegment(seg);
    });

    // Klawiatura: wiersz jest role="button", więc musi reagować na Enter/Space
    els.list.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const item = e.target.closest('.segment-item');
        if (!item) return;
        e.preventDefault();
        const seg = byId(Number(item.dataset.id));
        if (seg) focusSegment(seg);
    });

    // ---- szczegóły segmentu ----
    els.detail.addEventListener('click', function (e) {
        const action = e.target.closest('[data-action]');
        if (!action) return;

        const seg = detailId !== null ? byId(detailId) : null;
        if (!seg) return;

        const phaseItem = action.closest('.phase-item');
        const phaseKey = phaseItem ? phaseItem.dataset.phase : null;

        switch (action.dataset.action) {
            case 'zoom':
                if (chart) chart.zoomTo(seg.start, seg.end);
                break;

            case 'resize':
                if (armed && armed.kind === 'resize' && armed.id === seg.id) {
                    disarm();
                } else {
                    disarm();
                    arm({ kind: 'resize', id: seg.id });
                }
                break;

            case 'delete':
                if (!confirm('Usunąć segment ' + seg.name + ' wraz z fazami?')) return;
                detailId = null;
                mutate('DELETE', baseUrl + seg.id + '/');
                break;

            case 'mark':
                if (armed && armed.kind === 'phase' && armed.phase === phaseKey) {
                    disarm();
                } else {
                    disarm();
                    // Fazę zaznacza się wewnątrz uderzenia, więc najpierw
                    // pokazujemy je w całości na wykresie.
                    if (chart) chart.zoomTo(seg.start, seg.end);
                    arm({ kind: 'phase', id: seg.id, phase: phaseKey });
                }
                break;

            case 'clear':
                mutate('DELETE', baseUrl + seg.id + '/phases/' + encodeURIComponent(phaseKey) + '/');
                break;
        }
    });

    // ---- pasek narzędzi ----
    els.mark.addEventListener('click', function () {
        armed && armed.kind === 'segment' ? disarm() : arm({ kind: 'segment' });
    });

    els.reset.addEventListener('click', function () {
        if (typeof window.resetZoom === 'function') window.resetZoom();
    });

    els.back.addEventListener('click', function () {
        disarm();
        detailId = null;      // activeId zostaje — fazy nadal widać na wykresie
        syncViews();
    });

    // ============================================================
    //  API DLA motion3d.js
    //
    //  Zakładka „Ruch 3D” animuje POJEDYNCZY segment, więc jej lista
    //  wyboru to dokładnie ta sama lista, którą trzyma ten panel. Zamiast
    //  drugiego żądania o te same dane wystawiamy stan tutaj.
    //
    //  Skrypty ładowane PO segments.js widzą ten obiekt gotowy, bo ich
    //  nasłuch DOMContentLoaded rejestruje się później niż nasz. Dlatego
    //  przypisanie stoi PRZED sprawdzeniem datasetId niżej — inaczej przy
    //  wejściu bez wybranego pliku obiekt nigdy by nie powstał.
    // ============================================================

    const changeListeners = [];

    function snapshot() {
        return { segments: segments, phaseTypes: phaseTypes, activeId: activeId };
    }

    /** Jedyne miejsce, w którym odświeżają się widoki stanu segmentów.
     *
     *  Panel, prostokąty na wykresie i lista w zakładce 3D pokazują tę
     *  samą listę, więc muszą się zmieniać razem. Wołanie ich osobno
     *  w każdym miejscu, które rusza `segments` albo `activeId`, kończyło
     *  się tym, że jeden z widoków zostawał w tyle. */
    function syncViews() {
        render();
        drawShapes();

        const stan = snapshot();
        changeListeners.forEach(function (cb) {
            // Błąd jednego odbiorcy nie może zatrzymać pozostałych ani
            // przerwać operacji, która akurat zmieniła stan.
            try { cb(stan); } catch (err) { console.error('a8Segments:', err); }
        });
    }

    window.a8Segments = {
        /** Segment po id albo null. */
        byId: byId,

        all: () => segments,
        getActiveId: () => activeId,

        /** Powiadomienie o każdej zmianie listy segmentów albo
         *  podświetlenia. Wywoływane od razu, jeśli dane są już
         *  wczytane — odbiorca nie musi wiedzieć, czy zdążył się
         *  zarejestrować przed pierwszą odpowiedzią serwera. */
        onChange(cb) {
            changeListeners.push(cb);
            if (loaded) cb(snapshot());
        }
    };

    // ============================================================
    //  START
    // ============================================================

    if (!datasetId) {
        els.toolbar.hidden = true;
        els.hint.hidden = true;
        els.list.innerHTML =
            '<div class="empty-state">' +
            '<div class="empty-state-icon"><svg class="icon icon-lg">' +
            '<use href="#i-folder"></use></svg></div>' +
            '<p>Najpierw wybierz zestaw danych,<br>aby zaznaczać segmenty.</p></div>';
        return;
    }

    els.list.innerHTML =
        '<div class="empty-state">' +
        '<div class="empty-state-icon is-busy"><svg class="icon icon-lg">' +
        '<use href="#i-loader"></use></svg></div>' +
        '<p>Ładowanie segmentów…</p></div>';

    api('GET', baseUrl)
        .then(payload => {
            clearError();
            applyPayload(payload);
            // Wykres mógł jeszcze nie skończyć się rysować — wtedy prostokąty
            // dorysują się w momencie, w którym będzie gotowy.
            if (chart) chart.onReady(drawShapes);
        })
        .catch(err => {
            els.list.innerHTML = '';
            showError('Nie udało się pobrać segmentów: ' + err.message);
        });
});
