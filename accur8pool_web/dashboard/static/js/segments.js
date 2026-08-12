/* ============================================================
   ACCUR8POOL — segmenty i fazy

   Struktura danych: Dataset → Segment (jedno uderzenie) → SubSegment
   (jedna z czterech faz uderzenia). Wszystko leży w bazie, więc stan
   przeżywa odświeżenie strony i jest ten sam na każdym urządzeniu.

   Panel ma dwa widoki: listę uderzeń i szczegóły jednego z nich z fazami.
   Zaznaczanie zakresu idzie przez przeciągnięcie po wykresie (a8Chart
   z dashboard.js), więc można oznaczyć kilka uderzeń z rzędu bez ruszania
   zoomu.

   Podział na klasy idzie za tym, co się z czym zmienia:

     SegmentsApi     rozmowa z serwerem
     SegmentStore    stan (lista, podświetlenie, otwarty segment)
     ChartOverlay    prostokąty na wykresie 2D
     PanelView       HTML panelu
     SegmentsPanel   zdarzenia i tryb zaznaczania — spina powyższe

   Ładuj PO common.js i PO dashboard.js (window.a8Chart).
   ============================================================ */

(function () {
    'use strict';

    // Kolejność faz i ich nazwy przychodzą z serwera (modele są jedynym
    // źródłem prawdy). Tutaj zostaje tylko warstwa wizualna.
    const SEGMENT_COLOR = '#2563eb';
    const FALLBACK_COLOR = '#475569';

    // Skróty na prostokątach zostają tutaj — to czysta warstwa wizualna
    // wykresu 2D. KOLOR przychodzi z serwera (phase_types), bo tę samą fazę
    // maluje też animacja 3D, a scenę 3D buduje serwer. Dwie kopie palety
    // znaczyłyby, że ta sama faza ma inny kolor w każdej zakładce.
    const PHASE_SHORT = {
        przygotowanie: 'PRZYG',
        przymierzanie: 'PRZYM',
        uderzenie:     'UDER',
        po_uderzeniu:  'PO UD'
    };

    const formatNumber = value => Number(value).toLocaleString('pl-PL');

    // ============================================================
    //  KOMUNIKACJA Z SERWEREM
    //
    //  Każda mutacja zwraca pełną listę segmentów — numery zależą od
    //  kolejności na osi czasu, więc dodanie uderzenia w środku nagrania
    //  przenumerowuje późniejsze. Nie próbujemy tego odtwarzać lokalnie.
    // ============================================================

    class SegmentsApi {
        constructor(datasetId) {
            this.baseUrl = '/api/datasets/' + encodeURIComponent(datasetId) + '/segments/';
        }

        list() {
            return apiFetch(this.baseUrl, 'GET');
        }

        create(range) {
            return apiFetch(this.baseUrl, 'POST', { start: range.x0, end: range.x1 });
        }

        resize(segmentId, range) {
            return apiFetch(this.baseUrl + segmentId + '/', 'PATCH',
                            { start: range.x0, end: range.x1 });
        }

        remove(segmentId) {
            return apiFetch(this.baseUrl + segmentId + '/', 'DELETE');
        }

        setPhase(segmentId, phase, range) {
            return apiFetch(this._phaseUrl(segmentId, phase), 'PUT',
                            { start: range.x0, end: range.x1 });
        }

        clearPhase(segmentId, phase) {
            return apiFetch(this._phaseUrl(segmentId, phase), 'DELETE');
        }

        _phaseUrl(segmentId, phase) {
            return this.baseUrl + segmentId + '/phases/' + encodeURIComponent(phase) + '/';
        }
    }

    // ============================================================
    //  STAN
    // ============================================================

    /** Lista uderzeń, podświetlenie i otwarty widok faz — plus
     *  powiadamianie o każdej zmianie.
     *
     *  Panel, prostokąty na wykresie i lista w zakładce 3D pokazują tę samą
     *  listę, więc muszą się zmieniać razem. Wołanie ich osobno w każdym
     *  miejscu, które rusza stan, kończyło się tym, że jeden z widoków
     *  zostawał w tyle. */
    class SegmentStore {
        constructor() {
            this.segments = [];
            this.phaseTypes = [];
            this.activeId = null;    // podświetlony na wykresie (jego fazy też widać)
            this.detailId = null;    // otwarty w panelu widok faz
            this.loaded = false;     // pierwsza odpowiedź serwera już przyszła
            this._listeners = [];
        }

        byId(id) {
            return this.segments.find(segment => segment.id === id) || null;
        }

        get detail() {
            return this.detailId !== null ? this.byId(this.detailId) : null;
        }

        apply(payload) {
            this.segments = Array.isArray(payload.segments) ? payload.segments : [];

            if (Array.isArray(payload.phase_types) && payload.phase_types.length) {
                this.phaseTypes = payload.phase_types;
            }

            // Segment mógł zniknąć (usunięty w innej karcie) — nie zostawiamy
            // panelu otwartego na czymś, czego już nie ma.
            if (this.activeId !== null && !this.byId(this.activeId)) this.activeId = null;
            if (this.detailId !== null && !this.byId(this.detailId)) this.detailId = null;

            this.loaded = true;
        }

        /** Najświeższe uderzenie: najwyższe id = ostatnio dodane. */
        newest() {
            return this.segments.reduce(
                (best, segment) => (!best || segment.id > best.id) ? segment : best, null);
        }

        // ---- warstwa wizualna faz ----

        phaseType(key) {
            return this.phaseTypes.find(type => type.key === key) || null;
        }

        phaseLabel(key) {
            const type = this.phaseType(key);
            return type ? type.label : key;
        }

        phaseStyle(key) {
            const type = this.phaseType(key);
            return {
                color: (type && type.color) || FALLBACK_COLOR,
                short: PHASE_SHORT[key] || '···'
            };
        }

        // ---- powiadomienia ----

        snapshot() {
            return { segments: this.segments, phaseTypes: this.phaseTypes,
                     activeId: this.activeId };
        }

        subscribe(callback) {
            this._listeners.push(callback);
            if (this.loaded) callback(this.snapshot());
        }

        notify() {
            const state = this.snapshot();
            this._listeners.forEach(function (callback) {
                // Błąd jednego odbiorcy nie może zatrzymać pozostałych ani
                // przerwać operacji, która akurat zmieniła stan.
                try { callback(state); } catch (err) { console.error('a8Segments:', err); }
            });
        }
    }

    // ============================================================
    //  PROSTOKĄTY NA WYKRESIE
    // ============================================================

    class ChartOverlay {
        constructor(chart, store) {
            this.chart = chart;
            this.store = store;
        }

        draw() {
            if (this.chart) this.chart.setShapes(this.buildShapes());
        }

        buildShapes() {
            const shapes = this.store.segments.map(segment => this._segmentShape(segment));

            // Fazy tylko dla podświetlonego uderzenia. Cztery kolory razy
            // kilkadziesiąt segmentów zamieniłyby wykres w tęczę, w której
            // nie widać już samego przebiegu.
            const active = this.store.byId(this.store.activeId);
            if (active) {
                active.phases.forEach(phase => shapes.push(this._phaseShape(phase)));
            }
            return shapes;
        }

        _segmentShape(segment) {
            const isActive = segment.id === this.store.activeId;
            return {
                type: 'rect',
                xref: 'x',
                yref: 'paper',      // pełna wysokość niezależnie od zoomu w osi Y
                x0: segment.start,
                x1: segment.end,
                y0: 0,
                y1: 1,
                fillcolor: ChartOverlay.rgba(SEGMENT_COLOR, isActive ? 0.13 : 0.06),
                line: {
                    width: isActive ? 1 : 0,
                    color: ChartOverlay.rgba(SEGMENT_COLOR, 0.55),
                    dash: 'dot'
                },
                layer: 'below',
                label: {
                    text: segment.name,
                    textposition: 'top center',
                    // #94a3b8 na białym tle to 2.6:1 — numer segmentu był
                    // ledwie widoczny na własnym prostokącie.
                    font: { size: 11, color: isActive ? '#1d4ed8' : '#64748b' }
                }
            };
        }

        _phaseShape(phase) {
            const style = this.store.phaseStyle(phase.phase);
            return {
                type: 'rect',
                xref: 'x',
                yref: 'paper',
                x0: phase.start,
                x1: phase.end,
                y0: 0,
                y1: 1,
                fillcolor: ChartOverlay.rgba(style.color, 0.15),
                line: { width: 1, color: ChartOverlay.rgba(style.color, 0.65) },
                layer: 'below',
                label: {
                    text: style.short,
                    textposition: 'bottom center',
                    font: { size: 10, color: style.color }
                }
            };
        }

        static rgba(hex, alpha) {
            const value = parseInt(hex.slice(1), 16);
            return 'rgba(' + ((value >> 16) & 255) + ',' + ((value >> 8) & 255) +
                   ',' + (value & 255) + ',' + alpha + ')';
        }
    }

    // ============================================================
    //  WIDOK PANELU
    // ============================================================

    /** Cały HTML panelu. Nie dotyka serwera i nie zmienia stanu — dostaje
     *  store i „co jest właśnie uzbrojone”, oddaje wygląd. */
    class PanelView {
        constructor(elements, store) {
            this.els = elements;
            this.store = store;
        }

        render(armed) {
            const detail = this.store.detail;

            this._renderHeader(detail);
            this._renderHint(armed);
            this._renderToolbar(detail, armed);

            this.els.list.hidden = detail !== null;
            this.els.detail.hidden = detail === null;

            if (detail) {
                this._renderDetail(detail, armed);
            } else {
                this._renderList();
            }
        }

        showError(message) {
            if (!this.els.error) return;
            this.els.error.textContent = message;
            this.els.error.hidden = false;
        }

        clearError() {
            if (this.els.error) this.els.error.hidden = true;
        }

        // ---- nagłówek i pasek narzędzi ----

        _renderHeader(detail) {
            this.els.title.textContent = detail ? 'Segment ' + detail.name : 'Segmenty';
            this.els.subtitle.innerHTML = detail
                ? 'Fazy uderzenia: ' + detail.phases.length + ' / ' + this.store.phaseTypes.length
                : 'Zaznaczone uderzenia: <span id="seg-count">' + this.store.segments.length + '</span>';
            this.els.back.hidden = !detail;
        }

        _renderHint(armed) {
            this.els.hintText.textContent = this._hintText(armed);
            this.els.hint.classList.toggle('is-armed', armed !== null);
        }

        _hintText(armed) {
            if (armed) {
                if (armed.kind === 'segment') {
                    return 'Przeciągnij po wykresie, aby zaznaczyć uderzenie. Esc anuluje.';
                }
                if (armed.kind === 'resize') {
                    return 'Przeciągnij nowy zakres segmentu. Esc anuluje.';
                }
                return 'Przeciągnij zakres fazy „' + this.store.phaseLabel(armed.phase) +
                       '”. Esc anuluje.';
            }
            if (this.store.detailId !== null) {
                return 'Wybierz fazę i przeciągnij jej zakres po wykresie.';
            }
            return 'Zaznacz uderzenie przeciągnięciem po wykresie, potem kliknij je, ' +
                   'aby dodać fazy.';
        }

        /** Pasek narzędzi jest tylko w liście — widok faz ma swój. */
        _renderToolbar(detail, armed) {
            this.els.toolbar.hidden = detail !== null;

            const armingSegment = armed !== null && armed.kind === 'segment';
            this.els.markLabel.textContent = armingSegment
                ? 'Anuluj zaznaczanie' : 'Zaznacz uderzenie';
            this.els.mark.classList.toggle('is-armed', armingSegment);
        }

        // ---- lista uderzeń ----

        _renderList() {
            if (!this.store.segments.length) {
                this.els.list.innerHTML = PanelView.emptyState(
                    'i-scissors', 'Brak segmentów.<br>Zaznacz pierwsze uderzenie na wykresie.');
                return;
            }

            this.els.list.innerHTML = this.store.segments
                .map(segment => this._listItem(segment)).join('');
        }

        _listItem(segment) {
            const isActive = segment.id === this.store.activeId;
            return `
            <div class="segment-item${isActive ? ' active' : ''}"
                 data-id="${segment.id}" role="button" tabindex="0"
                 aria-label="Segment ${escapeHtml(segment.name)} — otwórz fazy">
                <div class="seg-color" style="background:${SEGMENT_COLOR}"></div>
                <div class="seg-info">
                    <div class="seg-name">Segment ${escapeHtml(segment.name)}</div>
                    <div class="phase-dots">
                        ${this._phaseDots(segment)}
                        <span class="phase-dots-label">${segment.phases.length}/${this.store.phaseTypes.length} faz</span>
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
            </div>`;
        }

        _phaseDots(segment) {
            const present = new Set(segment.phases.map(phase => phase.phase));

            return this.store.phaseTypes.map(type => {
                const style = this.store.phaseStyle(type.key);
                const on = present.has(type.key);
                return '<span class="phase-dot' + (on ? ' is-set' : '') + '"' +
                       (on ? ' style="background:' + style.color +
                             ';border-color:' + style.color + '"' : '') +
                       ' title="' + escapeHtml(type.label) +
                       (on ? '' : ' — nie zaznaczono') + '"></span>';
            }).join('');
        }

        // ---- szczegóły jednego uderzenia ----

        _renderDetail(segment, armed) {
            const byPhase = new Map(segment.phases.map(phase => [phase.phase, phase]));
            const resizing = armed !== null && armed.kind === 'resize' && armed.id === segment.id;

            this.els.detail.innerHTML = `
            <div class="seg-detail-head">
                <div class="seg-detail-range">
                    <strong>${formatNumber(segment.start)} – ${formatNumber(segment.end)}</strong>
                    <span>${formatNumber(segment.length)} próbek</span>
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
                ${this.store.phaseTypes
                    .map(type => this._phaseItem(segment, type, byPhase.get(type.key), armed))
                    .join('')}
            </div>`;
        }

        _phaseItem(segment, type, phase, armed) {
            const style = this.store.phaseStyle(type.key);
            const isArmed = armed !== null && armed.kind === 'phase' &&
                            armed.id === segment.id && armed.phase === type.key;

            const range = phase
                ? formatNumber(phase.start) + ' – ' + formatNumber(phase.end) + ' · ' +
                  formatNumber(phase.length) + ' próbek'
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
            </div>`;
        }

        static emptyState(icon, html, busy) {
            return '<div class="empty-state">' +
                   '<div class="empty-state-icon' + (busy ? ' is-busy' : '') + '">' +
                   '<svg class="icon icon-lg"><use href="#' + icon + '"></use></svg></div>' +
                   '<p>' + html + '</p></div>';
        }
    }

    // ============================================================
    //  PANEL: ZDARZENIA I TRYB ZAZNACZANIA
    // ============================================================

    class SegmentsPanel {
        constructor(elements, chart, datasetId) {
            this.els = elements;
            this.chart = chart;
            this.api = new SegmentsApi(datasetId);
            this.store = new SegmentStore();
            this.view = new PanelView(elements, this.store);
            this.overlay = new ChartOverlay(chart, this.store);

            this.armed = null;      // czekamy na przeciągnięcie: patrz arm()

            this._bindPanelEvents();
            this._bindChartEvents();
        }

        // ------------------------------------------------------------
        //  ODŚWIEŻANIE
        // ------------------------------------------------------------

        /** Jedyne miejsce, w którym odświeżają się widoki stanu segmentów. */
        syncViews() {
            this.view.render(this.armed);
            this.overlay.draw();
            this.store.notify();
        }

        applyPayload(payload) {
            this.store.apply(payload);
            this.syncViews();
        }

        async mutate(request) {
            try {
                this.applyPayload(await request);
                this.view.clearError();
            } catch (err) {
                this.view.showError(err.message);
            }
        }

        // ------------------------------------------------------------
        //  TRYB ZAZNACZANIA
        // ------------------------------------------------------------

        /** Uzbraja tryb zaznaczania. target:
         *    {kind:'segment'}            — nowe uderzenie
         *    {kind:'resize', id}         — nowy zakres istniejącego
         *    {kind:'phase', id, phase}   — zakres jednej fazy
         */
        arm(target) {
            if (!this.chart || !this.chart.isReady()) {
                this.view.showError('Wykres nie jest gotowy — zaznaczanie jest niedostępne.');
                return;
            }

            this.armed = target;

            if (!this.chart.beginSelect(range => this._onPick(range))) {
                this.armed = null;
                return;
            }

            // Na telefonie panel segmentów zasłania wykres, po którym trzeba
            // przeciągnąć — bez tego tryb włącza się „w ciemno”.
            if (typeof window.a8CloseMobilePanels === 'function') {
                window.a8CloseMobilePanels();
            }

            this.view.render(this.armed);
        }

        disarm() {
            if (!this.armed) return;
            this.armed = null;                      // najpierw stan, potem wykres —
            if (this.chart) this.chart.cancelSelect();  // inaczej nasłuch anulowania zapętli
            this.view.render(this.armed);
        }

        /** Przełącza tryb: to samo żądanie drugi raz go anuluje. */
        toggleArm(target, isSame) {
            if (this.armed && isSame(this.armed)) {
                this.disarm();
                return;
            }
            this.disarm();
            this.arm(target);
        }

        async _onPick(range) {
            const target = this.armed;
            this.armed = null;
            if (!target) return;

            try {
                this.applyPayload(await this._save(target, range));
                this.view.clearError();

                if (target.kind === 'segment') this._afterNewSegment();
            } catch (err) {
                this.view.showError(err.message);
                this.view.render(this.armed);
            }
        }

        _save(target, range) {
            if (target.kind === 'segment') return this.api.create(range);
            if (target.kind === 'resize') return this.api.resize(target.id, range);
            return this.api.setPhase(target.id, target.phase, range);
        }

        /** Świeże uderzenie podświetlamy i od razu uzbrajamy tryb ponownie.
         *  Przy nagraniu z kilkudziesięcioma uderzeniami klikanie „Zaznacz”
         *  przed każdym z nich byłoby jedyną czynnością tej pracy. */
        _afterNewSegment() {
            const created = this.store.newest();
            if (created) this.store.activeId = created.id;
            this.syncViews();
            this.arm({ kind: 'segment' });
        }

        // ------------------------------------------------------------
        //  ZDARZENIA
        // ------------------------------------------------------------

        _bindChartEvents() {
            if (!this.chart) return;
            // Tryb potrafi anulować też Escape obsłużony w dashboard.js —
            // wtedy trzeba zsynchronizować etykiety przycisków.
            this.chart.onSelectCancelled(() => {
                if (!this.armed) return;
                this.armed = null;
                this.view.render(this.armed);
            });
        }

        _bindPanelEvents() {
            this.els.list.addEventListener('click', e => this._onListClick(e));
            this.els.list.addEventListener('keydown', e => this._onListKeydown(e));
            this.els.detail.addEventListener('click', e => this._onDetailClick(e));

            this.els.mark.addEventListener('click', () => {
                this.toggleArm({ kind: 'segment' }, armed => armed.kind === 'segment');
            });

            this.els.reset.addEventListener('click', function () {
                if (typeof window.resetZoom === 'function') window.resetZoom();
            });

            this.els.back.addEventListener('click', () => {
                this.disarm();
                this.store.detailId = null;   // activeId zostaje — fazy nadal widać
                this.syncViews();
            });
        }

        _onListClick(e) {
            const item = e.target.closest('.segment-item');
            if (!item) return;

            const segment = this.store.byId(Number(item.dataset.id));
            if (!segment) return;

            const action = e.target.closest('[data-action]');
            const name = action ? action.dataset.action : null;

            if (name === 'delete') {
                this._deleteSegment(segment);
            } else if (name === 'zoom') {
                this.store.activeId = segment.id;
                this._zoomTo(segment);
                this.syncViews();
            } else {
                this._openDetail(segment);
            }
        }

        /** Wiersz jest role="button", więc musi reagować na Enter/Spację. */
        _onListKeydown(e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;

            const item = e.target.closest('.segment-item');
            if (!item) return;

            e.preventDefault();
            const segment = this.store.byId(Number(item.dataset.id));
            if (segment) this._openDetail(segment);
        }

        _onDetailClick(e) {
            const action = e.target.closest('[data-action]');
            if (!action) return;

            const segment = this.store.detail;
            if (!segment) return;

            const phaseItem = action.closest('.phase-item');
            const phaseKey = phaseItem ? phaseItem.dataset.phase : null;

            switch (action.dataset.action) {
                case 'zoom':
                    this._zoomTo(segment);
                    break;

                case 'resize':
                    this.toggleArm(
                        { kind: 'resize', id: segment.id },
                        armed => armed.kind === 'resize' && armed.id === segment.id);
                    break;

                case 'delete':
                    this._deleteSegment(segment, true);
                    break;

                case 'mark':
                    this._markPhase(segment, phaseKey);
                    break;

                case 'clear':
                    this.mutate(this.api.clearPhase(segment.id, phaseKey));
                    break;
            }
        }

        // ------------------------------------------------------------
        //  AKCJE
        // ------------------------------------------------------------

        /** Wejście w segment kończy zaznaczanie nowych uderzeń — inaczej
         *  pasek narzędzi znika (widok faz ma swój), a tryb zostaje włączony
         *  i kolejne przeciągnięcie tworzy segment, choć nic już o tym nie
         *  mówi. */
        _openDetail(segment) {
            this.disarm();
            this.store.activeId = segment.id;
            this.store.detailId = segment.id;
            this._zoomTo(segment);
            this.syncViews();
        }

        _deleteSegment(segment, fromDetail) {
            if (!confirm('Usunąć segment ' + segment.name + ' wraz z fazami?')) return;

            if (fromDetail || this.store.detailId === segment.id) {
                this.store.detailId = null;
            }
            this.mutate(this.api.remove(segment.id));
        }

        _markPhase(segment, phaseKey) {
            const same = armed => armed.kind === 'phase' && armed.phase === phaseKey;

            if (this.armed && same(this.armed)) {
                this.disarm();
                return;
            }
            this.disarm();
            // Fazę zaznacza się wewnątrz uderzenia, więc najpierw pokazujemy
            // je w całości na wykresie.
            this._zoomTo(segment);
            this.arm({ kind: 'phase', id: segment.id, phase: phaseKey });
        }

        _zoomTo(segment) {
            if (this.chart) this.chart.zoomTo(segment.start, segment.end);
        }

        // ------------------------------------------------------------
        //  START
        // ------------------------------------------------------------

        load() {
            this.els.list.innerHTML = PanelView.emptyState(
                'i-loader', 'Ładowanie segmentów…', true);

            this.api.list()
                .then(payload => {
                    this.view.clearError();
                    this.applyPayload(payload);
                    // Wykres mógł jeszcze nie skończyć się rysować — wtedy
                    // prostokąty dorysują się, gdy będzie gotowy.
                    if (this.chart) this.chart.onReady(() => this.overlay.draw());
                })
                .catch(err => {
                    this.els.list.innerHTML = '';
                    this.view.showError('Nie udało się pobrać segmentów: ' + err.message);
                });
        }
    }

    // ============================================================
    //  MONTAŻ
    // ============================================================

    document.addEventListener('DOMContentLoaded', function () {
        const panelEl = document.getElementById('segments-panel');
        if (!panelEl) return;

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

        const datasetId = window.currentDatasetId;
        const panel = new SegmentsPanel(els, window.a8Chart || null, datasetId || '');

        // ============================================================
        //  API DLA motion3d.js
        //
        //  Zakładka „Ruch 3D” animuje POJEDYNCZY segment, więc jej lista
        //  wyboru to dokładnie ta sama lista, którą trzyma ten panel.
        //  Zamiast drugiego żądania o te same dane wystawiamy stan tutaj.
        //
        //  Skrypty ładowane PO segments.js widzą ten obiekt gotowy, bo ich
        //  nasłuch DOMContentLoaded rejestruje się później niż nasz. Dlatego
        //  przypisanie stoi PRZED sprawdzeniem datasetId niżej — inaczej
        //  przy wejściu bez wybranego pliku obiekt nigdy by nie powstał.
        // ============================================================
        window.a8Segments = {
            /** Segment po id albo null. */
            byId: id => panel.store.byId(id),

            all: () => panel.store.segments,
            getActiveId: () => panel.store.activeId,

            /** Powiadomienie o każdej zmianie listy segmentów albo
             *  podświetlenia. Wywoływane od razu, jeśli dane są już
             *  wczytane — odbiorca nie musi wiedzieć, czy zdążył się
             *  zarejestrować przed pierwszą odpowiedzią serwera. */
            onChange: callback => panel.store.subscribe(callback)
        };

        if (!datasetId) {
            els.toolbar.hidden = true;
            els.hint.hidden = true;
            els.list.innerHTML = PanelView.emptyState(
                'i-folder', 'Najpierw wybierz zestaw danych,<br>aby zaznaczać segmenty.');
            return;
        }

        panel.load();
    });
})();
