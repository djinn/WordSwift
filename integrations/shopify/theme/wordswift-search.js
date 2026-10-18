/**
 * WordSwift storefront search widget for Shopify themes.
 *
 * Talks to webhook_server.py's POST /search proxy (NOT to WordSwift directly)
 * so that:
 *   - deleted products are filtered out via tombstones
 *   - the WordSwift port stays private
 *   - same-origin requests work under Shopify's app proxy (no CORS)
 *
 * Install:
 *   1. Copy to your theme's assets/ directory
 *   2. Add to theme.liquid before </body>:
 *        <script src="{{ 'wordswift-search.js' | asset_url }}" defer></script>
 *   3. Add a container with the data attributes below to your search template
 *
 * Markup:
 *   <div data-wordswift-search
 *        data-endpoint="/apps/search"
 *        data-min-chars="2"
 *        data-debounce="250"
 *        data-limit="8">
 *     <input type="search" data-ws-input placeholder="Search products…"
 *            autocomplete="off">
 *     <div data-ws-results></div>
 *   </div>
 */
(function () {
  'use strict';

  var DEFAULTS = {
    endpoint: '/apps/search',  // Shopify app proxy path
    minChars: 2,
    debounce: 250,
    limit: 8,
  };

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** Shopify money strings arrive pre-formatted; fall back gracefully. */
  function formatPrice(value) {
    if (!value && value !== 0) return '';
    var n = parseFloat(value);
    if (isNaN(n)) return escapeHtml(value);
    return '$' + n.toFixed(2);
  }

  function renderHit(hit) {
    var url = hit.handle ? '/products/' + hit.handle : '#';
    var img = hit.image
      ? '<img src="' + escapeHtml(hit.image) + '" alt="" loading="lazy" width="48" height="48">'
      : '';
    var price = hit.price ? '<span class="ws-price">' + formatPrice(hit.price) + '</span>' : '';
    var type = hit.product_type
      ? '<span class="ws-type">' + escapeHtml(hit.product_type) + '</span>'
      : '';

    return (
      '<a class="ws-hit" href="' + escapeHtml(url) + '">' +
        '<span class="ws-thumb">' + img + '</span>' +
        '<span class="ws-body">' +
          '<span class="ws-title">' + escapeHtml(hit.title || '') + '</span>' +
          type + price +
        '</span>' +
      '</a>'
    );
  }

  function init(root) {
    var cfg = {};
    Object.keys(DEFAULTS).forEach(function (k) {
      var attr = root.getAttribute('data-' + k.replace(/[A-Z]/g, function (m) {
        return '-' + m.toLowerCase();
      }));
      cfg[k] = attr !== null ? attr : DEFAULTS[k];
    });
    cfg.minChars = parseInt(cfg.minChars, 10) || DEFAULTS.minChars;
    cfg.debounce = parseInt(cfg.debounce, 10) || DEFAULTS.debounce;
    cfg.limit = parseInt(cfg.limit, 10) || DEFAULTS.limit;

    var input = root.querySelector('[data-ws-input]');
    var results = root.querySelector('[data-ws-results]');
    if (!input || !results) return;

    var controller = null;

    function setStatus(msg) {
      results.innerHTML = '<div class="ws-status">' + escapeHtml(msg) + '</div>';
      results.hidden = false;
    }

    function search(query) {
      // Abort any in-flight request so results can't arrive out of order.
      if (controller) controller.abort();
      controller = typeof AbortController !== 'undefined' ? new AbortController() : null;

      if (query.length < cfg.minChars) {
        results.hidden = true;
        results.innerHTML = '';
        return;
      }

      setStatus('Searching…');

      fetch(cfg.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q: query, limit: cfg.limit, hybrid: true }),
        signal: controller ? controller.signal : undefined,
        credentials: 'same-origin',
      })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function (data) {
          var hits = (data && data.hits) || [];
          if (!hits.length) {
            setStatus('No products found for “' + query + '”.');
            return;
          }
          results.innerHTML = hits.map(renderHit).join('');
          results.hidden = false;
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;  // superseded request
          console.error('[wordswift] search failed:', err);
          setStatus('Search is unavailable right now.');
        });
    }

    var onInput = debounce(function () {
      search(input.value.trim());
    }, cfg.debounce);

    input.addEventListener('input', onInput);
    input.addEventListener('focus', function () {
      if (input.value.trim().length >= cfg.minChars) search(input.value.trim());
    });

    // Close on outside click and Escape.
    document.addEventListener('click', function (e) {
      if (!root.contains(e.target)) results.hidden = true;
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        results.hidden = true;
        input.blur();
      }
      // Submit to the full search page on Enter.
      if (e.key === 'Enter') {
        e.preventDefault();
        window.location.href = '/search?q=' + encodeURIComponent(input.value.trim());
      }
    });
  }

  function boot() {
    var roots = document.querySelectorAll('[data-wordswift-search]');
    Array.prototype.forEach.call(roots, init);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Expose for Shopify's section-rendering (theme editor) re-init.
  document.addEventListener('shopify:section:load', boot);
})();
