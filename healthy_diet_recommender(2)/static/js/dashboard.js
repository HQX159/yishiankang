/**
 * Lightweight Flask adaptation of React Bits Spotlight Card / Fade Content.
 * Sources and license: ../../THIRD_PARTY_NOTICES.md.
 * No counters animate: health values always show their actual saved value.
 */
(function () {
    'use strict';
    var root = document.querySelector('.dashboard-redesign');
    if (!root) return;
    var current = document.querySelector('.nav-menu a[href="' + window.location.pathname + '"]');
    if (current) current.setAttribute('aria-current', 'page');
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    var fine = window.matchMedia('(hover: hover) and (pointer: fine)');
    root.querySelectorAll('.spotlight-card').forEach(function (card) {
        var pending = 0;
        var x = 0;
        var y = 0;
        card.addEventListener('pointermove', function (event) {
            if (reduce.matches || !fine.matches) return;
            var bounds = card.getBoundingClientRect();
            x = event.clientX - bounds.left;
            y = event.clientY - bounds.top;
            if (pending) return;
            pending = requestAnimationFrame(function () {
                card.style.setProperty('--spot-x', x + 'px');
                card.style.setProperty('--spot-y', y + 'px');
                pending = 0;
            });
        });
    });
    if (reduce.matches || !('IntersectionObserver' in window)) return;
    var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('dashboard-enter');
            observer.unobserve(entry.target);
        });
    }, { threshold: 0.05 });
    root.querySelectorAll('.dashboard-welcome, .dashboard-tools, .stats-grid, .info-row, .section').forEach(function (section) {
        observer.observe(section);
    });
}());
