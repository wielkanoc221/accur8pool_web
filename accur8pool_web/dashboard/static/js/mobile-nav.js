// Nawigacja mobilna: dolny pasek + szuflada menu + panel segmentów.
// Elementy budowane są z JS, żeby nie duplikować HTML-a na każdej podstronie.
// Na desktopie ten kod nic nie zmienia — dolny pasek jest ukryty przez CSS.

document.addEventListener('DOMContentLoaded', function () {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    const segmentsPanel = document.getElementById('segments-panel');
    const filesPanelBtn = document.getElementById('open-files-panel');
    const navTrack = document.getElementById('nav-menu-track');

    // --- Tło przyciemniające (wspólne dla menu i panelu segmentów) ---
    const backdrop = document.createElement('div');
    backdrop.className = 'sidebar-backdrop';
    document.body.appendChild(backdrop);

    // --- Dolny pasek nawigacji ---
    const nav = document.createElement('nav');
    nav.className = 'mobile-nav';
    nav.setAttribute('aria-label', 'Nawigacja główna');
    document.body.appendChild(nav);

    function addNavButton(icon, label, onClick) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mobile-nav-btn';
        btn.innerHTML = `<span class="mnav-icon" aria-hidden="true">${icon}</span><span>${label}</span>`;
        btn.addEventListener('click', onClick);
        nav.appendChild(btn);
        return btn;
    }

    // --- Otwieranie / zamykanie ---
    function closeAll() {
        sidebar.classList.remove('open');
        if (segmentsPanel) segmentsPanel.classList.remove('open');
        backdrop.classList.remove('visible');
        syncActiveState();
    }

    function openSidebar(showFiles) {
        closeAll();
        sidebar.classList.add('open');
        backdrop.classList.add('visible');

        // "Pliki" od razu przełącza na drugi panel sidebaru
        if (showFiles && filesPanelBtn) {
            filesPanelBtn.click();
        } else if (!showFiles && navTrack) {
            navTrack.classList.remove('show-files');
        }
        syncActiveState();
    }

    function openSegments() {
        if (!segmentsPanel) return;
        closeAll();
        segmentsPanel.classList.add('open');
        backdrop.classList.add('visible');
        syncActiveState();
    }

    // --- Przyciski paska ---
    const btnMenu = addNavButton('☰', 'Menu', function () {
        sidebar.classList.contains('open') ? closeAll() : openSidebar(false);
    });

    const btnFiles = addNavButton('📁', 'Pliki', function () {
        openSidebar(true);
    });

    // Segmenty i reset zoomu mają sens tylko na dashboardzie
    let btnSegments = null;
    if (segmentsPanel) {
        btnSegments = addNavButton('✂️', 'Segmenty', function () {
            segmentsPanel.classList.contains('open') ? closeAll() : openSegments();
        });

        // Akcje z topbaru (ukrytego na mobile) muszą być gdzieś dostępne
        addNavButton('🔍', 'Reset', function () {
            if (typeof window.resetZoom === 'function') window.resetZoom();
        });
    } else if (typeof window.loadDatasets === 'function') {
        addNavButton('🔄', 'Odśwież', function () {
            window.loadDatasets();
        });
    }

    function syncActiveState() {
        const filesOpen = sidebar.classList.contains('open') &&
                          navTrack && navTrack.classList.contains('show-files');
        const menuOpen = sidebar.classList.contains('open') && !filesOpen;
        const segOpen = segmentsPanel && segmentsPanel.classList.contains('open');

        btnMenu.classList.toggle('active', menuOpen);
        btnFiles.classList.toggle('active', filesOpen);
        if (btnSegments) btnSegments.classList.toggle('active', segOpen);
    }

    // --- Zamykanie: tło, X w panelu, Escape ---
    backdrop.addEventListener('click', closeAll);

    const segmentsClose = document.getElementById('segments-close');
    if (segmentsClose) segmentsClose.addEventListener('click', closeAll);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeAll();
    });

    // Wybór pliku zamyka menu (strona i tak się przeładuje, ale bez tego
    // menu zostałoby otwarte na moment przed przeładowaniem)
    sidebar.addEventListener('click', function (e) {
        if (e.target.closest('a')) closeAll();
    });

    // Powrót do szerokiego ekranu — wyczyść stan warstw mobilnych
    window.addEventListener('resize', function () {
        if (window.innerWidth > 768) closeAll();
    });
});
