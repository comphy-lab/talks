/**
 * CoMPhy Lab talks player (docs/SPEC.md, Section 3.2).
 *
 * Plays one talk from ./manifest.json. Every click state of every slide is
 * one step in a flat sequence. A state is a full-canvas still `image`, a list
 * of media placements (videos, YouTube embeds) and an optional transparent
 * `overlay` drawn above the media. The z-order is image < media < overlay < UI.
 *
 * Keyed media reconciliation
 *   Each placement has the key `kind:ref:x,y,w,h` (rect rounded to 1 px).
 *   On every state change the player keeps, untouched, every mounted element
 *   whose key is in the target state: it is never re-created, re-attached or
 *   seeked, so a video that spans several states or slides keeps playing.
 *   Elements whose key is absent are paused and detached; new keys get new
 *   elements that start from the beginning (YouTube: from `start`).
 *
 * Deep links
 *   #<first 8 hex chars of the slide UUID>        first state of that slide
 *   #<first 8 hex chars>.<k>                      state k of that slide, 1-based
 *   The hash follows the state on screen through history.replaceState; the
 *   first state is written without a suffix. Writes are coalesced to one per
 *   250 ms: WebKit throws a SecurityError after 100 replaceState calls in
 *   10 s, which holding an arrow key reaches in a few seconds.
 *
 * Read-only test hook (harmless in production)
 *   window.__talk.state() -> { slide, stateIndex, keys, paused } or null
 *     slide: 1-based slide number; stateIndex: 0-based state within it;
 *     keys: placement keys of the mounted media, in z-order;
 *     paused: whether media is paused by the pause control.
 *     Reflects the state on screen, which may lag a key press while a
 *     still that was not preloaded is decoding.
 *   window.__talk.media() -> [{ key, el }] mounted media in z-order;
 *     `el` is the <video>, or the wrapper <div> of a YouTube embed.
 *
 * Local testing
 *   On localhost or 127.0.0.1 only, `?media=<same-origin relative path>`
 *   replaces `media_base`. The override is ignored on every other host.
 *
 * YouTube embeds
 *   A click inside the embed moves keyboard focus into YouTube's
 *   cross-origin frame, where the talk's keys (and a presenter's clicker)
 *   would no longer arrive; focus returns to the stage frame at once. If the
 *   player has not reported ready 10 s after it was created (the API script
 *   loaded but the embed host is blocked), the "Watch on YouTube" link
 *   replaces it.
 */

const FILE_RE = /^[0-9a-f]{16}\.(webp|mp4)$/;
const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const HASH_RE = /^#?([0-9a-f]{8})(?:\.(\d{1,4}))?$/i;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1']);

const YT_API_SRC = 'https://www.youtube.com/iframe_api';
const YT_HOST = 'https://www.youtube-nocookie.com';
const YT_API_TIMEOUT_MS = 8000;
const YT_READY_TIMEOUT_MS = 10000;
const YT_LOOP_POLL_MS = 250;
// YouTube API Services terms: an embedded player's viewport must be at least
// 200 x 200 CSS px. Below that (e.g. slide 6 on a phone) show the fallback link.
const YT_MIN_PX = 200;
const YT_UNSTARTED = -1;
const YT_ENDED = 0;
const YT_PLAYING = 1;
const YT_CUED = 5;

const DECODE_TIMEOUT_MS = 4000;
const DISSOLVE_MS = 500;
const SWIPE_MIN_PX = 40;
const HASH_WRITE_MS = 250;
const HASH_RETRY_MS = 2000;

const root = document.documentElement;
const byId = (id) => document.getElementById(id);

const ui = {
  viewer: byId('viewer'),
  frame: byId('stage-frame'),
  stage: byId('stage'),
  imageLayer: byId('layer-image'),
  mediaLayer: byId('layer-media'),
  overlayLayer: byId('layer-overlay'),
  stageUi: byId('stage-ui'),
  prev: byId('btn-prev'),
  next: byId('btn-next'),
  pause: byId('btn-pause'),
  fullscreen: byId('btn-fullscreen'),
  pauseFs: byId('btn-pause-fs'),
  exitFs: byId('btn-exit-fs'),
  counterNum: byId('counter-num'),
  counterTitle: byId('counter-title'),
  progressBar: byId('progress-bar'),
  announcer: byId('announcer'),
  slideText: byId('slide-text'),
  title: byId('talk-title'),
  subtitle: byId('talk-subtitle'),
  speaker: byId('talk-speaker'),
  updated: byId('talk-updated'),
  changelog: byId('changelog'),
};

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

