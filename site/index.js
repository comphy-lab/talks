/**
 * Talks index: renders one card per talk from ./talks.json.
 *
 * Accepted shapes: an array of talks, or { "talks": [...] }. Each talk needs
 * `slug` and `title`; `subtitle`, `speaker`, `updated` (ISO date) and
 * `cover` (an https://media.comphy-lab.org/talks/ URL) are optional.
 * DOM is built with createElement/textContent only.
 */

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const COVER_PREFIX = 'https://media.comphy-lab.org/talks/';

const list = document.getElementById('talk-list');
const status = document.getElementById('talk-list-status');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function formatDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function card(talk) {
  const item = el('li');
  const article = el('article', 'talk-card');

  if (typeof talk.cover === 'string' && talk.cover.startsWith(COVER_PREFIX)) {
    const cover = el('img', 'talk-card__cover');
    cover.alt = '';
    cover.loading = 'lazy';
    cover.decoding = 'async';
    cover.addEventListener('error', () => cover.remove(), { once: true });
    cover.src = talk.cover;
    article.append(cover);
  }

  const body = el('div', 'talk-card__body');
  const heading = el('h2', 'talk-card__title');
  const link = el('a', 'talk-card__link', talk.title);
  link.href = `./${talk.slug}/`;
  heading.append(link);
  body.append(heading);

  if (talk.subtitle) body.append(el('p', 'talk-card__subtitle', talk.subtitle));

  const meta = el('p', 'talk-card__meta');
  if (talk.speaker) meta.append(talk.speaker);
  const date = formatDate(talk.updated);
  if (date) {
    if (talk.speaker) meta.append(' · ');
    const time = el('time', null, date);
    time.dateTime = talk.updated;
    meta.append('Updated ', time);
  }
  if (meta.childNodes.length) body.append(meta);

  article.append(body);
  item.append(article);
  return item;
}

async function main() {
  try {
    const response = await fetch('./talks.json', { cache: 'no-cache', credentials: 'same-origin' });
    if (!response.ok) throw new Error(`talks.json returned HTTP ${response.status}.`);
    const data = await response.json();
    const talks = (Array.isArray(data) ? data : (data && data.talks) || []).filter(
      (talk) => talk && typeof talk.slug === 'string' && SLUG_RE.test(talk.slug) && typeof talk.title === 'string',
    );
    talks.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
    list.replaceChildren(...talks.map(card));
    status.textContent = talks.length ? '' : 'No talks are published yet.';
  } catch (err) {
    status.textContent = 'The list of talks could not be loaded.';
    console.error(err);
  }
}

main();
