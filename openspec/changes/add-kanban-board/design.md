## Context

The Sample Cards app currently demonstrates Cards region styles, media sources, actions, faceted search, JET charts, and template directives. It does **not** demonstrate interactive drag & drop. The app uses APEXLang (.apx files) for all definitions, a local Oracle database for data, and external REST APIs for media on select pages.

**Constraint**: APEX Cards regions have no native Kanban layout or drag & drop. The Cards region supports Grid, Float, and Horizontal layouts only — none provide column-grouped Kanban visualization.

## Goals / Non-Goals

**Goals:**
- Demonstrate a fully functional Kanban board using APEX Cards regions
- Enable drag & drop of cards between columns (To Do → In Progress → Done)
- Persist status changes to the database via AJAX Callback
- Use SortableJS as the drag & drop library (lightweight, well-documented, no plugin dependency)
- Follow existing APEXLang conventions (.apx files, supporting objects for DB)
- Seed ~15 sample tasks across all three statuses

**Non-Goals:**
- No reordering within a column (SortableJS supports it, but we keep it simple — only cross-column moves)
- No subtasks, attachments, or comments on tasks
- No user assignment UI (assignee is a display attribute only)
- No plugin installation — pure declarative + inline JS
- No real-time sync (single-user demo)

## Decisions

### D1: Three separate Cards regions vs. single custom HTML region

**Decision**: Three separate Cards regions, one per column, placed in a 3-column layout.

**Rationale**:
- Each region is a standard Cards region sourced by `SELECT * FROM eba_demo_card_task WHERE status = 'X' ORDER BY sort_order`
- Fully declarative, consistent with existing page patterns in the app
- Easier to maintain and understand for learners
- Each column can have its own card count badge

**Alternative considered**: Single Static Content region with custom HTML/JS rendering cards from JSON via AJAX. More flexible but less declarative and harder to follow as a demo.

### D2: SortableJS from CDN vs. jQuery UI Sortable vs. Plugin

**Decision**: SortableJS loaded from CDN (`https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js`).

**Rationale**:
- SortableJS is modern, touch-friendly, zero-dependency, and the most common choice for APEX Kanban implementations
- jQuery UI Sortable is bundled with APEX but has a dated API and less polished UX
- A plugin adds import complexity; CDN is simpler for a demo app

### D3: Drag & drop event handling

**Decision**: Use SortableJS `onEnd` event to detect column changes, then call `apex.server.process()` to an AJAX Callback named `UPDATE_TASK_STATUS` that runs PL/SQL to update the status and sort_order.

**Flow**:
```
User drags card from "To Do" to "In Progress"
  │
  ▼
SortableJS fires onEnd event
  │
  ▼
JavaScript reads: task_id (from data attribute), new_status (from target column ID), new_position (from DOM index)
  │
  ▼
apex.server.process("UPDATE_TASK_STATUS", { p_task_id, p_status, p_sort_order })
  │
  ▼
AJAX Callback runs: UPDATE eba_demo_card_task SET status = p_status, sort_order = p_sort_order WHERE id = p_task_id
  │
  ▼
On success: refresh all three Cards regions
On error: revert card to original column (sortableEvt.item.clone or evt.oldIndex)
```

### D4: Card identification for drag & drop

**Decision**: Each card's root `<li>` element gets a `data-task-id` attribute via the Cards region's Link URL pattern or a custom attribute. The AJAX Callback uses this to identify which task to update.

**Approach**: Use the Cards region's `card` settings with `primaryKeyColumn: ID` and ensure the rendered `<li>` has a predictable ID or data attribute. If APEX doesn't expose this natively, add a Dynamic Action on page load that scans the Cards region DOM and adds `data-task-id` attributes based on hidden column values.

### D5: Visual column layout

**Decision**: Use a 3-column CSS Grid layout via inline CSS on a parent Static Content region that wraps the three Cards regions. Each column has a header with status name and card count.

```
┌─────────────────────────────────────────────────────┐
│  .kanban-board (CSS Grid: 1fr 1fr 1fr)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ TO DO    │  │ IN PROG  │  │ DONE     │           │
│  │ (5)      │  │ (4)      │  │ (6)      │           │
│  ├──────────┤  ├──────────┤  ├──────────┤           │
│  │ [Card]   │  │ [Card]   │  │ [Card]   │           │
│  │ [Card]   │  │ [Card]   │  │ [Card]   │           │
│  │ [Card]   │  │ [Card]   │  │ [Card]   │           │
│  └──────────┘  └──────────┘  └──────────┘           │
└─────────────────────────────────────────────────────┘
```

### D6: Table design

**Decision**: `EBA_DEMO_CARD_TASK` with columns:

| Column | Type | Purpose |
|---|---|---|
| ID | NUMBER PK | Primary key |
| TITLE | VARCHAR2(200) | Task title (card title) |
| DESCRIPTION | VARCHAR2(4000) | Task description (card body) |
| STATUS | VARCHAR2(20) | TODO, IN_PROGRESS, DONE |
| PRIORITY | VARCHAR2(20) | LOW, MEDIUM, HIGH, URGENT |
| ASSIGNEE | VARCHAR2(100) | Assigned person name |
| SORT_ORDER | NUMBER | Display order within status |
| CREATED | DATE | Created timestamp |

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Cards region DOM structure may not expose task ID easily on `<li>` elements | Use a hidden column in the Cards SQL query and a Dynamic Action to add `data-task-id` attributes on page load |
| SortableJS CDN may be unavailable | Add a fallback: if SortableJS fails to load, show a message and disable drag & drop (cards still display) |
| Three separate regions means three refreshes on drop | Acceptable for demo; could optimize with a single refresh of parent region |
| No within-column reordering | Documented as non-goal; can be added later as an enhancement |
| SortableJS version pinned to CDN URL | If CDN URL changes, update the page's File URLs or inline script reference |