/* ---------------------------------------------------------------- state */

let slides = [];
let videos = {};
let youtube = {};
let mediaBase = '';
let canvasW = 1920;
let canvasH = 1080;

/** Flat sequence of steps: { si: slide index, k: state index }. */
let steps = [];
/** Index into `steps` of the first state of each slide. */
let slideStart = [];

let committed = -1; // step on screen
let target = -1; // step requested most recently
let navToken = 0;
let paused = reducedMotion.matches;
let suppressClickUntil = 0;
let shownTextSlide = -1;
let announcedSlide = -1;

/** Mounted media, key -> entry, in z-order. */
let mounted = new Map();
/** Preloaded stills, url -> HTMLImageElement. */
const preloaded = new Map();
/** Finishers of running dissolves. */
const fades = new Set();

/* -------------------------------------------------------------- helpers */

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value));
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** Resolve a content-addressed media file name against media_base. */
function mediaUrl(file) {
  if (typeof file !== 'string' || !FILE_RE.test(file)) return null;
  return new URL(file, mediaBase).href;
}

function resolveMediaBase(manifestBase) {
  let base = manifestBase;
  if (LOCAL_HOSTS.has(window.location.hostname)) {
    const override = new URLSearchParams(window.location.search).get('media');
    const isRelative = override && !override.startsWith('//') && !/^[a-z][a-z0-9+.-]*:/i.test(override);
    if (isRelative) {
      const url = new URL(override.endsWith('/') ? override : `${override}/`, window.location.href);
      if (url.origin === window.location.origin) base = url.href;
    }
  }
  return base;
}

function isRect(rect) {
  return Array.isArray(rect) && rect.length === 4 && rect.every(Number.isFinite) && rect[2] > 0 && rect[3] > 0;
}

function validPlacement(m) {
  return Boolean(m) && (m.kind === 'video' || m.kind === 'youtube') && typeof m.ref === 'string' && isRect(m.rect);
}

/** The rect rounded to 1 px, as in the placement key. `+ 0` folds -0 into 0. */
function roundRect(rect) {
  return rect.map((v) => Math.round(v) + 0);
}

/** SPEC 3.2: kind + ref + rect rounded to 1 px. */
function placementKey(m) {
  return `${m.kind}:${m.ref}:${roundRect(m.rect).join(',')}`;
}

/**
 * Position an element over its canvas rectangle, in percent of the stage.
 * It uses the rounded rectangle its key encodes, so a placement is a function
 * of its key alone and a movie never half-covers a pixel row or column that
 * the still or overlay leaves uncovered.
 */
function place(node, rect) {
  const [x, y, w, h] = roundRect(rect);
  node.style.left = `${(x / canvasW) * 100}%`;
  node.style.top = `${(y / canvasH) * 100}%`;
  node.style.width = `${(w / canvasW) * 100}%`;
  node.style.height = `${(h / canvasH) * 100}%`;
}

function slidePrefix(slide) {
  return String(slide.id || '')
    .replace(/[^0-9a-f]/gi, '')
    .slice(0, 8)
    .toLowerCase();
}

function slideTitle(slide, si) {
  const title = typeof slide.title === 'string' ? slide.title.trim() : '';
  return title || `Slide ${si + 1}`;
}

function stepAt(index) {
  const { si, k } = steps[index];
  return { si, k, slide: slides[si], state: slides[si].states[k] };
}

function formatDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!match) return String(iso || '');
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
}

/* ------------------------------------------------------------ dissolves */

function flushStyle(node) {
  // Reading a computed value commits the start state of the transition.
  return window.getComputedStyle(node).opacity;
}

function fadeIn(node, done) {
  node.classList.add('fade', 'is-transparent');
  flushStyle(node);
  node.classList.remove('is-transparent');
  let timer = 0;
  const finish = () => {
    if (!fades.delete(finish)) return;
    clearTimeout(timer);
    node.classList.remove('fade');
    if (done) done();
  };
  timer = setTimeout(finish, DISSOLVE_MS + 50);
  fades.add(finish);
}

function fadeOut(node) {
  node.classList.add('fade', 'is-leaving');
  flushStyle(node);
  node.classList.add('is-transparent');
  let timer = 0;
  const finish = () => {
    if (!fades.delete(finish)) return;
    clearTimeout(timer);
    node.remove();
  };
  timer = setTimeout(finish, DISSOLVE_MS + 50);
  fades.add(finish);
}

function finishFades() {
  for (const finish of [...fades]) finish();
}

/* --------------------------------------------------------------- stills */

