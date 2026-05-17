// Initial-paint theme bootstrap. Runs BEFORE Tailwind/Alpine so the
// `dark` class is set on <html> immediately and we don't flash the wrong
// theme. Mirrors _applyTheme() in app.js. Reads the same localStorage
// key (claudewatch.theme); defaults to "auto" (follows OS).
//
// Lives in its own file (#127) rather than as an inline <script> so a
// future strict Content-Security-Policy can use `script-src 'self'`
// without needing to whitelist `'unsafe-inline'` or maintain a per-deploy
// SHA-256 hash. The <script> tag in index.html must stay synchronous
// (no defer/async) so this runs before the body paints.
(function () {
  try {
    var t = localStorage.getItem("claudewatch.theme") || "auto";
    if (t !== "light" && t !== "dark" && t !== "auto") t = "auto";
    var wantDark = t === "dark"
      || (t === "auto" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var html = document.documentElement;
    if (wantDark) { html.classList.add("dark"); html.classList.remove("light"); }
    else { html.classList.add("light"); html.classList.remove("dark"); }
  } catch (e) { /* keep default */ }
})();
