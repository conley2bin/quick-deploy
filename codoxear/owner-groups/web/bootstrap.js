/* Served same-origin after core.js and panel.js. No reads through upstream GET caches. */
(() => {
  'use strict';
  const script = document.currentScript;
  if (!script?.src) return;
  const namespace = new URL('.', script.src);
  if (namespace.origin !== location.origin) return;
  const readSnapshot = async () => {
    // Upstream service worker intercepts/cache-falls-back every GET, ignoring no-store.
    // Its non-GET pass-through makes this empty, read-only POST freshness-preserving.
    const response = await fetch(new URL('snapshot', namespace), {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
    });
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Read unavailable');
    return response.json();
  };
  let mounted = false;
  const observer = new MutationObserver(mount);
  function begin() {
    mounted = true; observer.disconnect();
    CodoxearOwnerPanel.start({ readSnapshot });
  }
  function mount() {
    if (!mounted && document.querySelector('.sidebar > #sessions.sessions')) begin();
  }
  observer.observe(document.documentElement, { childList: true, subtree: true }); mount();
  // A logged-in but incompatible future shell gets a visible compatibility warning.
  window.addEventListener('load', async () => {
    if (mounted || document.getElementById('loginForm')) return;
    try { await readSnapshot(); if (!mounted) begin(); } catch (_) { /* Login/API unavailable: native app remains. */ }
  }, { once: true });
  window.addEventListener('pagehide', () => observer.disconnect(), { once: true });
})();
