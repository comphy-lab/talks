# comphy-lab.org site shell (vendored)

The talks pages share comphy-lab.org's header and footer. These files are
ported from the main-site repository
[comphy-lab/comphy-lab.github.io](https://github.com/comphy-lab/comphy-lab.github.io)
at commit `94df3fdf090b5b584ce65d7f6d5cdc2f811c2f6c` (the commit that added Talks to the
header and moved its Menu breakpoint to 1119 px; Talks joined the footer in
`d79dbace73c97897a904ea130142c698d679856d`).

| File here | Ported from | How |
|---|---|---|
| `header.css` | `assets/css/styles.css` section 6 "Header & navigation" and its Menu drawer rules (`max-width: 1119px`; `768px` before comphy-lab.org added its Talks link); the legacy colour variables they read (`styles.css` `:root` and `[data-theme="dark"]`); the dark surface overrides in `assets/css/bridge.css` | Trimmed to the rules the header uses, variables resolved to values |
| `footer-v2.css` | `assets/css/footer-v2.css`; the `.site-footer` layout and 768 px centring in `styles.css`; `.invert-on-light` in `bridge.css` | Nearly verbatim |
| Header markup in `../../index.html` and `../../player/talk.template.html` | `_layouts/default.html` (`<header class="s-header">`) | Same items in the same order, with Talks after Project docs |
| Footer markup in the same two pages | `_includes/footer-v2.html` | Same columns and links |
| `logos/CoMPhy-Lab-no-name.png` | `assets/logos/CoMPhy-Lab-no-name.png` (1557 x 1035) | Downscaled to 295 x 196 (three times the 65 px header height) |
| `logos/LogoPof_transparent_white.png`, `logos/logoBasilisk_TransparentBackground.png` | `assets/logos/` | Downscaled to 216 px high (three times the 72 px partner strip) |
| `logos/Durham-University.png`, `logos/Durham-University-white.png` | `assets/logos/` | Copied |

Icons are inline SVG in the page markup, not icon fonts (the talks CSP allows
fonts and styles from this origin only):

- GitHub and Bluesky: Font Awesome Free 6.7.2 `svgs/brands/github.svg` and
  `bluesky.svg` (icons CC BY 4.0, Fonticons, Inc.), the version comphy-lab.org
  loads.
- Google Scholar: the `google-scholar` glyph of Academicons 1.7.0
  (`assets/css/academicons-1.7.0/fonts/academicons.svg` in the main-site
  repository; font licensed under the SIL OFL 1.1), drawn in its 2048-unit em
  box and flipped from font to SVG coordinates.
- Theme toggle moon and sun: the inline SVGs of `_layouts/default.html`.

## Deliberate differences

- The theme toggle, the Menu button and the Close button are `<button>`s (the
  main site uses a `<div>` and `href="#0"` links). The Menu button carries
  `aria-expanded` and `aria-controls`; `../../player/header.js` keeps Tab in
  the open drawer, closes it on Escape, Close, a click outside or a link, and
  returns focus to the Menu button. While it is open the talk player ignores
  every key, so the arrows scroll the drawer, not the slides behind it.
- The ⌘K command palette is left out. It needs the main site's search
  database and Fuse.js, and a palette taking keys on top of the talk player
  would compete with the player's shortcuts.
- The bar becomes the Menu drawer below 1120 px, the same breakpoint as
  comphy-lab.org. The talks bar has no command-palette button and needs about
  1003 px (1020 px with a 17 px classic scrollbar); comphy-lab.org's needs
  about 1099 px. Neither lets a label wrap. (Before its Talks link,
  comphy-lab.org switched at 768 px and wrapped or clipped its bar between
  769 and about 1020 px; the link alone would have taken that to 1096 px.)
- Talks is shown in the hover style with an underline and marked
  `aria-current`: `"page"` on the talks index, which is the page it links
  to, and `"true"` on a talk page, which lies inside that section.
- The drawer scrolls when the screen is too short for it, and is hidden once
  closed, so it leaves the tab order (comphy-lab.org's drawer gained both
  with its Talks link).
- On a phone held sideways (`orientation: landscape` and at most 520 px high)
  the talk page uses a 60 px header to leave room for the slides
  (`../../player/player.css`).
- "Edit on GitHub" points at this repository, the source of these pages.
- "Back to top" scrolls without changing the URL hash, which the talk player
  uses for deep links. The copyright year is set from the visitor's clock.

To update: compare the files above at a newer main-site commit, port the
changes, and change the commit here.
