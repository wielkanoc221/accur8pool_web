/* ============================================================
   ACCUR8POOL — "break shot" v2
   Zastępuje login.js i register.js (sam wykrywa stronę).
   Ładuj po pool.css.

   Kluczowa zmiana względem v1: animowany jest PRAWDZIWY przycisk.
   Kij uderza w niego, on się toczy i obraca razem z numerem.
   W osobnej warstwie jedzie tylko refleks świetlny — bo on jako
   jedyny nie może się kręcić razem z kulą.

   Dystans i kąt obrotu liczone są z realnych współrzędnych, więc
   animacja jest poprawna na każdej szerokości ekranu.
   ============================================================ */

(function () {
    'use strict';

    const form = document.getElementById('login-form') || document.getElementById('register-form');
    if (!form) return;

    const ball = document.getElementById('ball') || document.getElementById('register-ball');
    if (!ball) return;

    const card = ball.closest('.auth-card') || form;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let busy = false;

    // ---------- warstwy ----------
    const back = document.createElement('div');   // pod bilą: łuza, przyciemnienie
    back.id = 'pool-fx-back';
    const front = document.createElement('div');  // nad bilą: kij, kreda, refleks
    front.id = 'pool-fx-front';
    document.body.append(back, front);

    function make(cls, parent) {
        const node = document.createElement('div');
        node.className = cls;
        (parent || front).appendChild(node);
        return node;
    }

    const wait = ms => new Promise(res => setTimeout(res, ms));

    // Animacja jako obietnica — sekwencję da się wtedy pisać liniowo,
    // zamiast zagnieżdżać pięć setTimeoutów jeden w drugim.
    function animate(node, frames, options) {
        const anim = node.animate(frames, Object.assign({ fill: 'forwards' }, options));
        return anim.finished.catch(() => {});   // przerwana animacja to nie błąd
    }

    // ---------- pomiary sceny ----------
    function measure() {
        const r = ball.getBoundingClientRect();
        const cardRect = card.getBoundingClientRect();
        const size = r.width;

        const pocketSize = size * 1.55;
        const pocketCx = Math.min(cardRect.right + size * 0.2,
                                  window.innerWidth - pocketSize * 0.55);
        const ballCx = r.left + size / 2;
        const ballCy = r.top + size / 2;

        // Za krótki dystans czyta się jak szturchnięcie, nie jak zagranie
        const travel = Math.max(pocketCx - ballCx, size * 1.8);

        // Kąt obrotu = droga / obwód koła. Zaszyte na sztywno 720deg było
        // poprawne tylko dla jednej szerokości ekranu — stąd brało się
        // wrażenie wirowania w miejscu na telefonie.
        const spin = (travel / (Math.PI * size)) * 360;

        return { r, size, ballCx, ballCy, travel, spin, pocketSize, pocketCx };
    }

    function buildProps(m) {
        // Łuza (warstwa tylna — bila przelatuje NAD nią, a potem w nią wpada)
        const pocket = make('fx-pocket', back);
        pocket.style.width = m.pocketSize + 'px';
        pocket.style.height = m.pocketSize + 'px';
        pocket.style.left = (m.ballCx + m.travel - m.pocketSize / 2) + 'px';
        pocket.style.top = (m.ballCy - m.pocketSize / 2) + 'px';

        // Refleks — leci z bilą, ale się nie obraca
        const glare = make('fx-glare');
        glare.style.left = m.r.left + 'px';
        glare.style.top = m.r.top + 'px';
        glare.style.width = m.size + 'px';
        glare.style.height = m.size + 'px';

        // Smuga prędkości
        const streak = make('fx-streak');
        streak.style.width = (m.size * 1.6) + 'px';
        streak.style.height = (m.size * 0.18) + 'px';
        streak.style.left = (m.r.left - m.size * 1.5) + 'px';
        streak.style.top = (m.ballCy - m.size * 0.09) + 'px';

        // Kij
        const cueW = Math.max(190, Math.min(360, m.size * 4.2));
        const cueH = Math.max(9, m.size * 0.15);
        const cue = make('fx-cue');
        cue.style.width = cueW + 'px';
        cue.style.height = cueH + 'px';
        cue.style.left = (m.r.left - 8 - cueW) + 'px';
        cue.style.top = (m.ballCy - cueH / 2) + 'px';
        make('fx-cue-tip', cue);

        return { pocket, glare, streak, cue, tip: cue.querySelector('.fx-cue-tip') };
    }

    function cleanup() {
        back.innerHTML = '';
        front.innerHTML = '';
        back.classList.remove('dim');
    }

    // ---------- kreda + pierścień uderzenia ----------
    function impactBurst(x, y, size) {
        const ring = make('fx-ring');
        ring.style.width = size * 0.9 + 'px';
        ring.style.height = size * 0.9 + 'px';
        ring.style.left = (x - size * 0.45) + 'px';
        ring.style.top = (y - size * 0.45) + 'px';
        setTimeout(() => ring.remove(), 500);

        for (let i = 0; i < 12; i++) {
            const p = make('fx-chalk');
            // Kreda leci w lewo i w górę — tam, skąd przyszedł kij
            const angle = (Math.PI * 0.45) + Math.random() * Math.PI * 1.1;
            const dist = 26 + Math.random() * 46;
            p.style.left = x + 'px';
            p.style.top = y + 'px';
            p.style.setProperty('--dx', Math.cos(angle) * dist + 'px');
            p.style.setProperty('--dy', (Math.sin(angle) * dist - 18) + 'px');
            p.style.setProperty('--dr', (Math.random() * 540 - 270) + 'deg');
            p.style.animationDelay = (Math.random() * 40) + 'ms';
            setTimeout(() => p.remove(), 700);
        }
    }

    // ============================================================
    //  ZAGRANIE
    // ============================================================
    async function playShot() {
        document.body.classList.add('fx-shooting');
        const m = measure();
        const p = buildProps(m);

        if (reduced) {
            p.pocket.classList.add('open');
            // Dziura zamyka się natychmiast po wbiciu
            card.classList.add('fx-sink');
            await wait(200);
            return;
        }

        // 1. ZAMACH — kij wjeżdża i się cofa. Bez tej fazy oko nie zdąży
        //    zauważyć, że zaraz padnie uderzenie, i całość czyta się
        //    jak glitch zamiast jak zagranie.
        await animate(p.cue, [
            { transform: 'translateX(-30px) rotate(-3deg)', opacity: 0 },
            { transform: 'translateX(-52px) rotate(-3deg)', opacity: 1, offset: 0.45 },
            { transform: 'translateX(-96px) rotate(-3deg)', opacity: 1 }
        ], { duration: 260, easing: 'cubic-bezier(0.25, 0.9, 0.35, 1)' });

        // 2. UDERZENIE
        animate(p.cue, [
            { transform: 'translateX(-96px) rotate(-3deg)' },
            { transform: 'translateX(4px) rotate(-3deg)', offset: 0.42 },
            { transform: 'translateX(-38px) rotate(-3deg)', offset: 0.72 },
            { transform: 'translateX(-120px) rotate(-3deg)', opacity: 0 }
        ], { duration: 620, easing: 'cubic-bezier(0.3, 0, 0.2, 1)' });

        await wait(150);   // moment kontaktu

        // Końcówka ugina się o bilę — 130 ms, ale to ona sprzedaje siłę
        animate(p.tip, [
            { transform: 'scaleX(1)' },
            { transform: 'scaleX(0.55)', offset: 0.4 },
            { transform: 'scaleX(1)' }
        ], { duration: 130, easing: 'ease-out' });

        impactBurst(m.r.left, m.ballCy, m.size);
        card.classList.add('fx-shake');
        setTimeout(() => card.classList.remove('fx-shake'), 320);
        p.pocket.classList.add('open');
        back.classList.add('dim');
        p.glare.classList.add('on');
        p.streak.classList.add('on');

        // 3. TOCZENIE — przycisk jedzie i się obraca razem z numerem
        const roll = { duration: 700, easing: 'cubic-bezier(0.12, 0.62, 0.3, 1)' };

        animate(p.glare, [
            { transform: 'translate3d(0, 0, 0)' },
            { transform: 'translate3d(' + (m.travel * 0.55) + 'px, -3px, 0)', offset: 0.55 },
            { transform: 'translate3d(' + m.travel + 'px, 0, 0)' }
        ], roll);

        animate(p.streak, [
            { transform: 'translate3d(0, 0, 0)', opacity: 0.9 },
            { transform: 'translate3d(' + m.travel + 'px, 0, 0)', opacity: 0 }
        ], roll);

        await animate(ball, [
            { transform: 'translate3d(0, 0, 0) rotate(0deg)' },
            // lekkie podbicie w połowie drogi — stół nigdy nie jest idealny
            { transform: 'translate3d(' + (m.travel * 0.55) + 'px, -3px, 0) rotate(' + (m.spin * 0.55) + 'deg)', offset: 0.55 },
            { transform: 'translate3d(' + m.travel + 'px, 0, 0) rotate(' + m.spin + 'deg)' }
        ], roll);

        p.streak.classList.remove('on');

        // 4. GRZECHOT o krawędzie łuzy
        const rattle = { duration: 170, easing: 'ease-in-out' };
        const at = (x, y, rot) =>
            ({ transform: 'translate3d(' + x + 'px, ' + y + 'px, 0)' + (rot !== undefined ? ' rotate(' + rot + 'deg)' : '') });

        animate(p.glare, [
            at(m.travel, 0), at(m.travel + m.size * 0.12, 2, undefined),
            at(m.travel - m.size * 0.07, -1), at(m.travel, 0)
        ], rattle);

        await animate(ball, [
            at(m.travel, 0, m.spin),
            at(m.travel + m.size * 0.12, 2, m.spin + 14),
            at(m.travel - m.size * 0.07, -1, m.spin + 6),
            at(m.travel, 0, m.spin + 18)
        ], rattle);

        // 5. WPADNIĘCIE
        p.pocket.classList.add('gulp');
        const drop = { duration: 260, easing: 'cubic-bezier(0.5, 0, 0.9, 0.4)' };

        animate(p.glare, [
            { transform: 'translate3d(' + m.travel + 'px, 0, 0) scale(1)', opacity: 1 },
            { transform: 'translate3d(' + m.travel + 'px, ' + (m.size * 0.75) + 'px, 0) scale(0.42)', opacity: 0 }
        ], drop);

        await animate(ball, [
            {
                transform: 'translate3d(' + m.travel + 'px, 0, 0) rotate(' + (m.spin + 18) + 'deg) scale(1)',
                filter: 'brightness(1)'
            },
            {
                transform: 'translate3d(' + m.travel + 'px, ' + (m.size * 0.75) + 'px, 0) rotate(' + (m.spin + 200) + 'deg) scale(0.42)',
                filter: 'brightness(0.12)',
                opacity: 0
            }
        ], drop);
        p.pocket.classList.add('closing');
        setTimeout(() => p.pocket.remove(), 160);
        // 6. Karta zapada się w ślad za bilą
        card.classList.add('fx-sink');
        await wait(260);
    }

    // ============================================================
    //  POWRÓT PO NIEUDANEJ PRÓBIE
    //  Serwer renderuje stronę od nowa z klasą "failed", więc scenę
    //  odtwarzamy od końca: bila jest przy bandzie i wraca na miejsce.
    // ============================================================
    async function playReturn() {
        const m = measure();

        if (reduced) {
            ball.classList.remove('failed');
            return;
        }

        const glare = make('fx-glare');
        glare.style.left = m.r.left + 'px';
        glare.style.top = m.r.top + 'px';
        glare.style.width = m.size + 'px';
        glare.style.height = m.size + 'px';
        glare.classList.add('on');

        card.classList.add('fx-deny');
        setTimeout(() => card.classList.remove('fx-deny'), 440);

        const opts = { duration: 900, easing: 'cubic-bezier(0.2, 0.72, 0.3, 1)' };

        animate(glare, [
            { transform: 'translate3d(' + m.travel + 'px, 0, 0)' },
            { transform: 'translate3d(0, 0, 0)' }
        ], opts);

        // Start ściśnięta o bandę, powrót z odwrotną rotacją
        await animate(ball, [
            { transform: 'translate3d(' + m.travel + 'px, 0, 0) rotate(0deg) scaleX(0.78) scaleY(1.12)' },
            { transform: 'translate3d(' + (m.travel * 0.92) + 'px, 0, 0) rotate(' + (-m.spin * 0.1) + 'deg) scaleX(1.06) scaleY(0.94)', offset: 0.18 },
            { transform: 'translate3d(0, 0, 0) rotate(' + (-m.spin) + 'deg) scale(1)' }
        ], opts);

        ball.classList.remove('failed');
        cleanup();
    }

    // ============================================================
    //  PODPIĘCIE
    // ============================================================
    form.addEventListener('submit', function (e) {
        if (busy) { e.preventDefault(); return; }

        e.preventDefault();
        busy = true;

        // Efekt nigdy nie może zablokować logowania — stąd catch przed submit
        playShot().catch(() => {}).then(() => form.submit());
    });

    if (ball.classList.contains('failed')) {
        // Chwila oddechu, żeby użytkownik zdążył zobaczyć komunikat błędu
        setTimeout(() => { playReturn().catch(() => {}); }, 250);
    }

    window.addEventListener('pagehide', cleanup);
})();
