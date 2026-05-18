## Why

The Sample Cards application demonstrates every major Cards region capability except one: **Kanban-style task management with drag & drop**. Adding a Kanban board showcases how Cards regions can be combined with JavaScript libraries (SortableJS) and AJAX Callbacks to create interactive, modern UI patterns — a highly requested real-world use case that's missing from the current demo set.

## What Changes

- **New page 24** — Kanban Board with three columns (To Do, In Progress, Done), each rendered as a Cards region
- **New database table** `EBA_DEMO_CARD_TASK` — stores Kanban tasks with title, description, status, priority, assignee, and order
- **Drag & drop functionality** — SortableJS library enables dragging cards between columns; status updates via AJAX Callback
- **New install script** — creates the table and seeds ~15 sample tasks across all three statuses
- **Navigation updates** — add Kanban Board entry to the Desktop Navigation Menu under Advanced, and to the Advanced list
- **New REST Data Source Server** not needed — this is purely local database driven

## Capabilities

### New Capabilities
- `kanban-board`: The Kanban board page, drag & drop interaction, AJAX Callback for status updates, SortableJS integration, and the visual three-column layout using Cards regions

### Modified Capabilities
- `data-model`: New table `EBA_DEMO_CARD_TASK` with supporting install script and seed data
- `navigation`: New navigation entries for the Kanban Board page in the desktop menu and Advanced list

## Impact

- **New page**: `pages/p00024-kanban-board.apx`
- **New supporting object**: install script for `EBA_DEMO_CARD_TASK` table + seed data
- **Modified**: `shared-components/lists.apx` — add Kanban entry to `desktop-navigation-menu` and `advanced` lists
- **Modified**: `shared-components/rest-data-sources/` — none needed (local data only)
- **External dependency**: SortableJS loaded from CDN (no workspace-level dependency)
- **No breaking changes** — purely additive
