## 1. Database — Task Table & Seed Data

- [ ] 1.1 Create `supporting-objects/eba-demo-card-task.sql` — DDL for `EBA_DEMO_CARD_TASK` table with identity column, constraints, and comments
- [ ] 1.2 Create `supporting-objects/seed-task-data.sql` — INSERT ~15 sample tasks across TODO, IN_PROGRESS, DONE with varied priorities and assignees
- [ ] 1.3 Register install scripts in `supporting-objects/install-scripts.apx` with appropriate sequence numbers
- [ ] 1.4 Add `EBA_DEMO_CARD_TASK` to `supporting-objects/deinstall-script.sql` drop list

## 2. Page 24 — Kanban Board

- [ ] 2.1 Create `pages/p00024-kanban-board.apx` with page-level config (name, alias, template, security)
- [ ] 2.2 Add "About this page" static content region (collapsible, collapsed by default)
- [ ] 2.3 Add breadcrumb region
- [ ] 2.4 Add "To Do" Cards region — SQL: `SELECT * FROM eba_demo_card_task WHERE status = 'TODO' ORDER BY sort_order`
- [ ] 2.5 Add "In Progress" Cards region — SQL: `SELECT * FROM eba_demo_card_task WHERE status = 'IN_PROGRESS' ORDER BY sort_order`
- [ ] 2.6 Add "Done" Cards region — SQL: `SELECT * FROM eba_demo_card_task WHERE status = 'DONE' ORDER BY sort_order`
- [ ] 2.7 Configure card appearance: title=TITLE, subtitle=ASSIGNEE, body=DESCRIPTION, color-coded priority badge via CSS classes
- [ ] 2.8 Add CSS for 3-column Kanban layout (CSS Grid or Flexbox) via page CSS Inline or File URL

## 3. Drag & Drop — SortableJS Integration

- [ ] 3.1 Add SortableJS CDN reference to page (`https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js`)
- [ ] 3.2 Create Dynamic Action on page load: add `data-task-id` attributes to card `<li>` elements from hidden ID column
- [ ] 3.3 Create Dynamic Action on page load: initialize SortableJS on all three Cards regions with `group: "kanban"`
- [ ] 3.4 Create AJAX Callback `UPDATE_TASK_STATUS` — PL/SQL: `UPDATE eba_demo_card_task SET status = :p_status, sort_order = :p_sort_order WHERE id = :p_task_id`
- [ ] 3.5 Create Dynamic Action on SortableJS `onEnd`: call `apex.server.process("UPDATE_TASK_STATUS", ...)` with task ID, new status, new position
- [ ] 3.6 Add error handling: revert card on AJAX failure, show error notification
- [ ] 3.7 Add CDN fallback: display message if SortableJS fails to load

## 4. Navigation Updates

- [ ] 4.1 Add "Kanban Board" entry to `desktop-navigation-menu` list under Advanced parent (page 24, icon fa-columns)
- [ ] 4.2 Add "Kanban Board" entry to `advanced` list (page 24, icon fa-columns, description text)
- [ ] 4.3 Update `isCurrent` on Advanced parent entry to include page 24

## 5. Verification

- [ ] 5.1 Run APEXLang compiler-truth audit: `node tools/apexctl.mjs apexlang compiler-truth audit --app-path . --verify-component-attributes`
- [ ] 5.2 Verify page 24 renders correctly with three columns and cards
- [ ] 5.3 Verify drag & drop moves cards between columns and updates database
- [ ] 5.4 Verify navigation menu shows Kanban Board entry and highlights on page 24
