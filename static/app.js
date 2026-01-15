/* ============================================================
   AiScape Reasoning Workbench — app.js
   Purpose:
   Placeholder client script so base.html can include /static/app.js
   without 404 noise. Add small UI behaviours here over time.
   ============================================================ */
document.addEventListener("DOMContentLoaded", () => {
    const btn = document.querySelector(".rw-sidebar-toggle");
    const sidebar = document.querySelector(".rw-sidebar");

    if (btn && sidebar) {
        btn.addEventListener("click", () => {
            sidebar.classList.toggle("is-open");
        });
    }
});