function makeStill(url, className) {
  const img = el('img', `still ${className}`);
  img.alt = '';
  img.decoding = 'async';
  img.draggable = false;
  img.dataset.src = url;
  img.addEventListener(
    'error',
    () => {
      img.classList.add('is-broken');
      console.warn(`Slide image failed to load: ${url}`);
    },
    { once: true },
  );
  img.src = url;
  return img;
}

function isDecoded(img) {
  return img.complete && img.naturalWidth > 0;
}

function whenDecoded(img) {
  if (typeof img.decode === 'function') return img.decode().catch(() => undefined);
  return new Promise((resolve) => {
    img.addEventListener('load', resolve, { once: true });
    img.addEventListener('error', resolve, { once: true });
  });
}

/** Swap the single still in a layer, keeping it when the file is unchanged. */
function swapLayer(layer, img, dissolve) {
  const shown = layer.firstElementChild;
  if (img && shown && shown.dataset.src === img.dataset.src) return;
  if (!dissolve || (!shown && !img)) {
    if (img) layer.replaceChildren(img);
    else layer.replaceChildren();
    return;
  }
  if (img) {
    layer.append(img);
    fadeIn(img, () => {
      if (shown) shown.remove();
    });
  } else if (shown) {
    fadeOut(shown);
  }
}

function preloadAround(index) {
  const wanted = new Set();
  for (const i of [index + 1, index + 2, index - 1]) {
    if (i < 0 || i >= steps.length) continue;
    const { state } = stepAt(i);
    for (const file of [state.image, state.overlay]) {
      const url = file ? mediaUrl(file) : null;
      if (url) wanted.add(url);
    }
  }
  for (const url of [...preloaded.keys()]) {
    if (!wanted.has(url)) preloaded.delete(url);
  }
  for (const url of wanted) {
    if (preloaded.has(url)) continue;
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    if (typeof img.decode === 'function') img.decode().catch(() => undefined);
    preloaded.set(url, img);
  }
}

/* --------------------------------------------------------------- videos */

function createVideoEntry(key, m) {
  const info = videos[m.ref];
  const src = info ? mediaUrl(info.src) : null;
  if (!src) {
    console.warn(`Unknown or invalid video placement: ${key}`);
    return null;
  }
  const video = el('video', 'media media--video');
  video.muted = true;
  video.defaultMuted = true;
  video.setAttribute('muted', '');
  video.playsInline = true;
  video.setAttribute('playsinline', '');
  video.loop = true;
  video.preload = 'auto';
  video.controls = false;
  video.disablePictureInPicture = true;
  video.setAttribute('disablepictureinpicture', '');
  video.setAttribute('disableremoteplayback', '');
  video.setAttribute('aria-hidden', 'true');
  video.tabIndex = -1;
  video.dataset.key = key;
  const poster = info.poster ? mediaUrl(info.poster) : null;
  if (poster) video.poster = poster;
  place(video, m.rect);

  let alive = true;
  video.addEventListener('error', () => {
    if (!alive) return;
    // The still beneath carries this movie's poster frame: hiding the
    // element shows it instead of a blank or broken box.
    video.classList.add('is-failed');
    console.warn(`Video failed to load: ${src}`);
  });
  video.src = src;

  return {
    key,
    kind: 'video',
    el: video,
    play() {
      const promise = video.play();
      if (promise && typeof promise.catch === 'function') {
        promise.catch((err) => {
          // Autoplay refused (for example, low-power mode): show the play
          // state so that one press of the pause button starts everything.
          if (alive && err && err.name === 'NotAllowedError' && !paused) setPaused(true);
        });
      }
    },
    pause() {
      video.pause();
    },
    destroy() {
      alive = false;
      video.pause();
      video.remove();
      // Release the decoder (mobile browsers limit concurrent players).
      video.removeAttribute('src');
      video.load();
    },
  };
}

/* -------------------------------------------------------------- YouTube */

let youTubeApi = null;

/** Load the IFrame Player API once, on first need; reject after 8 s. */
function loadYouTubeApi() {
  if (youTubeApi) return youTubeApi;
  youTubeApi = new Promise((resolve, reject) => {
    if (window.YT && typeof window.YT.Player === 'function') {
      resolve(window.YT);
      return;
    }
    const timer = setTimeout(() => reject(new Error('YouTube IFrame API timed out')), YT_API_TIMEOUT_MS);
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      clearTimeout(timer);
      if (typeof previous === 'function') previous();
      resolve(window.YT);
    };
    const script = document.createElement('script');
    script.src = YT_API_SRC;
    script.async = true;
    script.addEventListener('error', () => {
      clearTimeout(timer);
      reject(new Error('YouTube IFrame API failed to load'));
    });
    document.head.append(script);
  });
  return youTubeApi;
}

