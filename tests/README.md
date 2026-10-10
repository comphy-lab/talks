# Browser tests

Playwright tests of the talks player against a real build, in Chromium and
WebKit. They serve the site the way production does and check what a viewer
sees: every click state, the videos, the overlays, the YouTube embed, the
controls and the phone layout. `docs/SPEC.md` is the contract they test.

## Setup

```bash
cd tests
uv sync
uv run playwright install chromium webkit
```

The tests need a build of the talk: `site/<slug>/manifest.json` and
`build/<slug>/media/`. Without one they stop with exit code 4. Build it first
(see `tools/README.md`):

```bash
cd tools
uv run python -m talks_build build --export "<Keynote HTML export folder>" \
    --talk ../talks-src/singular-bursting-bubbles/talk.yaml
```

`ffprobe` and `tesseract` are optional: the tests that need them are skipped
when they are missing.

## Running

```bash
cd tests
uv run pytest                          # both engines, YouTube answered by local stand-ins
uv run pytest --engine webkit          # one engine (repeatable)
uv run pytest --live-youtube           # also load the real YouTube player (network)
uv run pytest -k span -s               # a subset, with the timing traces printed
uv run pytest --slug=<slug> --site=<site dir> --build=<build root>   # another build
```

Pass path options as `--option=value`: pytest reads a bare path argument as a
test location.

The server fixture serves `site/` at `/talks/` and `build/<slug>/media/` at
`/media/<slug>/`, as GitHub Pages and R2 do in production, and opens
`/talks/<slug>/?media=/media/<slug>/` (the player's localhost-only media
override). It answers byte ranges, which WebKit needs to play MP4. Every
request that would leave the machine is intercepted: the YouTube IFrame API
and embed get the stand-ins in `stubs/`, which record what the player asks
for, and anything else fails the test.

For a manual look at the same layout:

```bash
uv run python serve.py --slug singular-bursting-bubbles     # prints the URL to open
```

## What is tested

| File | Checks |
|---|---|
| `test_walk.py` | (a) Every click state is reached with the right arrow and shows its still, overlay and media, with no console error, CSP violation, failed request or stray `<video>`; the counter, `aria-label` and deep link follow. A second walk in full screen at 1920 x 1080 compares the screen with each state's still and overlay outside the media boxes. (d) Every video element is muted, `playsinline`, looping, `preload="auto"`, without controls, with its poster and source. |
| `test_media.py` | (b) Slide 4: both videos present, visible, uncovered except by the overlay, playing, and showing their own frames on screen. (c) Each span group (slides 21-24, 34-36, 37-38, 106-110) keeps the same `<video>` element while its time keeps running, forwards and back, and detaches it on leaving. (e) In every state with an overlay, `elementFromPoint` at label pixels hits the overlay and the screen shows the label above the video. Videos wrap at their end without `ended`. Seams: on scaled stages (1366 x 768, 1440 x 900 at 2x, 412 x 915 at 2.625x) no video shows through its overlay. |
| `test_controls.py` | (f) The pause button, P and K pause and resume all media; media mounted while paused stays paused; reduced motion starts paused and never dissolves. (g) Deep links (slide 34, 1-based state suffix, case, clamping, unknown hash), Home and End, no history entries, key repeat, every navigation key and stage clicks. The live region announces a new slide, then each later click step. |
| `test_youtube.py` | (h) Slide 6 creates one youtube-nocookie IFrame player with the SPEC parameters at its rectangle, nothing over it, the loop at `end`, and a "Watch on YouTube" link when YouTube is blocked, or when only the embed host is blocked (after the 10 s readiness timeout). Keys still navigate after a click inside the embed. The rectangle is clear of slide content, with the title above and the credit below; the state before the movie starts shows its poster there. With `--live-youtube`, the real player starts at `start`, plays muted and loops back from `end`. |
| `test_index.py` | (i) The talks index lists the talk and its card opens it; with production `media_base`, and the `?media=` override ignored off localhost. |
| `test_mobile.py` | (j) At 390 x 844 with touch: swipes and taps navigate, short or vertical drags do not, every player control is on screen with a 44 px target, and the full-screen fallback for phones without element full screen keeps its controls in the letterbox, makes the page behind it inert (Tab reaches only visible controls), shows a focus ring of at least 3:1 on black in both themes, and gives focus back on leaving. The help text explains swiping on the YouTube slide. |
| `test_shell.py` | (k) comphy-lab.org's header and footer on the index and the talk page: logo, the main site's items in order with Talks after Project docs and the only `aria-current` (`page` on the index, `true` on a talk page), the icon links, the footer columns and partner logos, the current year, no CSP violation or console error. The fixed header never covers the stage, and stage and controls fit 1440 x 900 and a phone held sideways. Exactly one theme toggle, in the header, sharing `localStorage` `theme` across reloads and both pages. With focus on header controls, arrows, Page Up/Down, P, K and F still drive the player. The bar fits at 1024 px and becomes the Menu drawer at 1023 px. On a phone the drawer opens with focus inside, keeps Tab and Shift+Tab inside, and closes on Escape (focus back on Menu), Close, a tap outside (which does not change slide) and the header turning inert. While it is open no player key reaches the slides (nor F full screen); full screen hides the header. On a phone held sideways the drawer is taller than the screen and End scrolls it to its last item. "Back to top" keeps the deep link. |
| `test_build.py` | Files only: content-addressed media, span keys on consecutive states, the four span groups, 15 movies, SPEC section 4 encodes, overlays only over movies (plus their bleed band, which repeats the still), video boxes that line up with the poster baked into the still, the slide 4 corrections in the slide text and (with Tesseract) in the image, and titles and text that carry the slides' maths (no mis-decoded TeX glyphs, no transparent text). |

