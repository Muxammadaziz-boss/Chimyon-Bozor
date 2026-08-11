(function () {
    var STORAGE_KEY = 'jazzmin-theme-mode';

    function systemPrefersDark() {
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    function applyMode(mode) {
        if (mode === 'auto') {
            mode = systemPrefersDark() ? 'dark' : 'light';
        }
        document.documentElement.setAttribute('data-bs-theme', mode);
        try {
            localStorage.setItem(STORAGE_KEY, mode);
        } catch (e) {}
        updateIcon(mode);
    }

    function updateIcon(mode) {
        var icon = document.getElementById('jazzmin-dark-mode-toggle-icon');
        if (icon) {
            icon.className = mode === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
        }
    }

    function toggleMode() {
        var mode = localStorage.getItem(STORAGE_KEY) || document.documentElement.getAttribute('data-bs-theme') || 'dark';
        applyMode(mode === 'dark' ? 'light' : 'dark');
    }

    function addToggleButton() {
        // Try multiple selectors for navbar
        var nav = document.querySelector('ul.navbar-nav.ms-auto') ||
                   document.querySelector('ul.navbar-nav') ||
                   document.querySelector('.navbar-right') ||
                   document.querySelector('.navbar .nav');
        
        if (!nav || document.getElementById('jazzmin-dark-mode-toggle')) {
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
        nav.appendChild(li);
    }

    function init() {
        addToggleButton();
        var mode = localStorage.getItem(STORAGE_KEY) || 'dark';
        if (mode === 'auto') {
            mode = systemPrefersDark() ? 'dark' : 'light';
        }
        updateIcon(mode);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
