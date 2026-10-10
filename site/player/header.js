/*
 * Site header and footer behaviour for the talks pages (classic script, defer).
 *
 * The markup is static HTML ported from comphy-lab.org (see
 * ../vendor/comphy-lab/SOURCE.md); this script only adds behaviour:
 *
 * - Below the CSS breakpoint the navigation is a drawer opened by the
 *   "Menu" button (aria-expanded, aria-controls). Opening moves focus into
 *   the drawer and keeps Tab inside it; Escape, the Close button, a click
 *   outside or following a link close it, and focus returns to the button
 *   when it was inside the drawer. The drawer also closes when the header
 *   becomes inert (the talk player's full screen) or the layout widens.
 * - Footer "Back to top" scrolls to the top without touching the URL hash,
 *   which the talk player owns for deep links.
 * - `[data-year]` shows the current year.
 *
 * The theme toggle is wired by theme.js. Keys other than Escape and Tab
 * (while the drawer is open) are never handled or cancelled here, so the
 * talk player's shortcuts keep working with focus anywhere in the header.
 * The player itself ignores keys while the drawer is open (it reads the
 * Menu button's aria-expanded), so the drawer keeps them.
 */
(function () {
  'use strict';

  var header = document.querySelector('.s-header');
  var toggle = header && header.querySelector('.s-header__menu-toggle');
  var nav = header && header.querySelector('.s-header__nav');
  var closeBtn = nav && nav.querySelector('.s-header__nav-close-btn');
  var reducedMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

  function drawerMode() {
    return Boolean(toggle) && window.getComputedStyle(toggle).display !== 'none';
  }

  function isOpen() {
    return Boolean(nav) && nav.classList.contains('is-active');
  }

  function focusables() {
    var nodes = nav.querySelectorAll('a[href], button:not([disabled])');
    var out = [];
    for (var i = 0; i < nodes.length; i += 1) {
      if (nodes[i].getClientRects().length) out.push(nodes[i]);
    }
    return out;
  }

  function open() {
    if (!nav || isOpen()) return;
    nav.classList.add('is-active');
    toggle.setAttribute('aria-expanded', 'true');
    var first = focusables()[0];
    if (!first) return;
    // The drawer was `visibility: hidden` until now. Chromium refuses focus
    // to its contents until the next frame has rendered, so try again then.
    var tries = 0;
    (function attempt() {
      if (!isOpen() || nav.contains(document.activeElement)) return;
      first.focus({ preventScroll: true });
      tries += 1;
      if (document.activeElement !== first && tries < 4) window.requestAnimationFrame(attempt);
    })();
  }

  /* `restore`: give focus back to the Menu button if it was in the drawer, or
     nowhere (Safari does not focus a tapped button). */
  function close(restore) {
    if (!nav || !isOpen()) return;
    var active = document.activeElement;
    var hadFocus = !active || active === document.body || nav.contains(active);
    nav.classList.remove('is-active');
    toggle.setAttribute('aria-expanded', 'false');
    if (restore && hadFocus && drawerMode()) toggle.focus({ preventScroll: true });
  }

  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      if (isOpen()) close(true);
      else open();
    });

    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        close(true);
      });
    }

    nav.addEventListener('click', function (event) {
      var link = event.target instanceof Element ? event.target.closest('a[href]') : null;
      if (link) close(false);
    });

    // A click outside the open drawer only closes it: on a talk page it must
    // not also reach the stage, where a click changes slide.
    window.addEventListener(
      'click',
      function (event) {
        if (!isOpen()) return;
        var target = event.target;
        if (target instanceof Node && (nav.contains(target) || toggle.contains(target))) return;
        event.preventDefault();
        event.stopPropagation();
        close(false);
      },
      true
    );

    // Capture phase on window: runs before the talk player's document
    // listener, which ignores a key whose default was prevented.
    window.addEventListener(
      'keydown',
      function (event) {
        if (!isOpen()) return;
        if (event.key === 'Escape' || event.key === 'Esc') {
          event.preventDefault();
          close(true);
          return;
        }
        if (event.key !== 'Tab' || !drawerMode()) return;
        // Keep Tab inside the open drawer. Moving focus here (rather than
        // only wrapping at the ends) also works where the browser's own Tab
        // skips links and buttons (Safari's default).
        var items = focusables();
        if (!items.length) return;
        var index = items.indexOf(document.activeElement);
        var next = event.shiftKey
          ? (index <= 0 ? items.length - 1 : index - 1)
          : (index < 0 || index === items.length - 1 ? 0 : index + 1);
        event.preventDefault();
        items[next].focus();
      },
      true
    );

    // The talk player makes the header inert while the stage fills the screen.
    if (window.MutationObserver) {
      new MutationObserver(function () {
        if (header.inert || header.hasAttribute('inert')) close(false);
      }).observe(header, { attributes: true, attributeFilter: ['inert'] });
    }

    window.addEventListener('resize', function () {
      if (isOpen() && !drawerMode()) close(false);
    });
  }

  var tops = document.querySelectorAll('[data-back-to-top]');
  for (var i = 0; i < tops.length; i += 1) {
    tops[i].addEventListener('click', function (event) {
      event.preventDefault();
      var smooth = !(reducedMotion && reducedMotion.matches);
      window.scrollTo({ top: 0, left: 0, behavior: smooth ? 'smooth' : 'auto' });
      var home = header && header.querySelector('.s-header__logo a');
      if (home) home.focus({ preventScroll: true });
    });
  }

  var year = String(new Date().getFullYear());
  var years = document.querySelectorAll('[data-year]');
  for (var j = 0; j < years.length; j += 1) {
    if (/^\d{4}$/.test(year) && year > years[j].textContent) years[j].textContent = year;
  }
})();
