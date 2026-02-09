# HANDOFF SUMMARY (generated)
# Project root (active): D:\Workbench code
#
# Current focus: UI cleanup, Tailwind integration, PPDE/PDE topbar layout,
# chat UI, browse lists, and active project sync.
#
# Tailwind pipeline
# - Added Tailwind build pipeline with:
#   - package.json, tailwind.config.js, postcss.config.js
#   - static/src/tailwind.css -> compiled to static/css/app.css
#   - templates/base.html loads static/css/app.css
# - npm install/build run in D:\Workbench code and successful.
# - Tailwind rebuild command: npm run build:css
#
# Visual refresh (Tailwind base)
# - static/src/tailwind.css: base + component styles for cards, buttons, topbar.
# - static/app.css: manual overrides as needed.
#
# Sidebar + dashboard
# - Dashboard header buttons removed; New chat/project moved to sidebar.
# - Sidebar +Chat/+Project styled; now blue (blue-400).
# - Recent projects tiles adjusted for overflow; status badges used.
# - Removed "View all projects/chats" on dashboard; removed Settings section at bottom.
#
# Topbar (main)
# - Project tile shows Sandbox/Controlled text (no background) + project name (no "Mode" label).
# - Controlled text color: text-success (#198754), Sandbox text color: text-warning (#ffc107).
# - Project tile padding trimmed with .rw-status-project in static/app.css.
#
# Chat detail page (templates/accounts/chat_detail.html)
# - Removed rename UI.
# - Message textarea inline with right button stack; full width.
# - "Send" button inline with "Select file" row, same size.
# - "Select file" now a <button> wired to hidden file input (script included).
# - Both buttons forced identical size via .rw-btn-same in static/app.css.
# - Send = green (btn-success), Select file = blue (forced via .rw-btn-file override).
#
# Chat browse (accounts/views.py, templates/accounts/chat_browse.html)
# - Removed New chat/Back buttons.
# - Reduced top padding.
# - Added per-project row coloring:
#   - accounts/views.py assigns c.row_color from a 10-color palette (cycled by project_id).
#   - templates/accounts/chat_browse.html applies row_color to each <td>.
# - Fixed ValueError by adding _safe_int and guarding int conversions.
# - Fixed NameError by importing Case/When/Value/IntegerField.
#
# Project browse (templates/accounts/config_project_list.html)
# - Removed New project button.
# - Removed "Active L4" column.
# - Colored rows by project mode:
#   - Controlled rows: rgba(25,135,84,0.12)
#   - Sandbox rows: rgba(255,193,7,0.16)
#   (applied via .rw-row-controlled > td, .rw-row-sandbox > td in static/app.css)
# - Reduced top padding.
#
# Active project sync with chat
# - accounts/views.py -> chat_detail sets rw_active_project_id to chat.project_id.
# - accounts/context_processors.py -> always syncs project to chat’s project when a chat is viewed.
#
# PPDE topbar (templates/partials/ppde_topbar.html)
# - Status badge on left block.
# - Buttons wrap on narrow widths.
# - Standardized button sizes (rw-topbar-btn, rw-btn-xs; font/padding overrides in static/app.css).
# - Color scheme:
#   - Help: green
#   - Add stage: blue (inline or via rw-btn-file)
#   - Verify/Propose: blue outline (inline)
#   - Save & exit: gray
#   - Commit: green
#
# PDE topbar (templates/partials/pde_topbar.html)
# - Badge removed from center; reinserted on left.
# - Buttons responsive; color scheme similar to PPDE.
# - Verify badge appears; if missing, restart server or re-check template.
#
# Known issue to verify
# - PDE topbar badge still sometimes missing; verify in template and restart server.
#
# Key files changed
# - templates/base.html
# - static/src/tailwind.css
# - static/css/app.css (compiled)
# - static/app.css (manual overrides)
# - templates/partials/sidebar.html
# - templates/accounts/dashboard.html
# - templates/accounts/chat_detail.html
# - templates/accounts/chat_browse.html
# - templates/accounts/config_project_list.html
# - templates/partials/topbar.html
# - templates/partials/ppde_topbar.html
# - templates/partials/pde_topbar.html
# - accounts/views.py
# - accounts/context_processors.py
