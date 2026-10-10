# Talks site specification (v1)

This is the contract between the three parts of the repository:

- `tools/`: the build pipeline. It turns a Keynote HTML export plus a
  human-authored `talks-src/<slug>/talk.yaml` into media files and a
  manifest.
- `site/`: the static player, deployed by GitHub Pages at
  `https://comphy-lab.org/talks/`.
- `tests/`: unit tests for the pipeline and browser tests for the player.

Media (slide images and videos) is hosted on Cloudflare R2 at
`https://media.comphy-lab.org/talks/<slug>/`. Files are named by content hash,
so a URL never changes meaning and can be cached forever. The repository holds
code, manifests and decisions, never media.

## 1. Inputs

### 1.1 Keynote HTML export

The Keynote HTML export (File > Export To > HTML) is the only build input
for slide content. The export is not committed: it is large, and most of it
is duplicated. Its structure:

- `assets/header.json`: `slideList` (slide UUIDs in show order),
  `slideWidth`, `slideHeight` (1920 x 1080 for our decks).
- `assets/<UUID>/<UUID>.json`: per slide. `events[]` are the build steps.
  - Each event has a `baseLayer`: the static state *before* the event fires,
    as a tree of layers with `initialState` (position, bounds, anchor point,
    opacity, hidden) and `texture` references.
  - Each event has `effects[]`: `buildIn` / `buildOut` / `transition`, with
    nested effects. Movies appear as `apple:movie-start` buildIn effects
    wrapping a `renderMovie` effect, which carries
    `movie {asset, startTime, endTime, loopMode, ...}`.
  - Each event has `automaticPlay`: the event fires without a click.
  - The last event of every slide is its outgoing transition. Its `baseLayer`
    is one flattened texture of the slide's final state.
  - `assets` maps ids to textures: `{type: texture, index: <page>, url.native:
    assets/<UUID>.pdf}`, i.e. page N of a per-slide PDF. Movie ids map to
    `{type: video, url.native: <file>, width, height}`.
- `assets/<UUID>.pdf`: the per-slide texture atlas, vector, one page per
  texture. The `.pdfp` and `.jsonp` copies exist only for `file://` loading;
  ignore them.

### 1.2 `talks-src/<slug>/talk.yaml`

This file holds the human decisions the export does not record. See the file
for the authoritative example. Its fields:

- `slug`, `title`, `subtitle`, `speaker`, `venue_note`, `updated`
  (ISO date), `media_base`.
- `video.loop: true`: every video loops.
- `video.spans: auto`: the same source at the same rectangle on consecutive
  click states, and across consecutive slides, is one continuous placement.
- `video.max_scale: 2.0`: videos are downscaled to at most twice their
  on-slide box.
- `youtube[]`: movies replaced by a YouTube embed. Each entry gives the slide
  UUID, the source movie name prefix, the YouTube id, start and end in seconds,
  the embed rectangle, and the attribution text. `before_start` says what a
  click state shows when Keynote shows the movie's poster before the click
  that starts it: `poster` (default) draws that poster frame, fitted into the
  embed rectangle, as Keynote does; `embed` mounts the embed already, so it
  plays from that state on.
- `patches[]`: corrections drawn onto slide images. Each gives the slide UUID,
  the rectangle to cover, the replacement text and style. They are used until
  the `.key` file itself is fixed.
- `titles`: optional map from slide UUID to a human-written title, for a
  slide whose title derived from its text (Section 3) is wrong.
- `changelog[]`: `{date, note}`.

## 2. Pipeline outputs

`python -m talks_build build --export <dir> --talk talks-src/<slug>/talk.yaml`
writes:

- `build/<slug>/media/<sha256-16>.<ext>`: content-addressed files (`webp`
  stills and overlays, `mp4` videos, `webp` posters). `build/` is gitignored.
- `site/<slug>/manifest.json`: the manifest (Section 3), which is committed.
- `site/<slug>/index.html`: the talk page, generated from
  `site/player/talk.template.html`.

