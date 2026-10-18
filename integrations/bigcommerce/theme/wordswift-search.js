/**
 * WordSwift storefront search widget for BigCommerce Stencil themes.
 *
 * Talks to a same-origin proxy endpoint, NOT to WordSwift directly, so the
 * WordSwift port stays private and deleted products can be filtered.
 *
 * Install:
 *   1. Copy to your theme's assets/js/ directory
 *   2. Register it in your theme's app.js:
 *
 *        import WordSwiftSearch from './wordswift-search';
 *        export default class App {
 *          constructor() {
 *            this.plugins = [{
 *              name: 'wordSwiftSearch',
 *              constructor: WordSwiftSearch,
 *              selectors: ['[data-wordswift-search]'],
 *            }];
 *          }
 *        }
 *
 *   3. Add the markup to templates/components/search/form.html:
 *
 *        <div data-wordswift-search
 *             data-endpoint="/wordswift/search"
 *             data-min-chars="2"
 *             data-debounce="250"
 *             data-limit="8">
 *          <input type="search" data-ws-input placeholder="Search products…"
 *                 autocomplete="off">
 *          <div data-ws-results></div>
 *        </div>
 *
 * Stencil instantiates plugins per matched selector and calls onReady().
 */

export default class WordSwiftSearch {
  constructor(context, options) {
    this.context = context || {};
    this.options = Object.assign(
      {
        endpoint: '/wordswift/search',
        minChars: 2,
        debounce: 250,
        limit: 8,
      },
      options || {},
    );

    this.input = null;
    this.results = null;
    this.controller = null;
    this._timer = null;
  }

  /** Stencil calls this after construction, with the matched element as $element. */
  onReady() {
    const root = this.$element || document.querySelector('[data-wordswift-search]');
    if (!root) {
      return;
    }

    // Per-element data attributes override the plugin defaults.
    const readInt = (name, fallback) => {
      const raw = root.getAttribute(`data-${name}`);
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? n : fallback;
    };
    this.options.minChars = readInt('min-chars', this.options.minChars);
    this.options.debounce = readInt('debounce', this.options.debounce);
    this.options.limit = readInt('limit', this.options.limit);
    this.options.endpoint = root.getAttribute('data-endpoint') || this.options.endpoint;

    this.input = root.querySelector('[data-ws-input]');
    this.results = root.querySelector('[data-ws-results]');
    if (!this.input || !this.results) {
      return;
    }

    this.input.addEventListener('input', () => this._onInput());
    this.input.addEventListener('focus', () => {
      const q = this.input.value.trim();
      if (q.length >= this.options.minChars) {
        this.search(q);
      }
    });
    this.input.addEventListener('keydown', (e) => this._onKeydown(e));

    document.addEventListener('click', (e) => {
      if (!root.contains(e.target)) {
        this.hide();
      }
    });
  }

  _onInput() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this.search(this.input.value.trim());
    }, this.options.debounce);
  }

  _onKeydown(e) {
    if (e.key === 'Escape') {
      this.hide();
      this.input.blur();
      return;
    }
    if (e.key === 'Enter') {
      // Let BigCommerce's own /search.php handle the full results page.
      const q = this.input.value.trim();
      if (q) {
        e.preventDefault();
        window.location.href = `/search.php?search_query=${encodeURIComponent(q)}`;
      }
    }
  }

  hide() {
    if (this.results) {
      this.results.hidden = true;
      this.results.innerHTML = '';
    }
  }

  setStatus(msg) {
    this.results.innerHTML = `<div class="ws-status">${escapeHtml(msg)}</div>`;
    this.results.hidden = false;
  }

  /**
   * Query the proxy and render hits.
   *
   * @param {string} query
   */
  search(query) {
    if (!query || query.length < this.options.minChars) {
      this.hide();
      return;
    }

    // Abort in-flight requests so late responses can't overwrite newer ones.
    if (this.controller) {
      this.controller.abort();
    }
    this.controller = typeof AbortController !== 'undefined' ? new AbortController() : null;

    this.setStatus(this.context.wordswift_searching || 'Searching…');

    fetch(this.options.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ q: query, limit: this.options.limit, hybrid: true }),
      signal: this.controller ? this.controller.signal : undefined,
      credentials: 'same-origin',
    })
      .then((r) => {
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((data) => {
        const hits = (data && data.hits) || [];
        if (!hits.length) {
          this.setStatus(
            (this.context.wordswift_no_results || 'No products found for') +
              ` “${query}”.`,
          );
          return;
        }
        this.results.innerHTML = hits.map((hit) => this.renderHit(hit)).join('');
        this.results.hidden = false;
      })
      .catch((err) => {
        if (err && err.name === 'AbortError') {
          return; // superseded by a newer keystroke
        }
        console.error('[wordswift] search failed:', err);
        this.setStatus(
          this.context.wordswift_unavailable || 'Search is unavailable right now.',
        );
      });
  }

  /**
   * Render one product hit as a Stencil-friendly anchor.
   *
   * @param {Object} hit
   * @return {string}
   */
  renderHit(hit) {
    const url = hit.url
      ? hit.url
      : hit.custom_url && hit.custom_url.url
        ? hit.custom_url.url
        : `/products/${encodeURIComponent(hit.title || '')}`;

    const img = hit.image
      ? `<img src="${escapeHtml(hit.image)}" alt="" loading="lazy" width="48" height="48">`
      : '';

    const price =
      hit.price !== undefined && hit.price !== null && hit.price !== ''
        ? `<span class="ws-price">${formatPrice(hit.price)}</span>`
        : '';

    const sku = hit.sku ? `<span class="ws-sku">${escapeHtml(hit.sku)}</span>` : '';

    return (
      '<a class="ws-hit" href="' + escapeHtml(url) + '">' +
      '<span class="ws-thumb">' + img + '</span>' +
      '<span class="ws-body">' +
      '<span class="ws-title">' + escapeHtml(hit.title || '') + '</span>' +
      sku +
      price +
      '</span>' +
      '</a>'
    );
  }
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------
function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]),
  );
}

function formatPrice(value) {
  const n = typeof value === 'number' ? value : parseFloat(value);
  if (!Number.isFinite(n)) {
    return escapeHtml(value);
  }
  return `$${n.toFixed(2)}`;
}
