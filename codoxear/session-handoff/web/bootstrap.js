/* Separate namespace script: capture currentScript before asynchronous native mount. */
(function (win) {
  'use strict';
  const src = win.document.currentScript?.src;
  if (!src) return;
  let instance, loginObserver, stopped = false;
  function start() {
    if (!stopped) instance = win.CodoxearHandoffPanel.mount(win, src, awaitLogin);
  }
  function awaitLogin() {
    // Native logout/login replaces #root without navigation. The old component has
    // disposed ALL its timers/requests/observers/handlers and cleared its reference.
    // This fresh root-only sentinel contains no operation data and performs no I/O.
    const root = win.document.getElementById('root');
    let sawLogin = Boolean(win.document.getElementById('loginForm'));
    loginObserver = new MutationObserver(() => {
      if (win.document.getElementById('loginForm')) sawLogin = true;
      else if (sawLogin && root?.querySelector(':scope > .app')) {
        loginObserver.disconnect(); loginObserver = null; start();
      }
    });
    if (root) loginObserver.observe(root, { childList: true });
  }
  win.addEventListener('pagehide', () => {
    stopped = true; loginObserver?.disconnect(); instance?.dispose();
  }, { once: true });
  start();
})(window);