`python -m talks_build upload --slug <slug>` uploads to R2 every file in
`build/<slug>/media/` not already present, under
`talks/<slug>/<file>`, using the `cf` CLI with the correct
`Content-Type`. Because files are content-addressed, an existing key is never
overwritten. The slug must be a valid talk slug and equal the manifest's
`slug`. The existing keys are listed page by page with `--start-after` (the
`cf` CLI does not pass on the API's cursor) until a page brings no new key;
if the listing fails or does not finish, nothing is uploaded.

`python -m talks_build validate site/<slug>/manifest.json` checks the
manifest against `tools/schema/manifest.schema.json` and the invariants in
Section 3.3.

## 3. Manifest (`schema: 1`)

```jsonc
{
  "schema": 1,
  "slug": "singular-bursting-bubbles",
  "title": "…", "subtitle": "…", "speaker": "…", "updated": "2026-10-10",
  "canvas": { "width": 1920, "height": 1080 },
  "media_base": "https://media.comphy-lab.org/talks/singular-bursting-bubbles/",
  "videos": {
    "v01": { "src": "<hash>.mp4", "poster": "<hash>.webp",
             "width": 1024, "height": 880, "duration": 8.27,
             "credit": null }                         // or attribution text
  },
  "youtube": {
    "y01": { "id": "a9hUsVq9q7U", "start": 7.191, "end": 63.397,
             "title": "…", "credit": "…" }
  },
  "slides": [
    {
      "id": "<Keynote slide UUID>",                  // stable deep-link key
      "n": 1,                                         // 1-based show order
      "title": "…",                                   // first text line, for UI and screen readers
      "text": "…",                                    // all text shown on the slide, for screen readers and search
      "states": [                                     // one per click (rest state)
        {
          "image":   "<hash>.webp",                   // full-canvas still, videos excluded
          "overlay": "<hash>.webp",                   // or null: transparent full-canvas layer drawn ABOVE media
          "transition": "none",                       // "none" | "dissolve"
          "media": [                                  // everything visible in this state
            { "kind": "video",   "ref": "v01", "rect": [x, y, w, h] },
            { "kind": "youtube", "ref": "y01", "rect": [x, y, w, h] }
          ]
        }
      ]
    }
  ],
  "changelog": [ { "date": "2026-10-10", "note": "…" } ]
}
```

All rectangles are in canvas pixels (origin top-left). They may extend past the
canvas; the stage clips them. Media rectangles are whole pixels: the pipeline
rounds each *edge* of the Keynote rectangle (`w = round(x + w) - round(x)`),
not the size, so a video lines up with the poster frame baked into the still.

### 3.1 Still images

- `image` is the slide as it looks in that state with every movie area showing
  its poster frame. In other words, it is the Keynote render for that state;
  the video element covers the poster.
- `overlay` exists when Keynote layers sit *above* a movie (labels, masks,
  arrows). It contains only those layers on a transparent background, so the
  player can draw them over the playing video. When nothing overlaps a movie,
  `overlay` is null. Where those layers reach a movie's edge, the overlay
  continues for 16 canvas pixels past the edge with the state's own `image`
  (opaque), never over another media rectangle: a browser that scales the
  stage blends the overlay's edge with what lies beyond it, and without the
  band the movie's edge pixels would show through as a hairline.
- For a `youtube` placement, `image` must not contain the movie poster under
  the embed rectangle. YouTube forbids overlays on its player, so the
  slide's title and credit must lie outside `rect`. A state of that slide
  without the placement (before the click that starts the movie, with
  `before_start: poster`) shows the poster fitted into the same rectangle.
- `title` and `text` come from the flat PDF export, as a viewer reads the
  slide: TeX glyphs are decoded from the fonts' own glyph names where the
  export's `ToUnicode` maps contradict them, and text painted at zero
  opacity (Beamer overlays kept for later slides) is left out. `text` covers
  every click state of the slide.

### 3.2 Player semantics

- **Stage.** The player shows a 16:9 stage, scaled uniformly to fit the
  viewport. The z-order is `image`, then media, then `overlay`, then UI. Media
  outside the canvas is clipped.
- **Placement key.** Each media item has the key `kind + ref + rect rounded to
  1 px`.
- **Changing state** (any navigation, including jumps):
  1. Compute the target state's set of keys.
  2. Keep, untouched, every mounted element whose key is in the target set.
     It is never re-created, re-attached or seeked, so it keeps playing.
  3. Remove any element whose key is not in the set (pause, then detach).
  4. Create elements for new keys and start them from the beginning (YouTube:
     from `start`).
  5. Swap `image` and `overlay`.

  This one rule implements builds within a slide and videos playing across
  slides.
- **Video elements.** `muted`, `playsinline`, `loop`, `preload="auto"`,
  `disablepictureinpicture`, no `controls`. The `poster` is set to the video's
  poster. The `src` is `media_base + src`.
- **YouTube.** Use the IFrame Player API, loaded only when a state first needs
  it, with host `https://www.youtube-nocookie.com`, `mute: 1`, `controls: 0`,
  `playsinline: 1` and `rel: 0`. Start at `start` and stop at `end`. When it
  reaches the end (`ENDED`), seek back to `start` and play: this is the loop.
  If the API cannot load (blocked network), or the player has not reported
  ready 10 s after it was created (the embed host blocked), show the poster
  region with a "Watch on YouTube" link and the credit. A click inside the
  embed moves keyboard focus into YouTube's frame; the player returns it to
  the stage frame at once, so keys and presenter clickers keep working.
- **Pause control.** A visible button pauses and resumes all media. This
  satisfies WCAG 2.2.2. Under `prefers-reduced-motion: reduce`, media starts
  paused and the button resumes it.
- **Navigation.**
  - Next: Right, Down, Space, PageDown, Enter, a click on the stage's right
    half, a swipe left.
  - Previous: Left, Up, PageUp, Backspace, a click on the left half, a swipe
    right.
  - Home and End go to the first and last state.
  - `F` toggles fullscreen. `P` and `K` toggle pause.
  - Presenter clickers send PageUp and PageDown.
  - Clicks and swipes inside a YouTube embed go to YouTube's cross-origin
    frame and cannot navigate; the page's help says to swipe outside the
    video or use the arrow buttons there.
- **Deep links.** `#<first 8 hex chars of slide UUID>` or `#<…>.<k>`, where
  `k` counts the slide's states from 1 (`#<…>.1` is the same as `#<…>`). The
  hash follows the state on screen, written without a suffix for a slide's
  first state. `history.replaceState` avoids flooding history; writes are
  coalesced to at most one per 250 ms, because WebKit throws after 100
  `replaceState` calls in 10 s (holding an arrow key reaches that).
- **Preloading.** Stills for the next two states and the previous one. Video
  elements are created only when needed.
- **Accessibility.**
  - The stage has `role="img"` with `aria-label` set to the slide title.
  - A visually hidden live region announces "Slide n of N: title" on
    reaching a slide (", step k of K" before the colon when the slide has
    K > 1 click states) and "Step k of K" on the later steps of the same
    slide.
  - The slide `text` is in a visually hidden element for screen readers.
  - All controls are keyboard reachable with a visible teal focus ring. On
    the black full-screen backdrop a light inner ring keeps the indicator
    above 3:1 in both themes.
  - While the stage fills the screen (element full screen, or the phone
    fallback that fills the viewport), the page around it is `inert`, so
    focus and screen readers reach only what is visible; focus returns to
    where it was on leaving.
- **Theme.** The design system's `data-theme` on `<html>`, with
  `localStorage` key `theme` (shared with comphy-lab.org).
- **Security.**
  - A per-page `<meta http-equiv="Content-Security-Policy">` sets:
    - `default-src 'self'`
    - `img-src 'self' https://media.comphy-lab.org https://i.ytimg.com data:`
    - `media-src 'self' https://media.comphy-lab.org` (`'self'` lets the
      local test hook below play videos; the site origin itself serves no
      media)
    - `script-src 'self' https://www.youtube.com/iframe_api https://www.youtube.com/s/player/
      https://static.cloudflareinsights.com/beacon.min.js https://static.cloudflareinsights.com/beacon.min.js/`
      (the Cloudflare Web Analytics beacon the zone injects, as allowed on comphy-lab.org)
      (the IFrame API and the widget script it loads, nothing else on that host)
    - `frame-src https://www.youtube-nocookie.com`
    - `style-src 'self'`
    - `font-src 'self'`
    - `connect-src 'self' https://cloudflareinsights.com`
    - `base-uri 'none'`
    - `form-action 'none'`
    - `object-src 'none'`
  - No inline scripts or styles. DOM construction uses
    `createElement`/`textContent` only, never `innerHTML`.
- **Test hook.** When the page is served from `localhost` or `127.0.0.1`,
  `?media=<path>` may override `media_base` with a same-origin relative path.
  This override is ignored on any other host. The browser tests serve
  `site/` at `/talks/` and `build/<slug>/media/` at `/media/<slug>/`, as in
  production, and open `/talks/<slug>/?media=/media/<slug>/`.
  `window.__talk` is a frozen, read-only object for tests: `state()` returns
  `{slide, stateIndex, keys, paused}` (slide 1-based, stateIndex 0-based)
  and `media()` returns the mounted media as `[{key, el}]`.

### 3.3 Manifest invariants (checked by `validate`)

- `canvas` is 1920 x 1080 for v1.
- Every `ref` resolves to a `videos` or `youtube` entry.
- Every slide has at least one state, and slide `n` values are `1..N` in order.
- Every media file name matches `^[0-9a-f]{16}\.(webp|mp4)$`.
- `media_base` is `https://media.comphy-lab.org/talks/<slug>/`.
- No two placements in one state share a key.

## 4. Video encoding

- **H.264** `libx264`, High profile, `yuv420p`, `-crf 23`, `-preset slow`,
  `-movflags +faststart`, no audio track. Width and height are even.
- **Scale.** At most `max_scale` times the largest on-slide rectangle the
  source occupies, and never upscaled.
- **Trim.** Keynote already pre-trims source files; the trim is encoded in the
  file name as `<name>-<start>-<end>.<ext>`. Encode the trimmed file as it is.
- **Poster.** The first frame as `webp`.
- **Deduplication.** The same source used on several slides is encoded once.
  Deduplicate by decoded stream identity (Keynote re-muxes copies with
  different container timestamps), not by file hash.