function youTubeFallback(info, rect, start) {
  const link = el('a', 'yt-fallback');
  link.href = `https://www.youtube.com/watch?v=${encodeURIComponent(info.id)}&t=${Math.floor(start)}s`;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  place(link, rect);

  const thumb = el('img', 'yt-fallback__thumb');
  thumb.alt = '';
  thumb.addEventListener('error', () => thumb.remove(), { once: true });
  thumb.src = `https://i.ytimg.com/vi/${encodeURIComponent(info.id)}/hqdefault.jpg`;

  const body = el('span', 'yt-fallback__body');
  body.append(el('span', 'yt-fallback__label', 'Watch on YouTube'));
  if (info.title) body.append(el('span', 'yt-fallback__title', info.title));
  if (info.credit) body.append(el('span', 'yt-fallback__credit', info.credit));
  body.append(el('span', 'sr-only', '(opens in a new tab)'));
  link.append(thumb, body);
  return link;
}

/** On-screen size, in CSS px, of a canvas rectangle on the current stage. */
function renderedSize(rect) {
  const stageEl = document.getElementById('stage');
  const r = stageEl ? stageEl.getBoundingClientRect() : null;
  if (!r || !r.width || !r.height) return null;
  const [, , w, h] = roundRect(rect);
  return { w: (w / canvasW) * r.width, h: (h / canvasH) * r.height };
}

function createYouTubeEntry(key, m) {
  const info = youtube[m.ref];
  if (!info || typeof info.id !== 'string' || !YT_ID_RE.test(info.id)) {
    console.warn(`Unknown or invalid YouTube placement: ${key}`);
    return null;
  }
  const start = Number.isFinite(info.start) && info.start > 0 ? info.start : 0;
  const end = Number.isFinite(info.end) && info.end > start ? info.end : null;

  const box = el('div', 'media media--youtube');
  box.dataset.key = key;
  place(box, m.rect);
  const slot = el('div', 'media__slot');
  box.append(slot);

  let player = null;
  let ready = false;
  let alive = true;
  let poll = 0;
  let readyTimer = 0;
  let fallback = null;

  const teardown = () => {
    clearInterval(poll);
    clearTimeout(readyTimer);
    ready = false;
    if (player) {
      try {
        player.destroy();
      } catch (err) {
        /* The player may not have finished initialising. */
      }
    }
    player = null;
  };

  const showFallback = () => {
    if (!alive || fallback) return;
    teardown();
    box.classList.add('is-fallback');
    fallback = youTubeFallback(info, m.rect, start);
    ui.stageUi.append(fallback);
  };

  const restart = () => {
    player.seekTo(start, true);
    player.playVideo();
  };

  const entry = {
    key,
    kind: 'youtube',
    el: box,
    play() {
      if (!ready) return;
      // Not yet started (cued while paused) or ended: begin exactly at `start`.
      const playerState = player.getPlayerState();
      if (playerState === YT_ENDED || playerState === YT_CUED || playerState === YT_UNSTARTED) restart();
      else player.playVideo();
    },
    pause() {
      if (ready) player.pauseVideo();
    },
    destroy() {
      alive = false;
      teardown();
      box.remove();
      if (fallback) fallback.remove();
    },
  };

  // Too small to embed within YouTube's terms: link out instead.
  const size = renderedSize(m.rect);
  if (size && (size.w < YT_MIN_PX || size.h < YT_MIN_PX)) {
    box.classList.add('is-too-small');
    showFallback();
    return entry;
  }

  loadYouTubeApi()
    .then((YT) => {
      if (!alive) return;
      const playerVars = {
        autoplay: 0,
        mute: 1,
        controls: 0,
        playsinline: 1,
        rel: 0,
        disablekb: 1,
        fs: 0,
        iv_load_policy: 3,
        start: Math.floor(start),
        origin: window.location.origin,
      };
      if (end !== null) playerVars.end = Math.ceil(end);
      player = new YT.Player(slot, {
        host: YT_HOST,
        videoId: info.id,
        width: '100%',
        height: '100%',
        playerVars,
        events: {
          onReady() {
            if (!alive) return;
            clearTimeout(readyTimer);
            ready = true;
            const frame = typeof player.getIframe === 'function' ? player.getIframe() : null;
            if (frame) {
              frame.title = info.title ? `YouTube: ${info.title}` : 'YouTube video';
              frame.tabIndex = -1;
            }
            player.mute();
            if (!paused) restart();
            // Stop at `end` to the millisecond; the `end` player parameter
            // takes whole seconds only.
            if (end !== null) {
              poll = setInterval(() => {
                if (ready && player.getPlayerState() === YT_PLAYING && player.getCurrentTime() >= end) {
                  player.seekTo(start, true);
                }
              }, YT_LOOP_POLL_MS);
            }
          },
          onStateChange(event) {
            if (alive && ready && event.data === YT_ENDED && !paused) restart();
          },
          onError: showFallback,
        },
      });
      // The API loaded, but the player itself may never come up (the embed
      // host blocked by a filter): fall back rather than leave a black box.
      readyTimer = setTimeout(() => {
        if (!ready) showFallback();
      }, YT_READY_TIMEOUT_MS);
    })
    .catch(showFallback);

  return entry;
}

