/*
 * Theme bootstrap for the talks pages (classic script, loaded in <head>).
 *
 * Sets `data-theme` on <html> before first paint from localStorage key
 * `theme` (shared with comphy-lab.org), falling back to the system colour
 * scheme. Wires every `[data-theme-toggle]` button once the DOM is ready.
 * No inline script is needed, so the page CSP stays `script-src 'self'`.
 */
(function () {
  'use strict';

  var KEY = 'theme';
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function saved() {
    try {
      var value = window.localStorage.getItem(KEY);
      return value === 'dark' || value === 'light' ? value : null;
    } catch (err) {
      return null;
    }
  }

  function current() {
    return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function label() {
    var next = current() === 'dark' ? 'light' : 'dark';
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].setAttribute('aria-label', 'Switch to ' + next + ' theme');
      buttons[i].setAttribute('title', 'Switch to ' + next + ' theme');
    }
  }

  function apply(theme, persist) {
    root.setAttribute('data-theme', theme);
    if (persist) {
      try {
        window.localStorage.setItem(KEY, theme);
      } catch (err) {
        /* Storage may be unavailable (private mode); the toggle still works. */
      }
    }
    label();
  }

  apply(saved() || (media && media.matches ? 'dark' : 'light'), false);

  if (media && media.addEventListener) {
    media.addEventListener('change', function (event) {
      if (!saved()) apply(event.matches ? 'dark' : 'light', false);
    });
  }

  function wire() {
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].addEventListener('click', function () {
        apply(current() === 'dark' ? 'light' : 'dark', true);
      });
    }
    label();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
