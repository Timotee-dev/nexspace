/* Runs before the page paints, so there is no flash of the wrong theme. Kept as a file (not inline)
   so the Content-Security-Policy can forbid inline scripts entirely. */
(function () {
  var root = document.documentElement, pref = root.dataset.themePref;
  if (root.dataset.guest === "true") {
    try { pref = localStorage.getItem("nexspace-theme") || pref; } catch (e) {}
  }
  root.dataset.themePref = pref;
  root.dataset.theme = (pref === "light" || pref === "dark") ? pref
    : (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
})();