function createEntry(key, m) {
  return m.kind === 'video' ? createVideoEntry(key, m) : createYouTubeEntry(key, m);
}

/* ------------------------------------------------------- state changes */

/** Mount `steps[index]`. Steps 1-5 of SPEC 3.2, in order. */
function commit(index, stills, animate) {
  const { si, state } = stepAt(index);
  const dissolve = animate && state.transition === 'dissolve' && !reducedMotion.matches;
  finishFades();

  // 1. The target state's keys, in z-order.
  const wanted = [];
  const seen = new Set();
  for (const m of Array.isArray(state.media) ? state.media : []) {
    if (!validPlacement(m)) continue;
    const key = placementKey(m);
    if (seen.has(key)) continue;
    seen.add(key);
    wanted.push([key, m]);
  }

  // 3. Pause and detach every element whose key is not in the target set.
  for (const [key, entry] of mounted) {
    if (!seen.has(key)) entry.destroy();
  }

  // 2 and 4. Keep surviving elements untouched; create the new ones.
  const next = new Map();
  wanted.forEach(([key, m], i) => {
    const z = String(i + 1);
    let entry = mounted.get(key);
    if (entry) {
      if (entry.el.style.zIndex !== z) entry.el.style.zIndex = z;
    } else {
      entry = createEntry(key, m);
      if (!entry) return;
      entry.el.style.zIndex = z;
      ui.mediaLayer.append(entry.el);
      if (dissolve) fadeIn(entry.el);
      if (!paused) entry.play();
    }
    next.set(key, entry);
  });
  mounted = next;

  // 5. Swap the still and the overlay.
  swapLayer(ui.imageLayer, stills.image, dissolve);
  swapLayer(ui.overlayLayer, stills.overlay, dissolve);

  committed = index;
  updateChrome(index, si);
  preloadAround(index);
}

function go(index, { animate = false } = {}) {
  if (!steps.length) return;
  const to = clamp(index, 0, steps.length - 1);
  if (to === target) return;
  target = to;
  const token = ++navToken;
  const { state } = stepAt(to);
  const imageUrl = state.image ? mediaUrl(state.image) : null;
  const overlayUrl = state.overlay ? mediaUrl(state.overlay) : null;
  const stills = {
    image: imageUrl ? makeStill(imageUrl, 'still--image') : null,
    overlay: overlayUrl ? makeStill(overlayUrl, 'still--overlay') : null,
  };
  const pending = [stills.image, stills.overlay].filter((img) => img && !isDecoded(img));
  if (!pending.length) {
    setBusy(false);
    commit(to, stills, animate);
    return;
  }
  // Keep the current state on screen until the next still can be painted,
  // so a step never flashes an empty stage.
  setBusy(true);
  Promise.race([Promise.all(pending.map(whenDecoded)), delay(DECODE_TIMEOUT_MS)]).then(() => {
    if (token !== navToken) return;
    setBusy(false);
    commit(to, stills, animate);
  });
}

function setBusy(on) {
  ui.frame.classList.toggle('is-loading', on);
  if (on) ui.stage.setAttribute('aria-busy', 'true');
  else ui.stage.removeAttribute('aria-busy');
}

function goNext() {
  go(target + 1, { animate: true });
}

function goPrev() {
  go(target - 1);
}

function stepFromHash(hash) {
  const match = HASH_RE.exec(hash || '');
  if (!match) return -1;
  const prefix = match[1].toLowerCase();
  const si = slides.findIndex((slide) => slidePrefix(slide) === prefix);
  if (si < 0) return -1;
  const count = slides[si].states.length;
  const k = match[2] ? clamp(parseInt(match[2], 10), 1, count) - 1 : 0;
  return slideStart[si] + k;
}

