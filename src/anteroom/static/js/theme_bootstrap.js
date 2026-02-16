// CSP-safe theme bootstrap (no inline scripts)
(() => {
  try {
    let t = localStorage.getItem('anteroom_theme') || localStorage.getItem('parlor_theme');
    if (!t) {
      t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches)
        ? 'dawn'
        : 'midnight';
    }
    document.documentElement.setAttribute('data-theme', t);
  } catch {
    // ignore
  }
})();
