(function () {
    var STORAGE_KEY = 'jazzmin_dark_mode';

    function getSavedMode() {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            return null;
        }
    }

    function saveMode(mode) {
        try {
            localStorage.setItem(STORAGE_KEY, mode);
        } catch (e) {}
    }

    function systemPrefersDark() {
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    function applyMode(mode) {
        var darkLink = document.getElementById('jazzmin-dark-mode-theme');
        var body = document.body;
        if (!darkLink) {
            return;
        }
        if (mode === 'dark') {
            darkLink.disabled = false;
            darkLink.media = 'all';
            body.classList.add('dark-mode');
            body.classList.add('theme-dark');
        } else {
            darkLink.disabled = true;
            darkLink.media = 'all';
            body.classList.remove('dark-mode');
            body.classList.remove('theme-dark');
        }
    }

    function updateIcon(mode) {
        var icon = document.getElementById('jazzmin-dark-mode-toggle-icon');
        if (icon) {
            icon.className = mode === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
        }
    }

    function toggleMode() {
        var saved = getSavedMode();
        var current = saved || (systemPrefersDark() ? 'dark' : 'light');
        var next = current === 'dark' ? 'light' : 'dark';
        saveMode(next);
        applyMode(next);
        updateIcon(next);
    }

    function addToggleButton() {
        var nav = document.querySelector('ul.navbar-nav.ml-auto');
        if (!nav) {
            return;
        }
        var li = document.createElement('li');
        li.className = 'nav-item';
        var a = document.createElement('a');
        a.className = 'nav-link';
        a.href = '#';
        a.id = 'jazzmin-dark-mode-toggle';
        a.title = 'Toggle dark mode';
        a.innerHTML = '<i id="jazzmin-dark-mode-toggle-icon" class="fas fa-moon"></i>';
        a.addEventListener('click', function (e) {
            e.preventDefault();
            toggleMode();
        });
        li.appendChild(a);
        nav.prepend(li);
    }

    function init() {
        addToggleButton();
        var mode = getSavedMode() || (systemPrefersDark() ? 'dark' : 'light');
        applyMode(mode);
        updateIcon(mode);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