function hashFor(index) {
  const { si, k } = steps[index];
  const prefix = slidePrefix(slides[si]);
  return k === 0 ? `#${prefix}` : `#${prefix}.${k + 1}`;
}

/* Deep-link writes, coalesced (see the header comment). */
let hashTimer = 0;
let pendingHash = null;
let lastHashWrite = -Infinity;

function flushHash() {
  hashTimer = 0;
  const hash = pendingHash;
  pendingHash = null;
  if (hash === null || window.location.hash === hash) return;
  try {
    window.history.replaceState(window.history.state, '', hash);
    lastHashWrite = performance.now();
  } catch (err) {
    // Rate-limited by the browser: keep the latest hash and try again later.
    if (pendingHash === null) pendingHash = hash;
    hashTimer = setTimeout(flushHash, HASH_RETRY_MS);
  }
}

function cancelHashWrite() {
  clearTimeout(hashTimer);
  hashTimer = 0;
  pendingHash = null;
}

function writeHash(hash) {
  pendingHash = hash;
  if (hashTimer) return;
  hashTimer = setTimeout(flushHash, Math.max(0, lastHashWrite + HASH_WRITE_MS - performance.now()));
}

function updateChrome(index, si) {
  const slide = slides[si];
  const title = slideTitle(slide, si);
  const n = si + 1;
  const total = slides.length;

  ui.counterNum.textContent = `${n} / ${total}`;
  ui.counterTitle.textContent = title;
  ui.stage.setAttribute('aria-label', title);
  // A new slide is announced with its title; a later click step of the same
  // slide only with its position, so every press of Next is heard.
  const { k } = steps[index];
  const count = slide.states.length;
  const step = count > 1 ? `step ${k + 1} of ${count}` : '';
  let message;
  if (announcedSlide !== si) {
    message = `Slide ${n} of ${total}${step ? `, ${step}` : ''}: ${title}`;
    announcedSlide = si;
  } else {
    message = step ? `${step.charAt(0).toUpperCase()}${step.slice(1)}` : `Slide ${n} of ${total}: ${title}`;
  }
  if (ui.announcer.textContent !== message) ui.announcer.textContent = message;
  if (shownTextSlide !== si) {
    ui.slideText.textContent = typeof slide.text === 'string' ? slide.text : '';
    shownTextSlide = si;
  }
  ui.prev.setAttribute('aria-disabled', String(index === 0));
  ui.next.setAttribute('aria-disabled', String(index === steps.length - 1));
  ui.progressBar.style.transform = `scaleX(${(index + 1) / steps.length})`;

  writeHash(hashFor(index));
}

/* ------------------------------------------------------------- controls */

function setPaused(value) {
  paused = value;
  for (const entry of mounted.values()) {
    if (paused) entry.pause();
    else entry.play();
  }
  const label = paused ? 'Play videos' : 'Pause videos';
  for (const button of [ui.pause, ui.pauseFs]) {
    if (!button) continue;
    button.dataset.paused = String(paused);
    button.setAttribute('aria-label', label);
    button.title = `${label} (P or K)`;
  }
}

function fullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

/**
 * While the stage fills the screen, the page around it is out of sight: make
 * it inert so that Tab, and a screen reader's swipe, cannot land on hidden
 * controls. The live region and the slide text stay available.
 */
const CHROME_SELECTORS = ['.skip-link', '.site-header', '.talk-head', '.progress', '.controls', '.about', '.site-footer'];

function setChromeInert(on) {
  for (const selector of CHROME_SELECTORS) {
    for (const node of document.querySelectorAll(selector)) node.inert = on;
  }
}

/** The control that had focus before full screen, given it back on leaving. */
let fsReturnFocus = null;

function rememberFocus() {
  const active = document.activeElement;
  fsReturnFocus = active && active !== document.body && !ui.frame.contains(active) ? active : null;
}

function setPseudoFullscreen(on) {
  if (on) rememberFocus();
  root.classList.toggle('is-pseudo-fullscreen', on);
  // Keep keyboard focus on a control that stays visible before the rest goes inert.
  if (on && fsReturnFocus && ui.exitFs) ui.exitFs.focus({ preventScroll: true });
  updateFullscreenButton();
}