## Notes on method

- The page CSP forbids `eval`, so Playwright's `wait_for_function` cannot
  poll; `talk_helpers.wait_until` polls `page.evaluate` instead.
- Stills, videos and the overlay are `pointer-events: none` in production, so
  clicks reach the stage. The hit tests switch pointer events on for the
  painted layers only while they call `elementFromPoint`, so the answer is the
  paint order.
- Pixel checks run in full screen at 1920 x 1080, one screen pixel per canvas
  pixel, except the seam test, which runs on scaled stages: it paints every
  video solid black, then solid white (a CSS filter, same compositing), and
  requires the two captures to agree away from where a video is meant to
  show. Video frames are read back through a canvas (the media is
  same-origin under the test server).
- The YouTube stand-in reports ready only when its embed page posts a
  message, as the real API does, so blocking the embed host
  (`open_talk(youtube="partial")`) leaves the player unready.
- WebKit, like Safari, moves Tab through buttons only with Option held; the
  focus tests press `Alt+Tab` there.
- Chromium swipes are real touch input through the DevTools protocol.
  Playwright cannot inject touch moves into WebKit, so there the swipe is a
  sequence of `PointerEvent`s with `pointerType: 'touch'`; taps are real in
  both.
- In WebKit, Playwright's own screenshot code injects an inline style that the
  page CSP refuses. `TalkPage.screenshot` discounts only that report.
- In WebKit, the first screenshot of a page can be offset by one device
  pixel from every later one when the stage sits at a fractional device
  offset (the talk page at 412 x 915 and 2.625x). The seam test takes and
  discards one capture before comparing its black and white captures.
- Playwright's WebKit build does not advance YouTube's own playback (a bare
  youtube-nocookie embed stalls on its first frame the same way, headless or
  headed), so the live test marks that one check as an expected failure in
  WebKit. Everything the player controls is still checked there.

## Screenshots for review

```bash
uv run python screenshots.py --out <dir> --pdf "<flat Keynote PDF>" [--live-youtube] [--engine webkit]
```

This writes `<dir>/<engine>/<n>-<k>.png` for every state (slide `n`, state
`k` counted from 1, full screen at 1920 x 1080 with the videos playing),
`<dir>/keynote/<n>.png` (the flat PDF at 1920 px wide, rendered with
poppler's `pdftoppm`), and `<dir>/compare.json`, the closest state to each
Keynote page with their mean difference over the slide and outside the media
boxes.
