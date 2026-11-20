/* eslint-disable no-undef, @typescript-eslint/no-unused-vars */
// 防 FOUC：首帧即应用主题与配色。独立文件而不是内联脚本，
// 这样生产环境的 CSP（script-src 'self'）不需要放宽 unsafe-inline。
(function () {
  try {
    var t = localStorage.getItem("spark-theme") || "light";
    var dark = t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
  try {
    document.documentElement.setAttribute(
      "data-palette",
      localStorage.getItem("spark-palette") || "ember"
    );
  } catch (e) {
    document.documentElement.setAttribute("data-palette", "ember");
  }
})();