function updateFullscreenButton() {
  const on = Boolean(fullscreenElement()) || root.classList.contains('is-pseudo-fullscreen');
  const was = ui.fullscreen.dataset.active === 'true';
  setChromeInert(on);
  if (was && !on) {
    const active = document.activeElement;
    const lost =
      !active || active === document.body || active === ui.frame || (active.closest && active.closest('#stage-tools'));
    if (lost) (fsReturnFocus && fsReturnFocus.isConnected ? fsReturnFocus : ui.fullscreen).focus({ preventScroll: true });
    fsReturnFocus = null;
  }
  ui.fullscreen.dataset.active = String(on);
  ui.fullscreen.setAttribute('aria-label', on ? 'Exit full screen' : 'Enter full screen');
  ui.fullscreen.title = on ? 'Exit full screen (F)' : 'Full screen (F)';
}

function toggleFullscreen() {
  if (fullscreenElement()) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) {
      const result = exit.call(document);
      if (result && typeof result.catch === 'function') result.catch(() => undefined);
    }
    return;
  }
  if (root.classList.contains('is-pseudo-fullscreen')) {
    setPseudoFullscreen(false);
    return;
  }
  const request = ui.frame.requestFullscreen || ui.frame.webkitRequestFullscreen;
  rememberFocus();
  if (!request) {
    // iPhone Safari has no element full screen: fill the viewport instead.
    setPseudoFullscreen(true);
    return;
  }
  try {
    const result = request.call(ui.frame);
    if (result && typeof result.catch === 'function') result.catch(() => setPseudoFullscreen(true));
  } catch (err) {
    setPseudoFullscreen(true);
  }
}

function onKeyDown(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  const origin = event.target instanceof Element ? event.target : null;
  if (origin && origin.closest('input, textarea, select, [contenteditable="true"]')) return;
  const onControl = Boolean(origin && origin.closest('button, a[href]'));

  switch (event.key) {
    case 'ArrowRight':
    case 'ArrowDown':
    case 'PageDown':
      goNext();
      break;
    case ' ':
    case 'Spacebar':
    case 'Enter':
      // On a focused button or link these keys activate it instead.
      if (onControl) return;
      goNext();
      break;
    case 'ArrowLeft':
    case 'ArrowUp':
    case 'PageUp':
    case 'Backspace':
      goPrev();
      break;
    case 'Home':
      go(0);
      break;
    case 'End':
      go(steps.length - 1);
      break;
    case 'f':
    case 'F':
      toggleFullscreen();
      break;
    case 'p':
    case 'P':
    case 'k':
    case 'K':
      setPaused(!paused);
      break;
    case 'Escape':
      if (!root.classList.contains('is-pseudo-fullscreen')) return;
      setPseudoFullscreen(false);
      break;
    default:
      return;
  }
  event.preventDefault();
}

function bindControls() {
  document.addEventListener('keydown', onKeyDown);

  ui.prev.addEventListener('click', goPrev);
  ui.next.addEventListener('click', goNext);
  ui.pause.addEventListener('click', () => setPaused(!paused));
  ui.fullscreen.addEventListener('click', toggleFullscreen);
  // Full-screen controls (shown where there may be no keyboard).
  if (ui.pauseFs) ui.pauseFs.addEventListener('click', () => setPaused(!paused));
  if (ui.exitFs) ui.exitFs.addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);

  // A click on the right half of the stage moves on; the left half goes back.
  ui.frame.addEventListener('click', (event) => {
    if (event.button !== 0 || performance.now() < suppressClickUntil) return;
    if (event.target instanceof Element && event.target.closest('a, button')) return;
    const rect = ui.frame.getBoundingClientRect();
    if (event.clientX >= rect.left + rect.width / 2) goNext();
    else goPrev();
  });

  // Swipe left for next, right for previous (touch and pen).
  let swipe = null;
  ui.frame.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'mouse' || !event.isPrimary) return;
    swipe = { id: event.pointerId, x: event.clientX, y: event.clientY };
  });
  ui.frame.addEventListener('pointerup', (event) => {
    if (!swipe || event.pointerId !== swipe.id) return;
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) >= SWIPE_MIN_PX && Math.abs(dx) > 1.5 * Math.abs(dy)) {
      suppressClickUntil = performance.now() + 500;
      if (dx < 0) goNext();
      else goPrev();
    }
  });
  ui.frame.addEventListener('pointercancel', () => {
    swipe = null;
  });

  window.addEventListener('hashchange', (event) => {
    // The user's hash wins over a coalesced write still pending from the
    // previous step, which could otherwise land before this event runs.
    cancelHashWrite();
    let hash = window.location.hash;
    try {
      hash = new URL(event.newURL).hash;
    } catch (err) {
      /* Keep location.hash. */
    }
    const index = stepFromHash(hash);
    if (index >= 0 && index !== target) go(index);
    else if (target >= 0) writeHash(hashFor(target)); // canonical form, or undo an unknown hash
  });

  // A click inside a YouTube embed gives its cross-origin frame the keyboard
  // focus, so keys and presenter clickers stop reaching the talk. The click
  // itself still reaches YouTube; focus then comes straight back.
  // The stage frame takes that focus: it lies inside the full-screen element
  // too, where the browser refuses focus to anything outside.
  ui.frame.tabIndex = -1;
  window.addEventListener('blur', () => {
    setTimeout(() => {
      const active = document.activeElement;
      if (active && active.tagName === 'IFRAME' && ui.mediaLayer.contains(active)) {
        ui.frame.focus({ preventScroll: true });
      }
    }, 0);
  });

  // Browsers may suspend background video; resume, without seeking, on return.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !paused) {
      for (const entry of mounted.values()) if (entry.kind === 'video') entry.play();
    }
  });

  setPaused(paused);
  updateFullscreenButton();
}

