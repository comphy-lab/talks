# Talks player

The static player behind `comphy-lab.org/talks/<slug>/`. It implements
`docs/SPEC.md` Section 3.2 and builds its DOM with
`createElement`/`textContent` only.

| File | Role |
|---|---|
| `talk.template.html` | Page template. The build replaces `{{TITLE}}`, `{{DESCRIPTION}}` and `{{SLUG}}` (HTML-escaped) and writes `site/<slug>/index.html`. Holds the per-page CSP. |
| `player.js` | ES module. Reads `./manifest.json` and plays the talk. |
| `player.css` | Player and page layout (talk page and talks index), on top of `../vendor/ds/tokens.css` and the vendored comphy-lab.org header and footer styles. |
| `theme.js` | Classic script in `<head>`: sets `data-theme` from `localStorage` key `theme` before first paint and wires `[data-theme-toggle]`, the one theme toggle, in the site header. |
| `header.js` | Classic deferred script for both pages: the site header's Menu drawer (below 1120 px), the footer's "Back to top" and current year. The header and footer markup are static, ported from comphy-lab.org (`../vendor/comphy-lab/SOURCE.md`). |
| `dev/manifest.json` | Mock talk for development. Its file names are placeholders; no media is committed. |

## Behaviour worth knowing

- **Placement key.** `kind:ref:x,y,w,h`, with the rectangle rounded to 1 px.
  An element whose key survives a state change is never re-created,
  re-attached or seeked, so a video spanning slides keeps playing. Elements
  are placed at that rounded rectangle, so placement depends on the key alone.
- **Deep links.** `#<first 8 hex chars of the slide UUID>` opens the first
  state of a slide; `#<…>.<k>` opens state `k`, counted from 1. The player
  rewrites the hash in this canonical form with `history.replaceState`, at
  most once per 250 ms: WebKit throws a `SecurityError` after 100 calls in
  10 s, which holding an arrow key reaches in a few seconds.
- **Steps.** Previous from the first state of a slide goes to the last state
  of the slide before, as in Keynote. A `dissolve` transition runs only when
  stepping forwards, and never under `prefers-reduced-motion`.
- **Stills.** The player keeps the current state on screen until the next
  still has decoded (up to 4 s), so a step never flashes an empty stage.
- **Failures.** A video that fails to load is hidden, which shows the poster
  frame baked into the still beneath it. If the YouTube IFrame API fails or
  takes longer than 8 s, or the player is not ready 10 s after it was
  created (the embed host blocked), the embed area becomes a "Watch on
  YouTube" link with the credit.
- **YouTube focus.** A click inside the embed moves keyboard focus into
  YouTube's cross-origin frame; the player hands it straight back to the
  stage frame (focusable with `tabindex="-1"`), so keys and presenter
  clickers keep working. Touches inside the embed cannot navigate.
- **Full screen.** While the stage fills the screen (element full screen or
  the phone fallback), the header, controls, help and footer are `inert`;
  focus returns to where it was on leaving. The phone fallback also hides
  the fixed site header, and an open Menu drawer closes.
- **Site header.** It handles no player key: only Escape and Tab while its
  Menu drawer is open (`header.js` listens in the capture phase and cancels
  Escape). While the drawer is open the player ignores every key, so the
  arrows scroll the drawer rather than change the slide hidden behind it; it
  only keeps cancelling Backspace (WebKit's History Back).
- **Downloads.** The download button appears only when the manifest lists at
  least one valid `downloads` entry. Its panel holds plain links to
  `media_base + file`; Escape, a second press, a click outside (on the stage
  it does not also change slide), following a link and entering full screen
  close it.
- **Announcements.** The live region names a new slide ("Slide n of N, step 1
  of K: title") and then each later step of it ("Step k of K").

## Test hook

`window.__talk` is a frozen, read-only object:

- `state()` returns `{ slide, stateIndex, keys, paused }` for the state on
  screen, or `null` before the first one. `slide` is 1-based, `stateIndex`
  is 0-based within the slide, `keys` lists the mounted placement keys in
  z-order.
- `media()` returns `[{ key, el }]` for the mounted media, in z-order. `el` is
  the `<video>`, or the wrapper `<div>` of a YouTube embed.

The CSP forbids `eval`, so Playwright's `wait_for_function` cannot poll in
these pages; poll `page.evaluate("() => window.__talk.state()")` instead.

## Local preview

```bash
cd tests && uv run python serve.py --slug <slug>
# then open http://127.0.0.1:8000/talks/<slug>/?media=/media/<slug>/
```

`tests/serve.py` serves `site/` at `/talks/` and `build/<slug>/media/` at
`/media/<slug>/`, as in production, and answers byte-range requests (Safari
needs them to play MP4). On `localhost` or `127.0.0.1` only,
`?media=<same-origin relative path>` replaces `media_base`; the CSP allows
`media-src 'self'` so that videos play through this override.