/* ----------------------------------------------------------------- boot */

function showError(message) {
  const box = el('div', 'stage-error');
  box.setAttribute('role', 'alert');
  box.append(el('p', 'stage-error__title', 'This talk could not be loaded.'), el('p', 'stage-error__detail', message));
  ui.stageUi.replaceChildren(box);
  ui.stage.setAttribute('aria-label', 'Slides unavailable');
}

function checkManifest(manifest) {
  if (!manifest || manifest.schema !== 1) throw new Error('Unsupported manifest schema.');
  if (!Array.isArray(manifest.slides) || manifest.slides.length === 0) throw new Error('The manifest has no slides.');
  manifest.slides.forEach((slide, i) => {
    if (!slide || !Array.isArray(slide.states) || slide.states.length === 0) {
      throw new Error(`Slide ${i + 1} has no states.`);
    }
  });
  if (typeof manifest.media_base !== 'string') throw new Error('The manifest has no media_base.');
}

function fillHeader(manifest) {
  if (manifest.title) ui.title.textContent = manifest.title;
  ui.subtitle.textContent = manifest.subtitle || '';
  ui.speaker.textContent = manifest.speaker ? `Talk · ${manifest.speaker}` : 'Talk';
  if (manifest.updated) {
    const time = el('time', null, formatDate(manifest.updated));
    time.dateTime = manifest.updated;
    ui.updated.replaceChildren('Updated ', time);
  }
  const items = Array.isArray(manifest.changelog) ? [...manifest.changelog] : [];
  items.sort((a, b) => String(b.date).localeCompare(String(a.date)));
  ui.changelog.replaceChildren(
    ...items.map((item) => {
      const li = el('li');
      const time = el('time', null, formatDate(item.date));
      time.dateTime = String(item.date || '');
      li.append(time, el('span', null, item.note || ''));
      return li;
    }),
  );
}

function init(manifest) {
  slides = manifest.slides;
  videos = manifest.videos || {};
  youtube = manifest.youtube || {};
  if (manifest.canvas && manifest.canvas.width > 0 && manifest.canvas.height > 0) {
    canvasW = manifest.canvas.width;
    canvasH = manifest.canvas.height;
  }
  mediaBase = resolveMediaBase(manifest.media_base);

  steps = [];
  slideStart = [];
  slides.forEach((slide, si) => {
    slideStart.push(steps.length);
    slide.states.forEach((_, k) => steps.push({ si, k }));
  });

  fillHeader(manifest);
  const first = stepFromHash(window.location.hash);
  go(first >= 0 ? first : 0);
}

const hook = Object.freeze({
  state() {
    if (committed < 0) return null;
    const { si, k } = steps[committed];
    return { slide: si + 1, stateIndex: k, keys: [...mounted.keys()], paused };
  },
  media() {
    return [...mounted.values()].map((entry) => ({ key: entry.key, el: entry.el }));
  },
});
Object.defineProperty(window, '__talk', { value: hook, enumerable: false, configurable: false, writable: false });

async function main() {
  bindControls();
  try {
    const response = await fetch('./manifest.json', { cache: 'no-cache', credentials: 'same-origin' });
    if (!response.ok) throw new Error(`manifest.json returned HTTP ${response.status}.`);
    const manifest = await response.json();
    checkManifest(manifest);
    init(manifest);
  } catch (err) {
    showError(err instanceof Error ? err.message : String(err));
    console.error(err);
  }
}

main();
