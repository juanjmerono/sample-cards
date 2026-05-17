# Cards Region

## Overview
The Cards region type is the core feature of this application. It renders data in bite-sized card blocks with configurable layout, appearance, media, and actions.

## Capabilities

### Card Styles
- **Style A** (`t-CardsRegion--styleA`) — Flat card design
- **Style B** (`t-CardsRegion--styleB`) — Raised card with shadow
- **Style C** (`t-CardsRegion--styleC`) — Compact card design
- Demonstrated on page 3 (Basic Cards) with Region Display Selector (RDS) for tabbed switching

### Media Sources
Cards can display media from multiple sources:

| Source Type | Example Page | Description |
|---|---|---|
| BLOB Column | Page 2 | Images stored in `EBA_DEMO_CARD_EMP.PROFILE_IMAGE` |
| BLOB as URL | Page 5 | BLOB column rendered as a downloadable URL |
| External URL | Page 4 | Image URLs from external sources |
| REST Data Source | Pages 6, 7, 10 | YouTube thumbnails, movie posters via API |
| Static Files | Page 8 | Application static files (e.g., `#APP_FILES#icons/`) |
| Embedded Video | Page 6 | YouTube `<iframe>` via HTML media formatting |
| JET Data Viz | Page 11 | Oracle JET gauge meter charts inside cards |

### Card Content Configuration
- **Title**: sourced from a column (e.g., `ENAME`)
- **Subtitle**: sourced from a column (e.g., `JOB`)
- **Body**: HTML expressions with column substitution
- **Icon & Badge**:
  - `iconSource`: `initials`, `imageBlobColumn`, or icon class
  - `iconColumn`: column for initials
  - `iconPosition`: `top` or other positions
  - `badgeColumn`: column for badge text
  - `iconDescription`: accessible description

### Card Actions
- **Full Card Action** (page 15): entire card is clickable, links to a target
- **Conditional Actions** (page 16): multiple action elements per card, shown/hidden conditionally
- **Card Actions Index** (page 22): overview of action patterns

### Pagination
- Cards regions support pagination with `showTotalCount: true`

### No Data Found
- Custom "No Data Found" messages configurable per Cards region (page 17)

### Template Directives
- Dynamic content rendering using APEX template directives (page 9)

### Color Coding
- Cards can be color-coded based on column values using CSS classes (page 18)

### Integration with Faceted Search
- Cards regions can be the `filteredRegion` for a Faceted Search region (page 12)
- Facets include: checkbox groups, range filters, and free-text search
- Facets support collapsible sections and max displayed entries

## Data Sources for Cards
- **Local Database**: `tableName` or `sqlQuery` (SQL with subqueries for lookups)
- **REST Data Source**: external APIs (YouTube, TMDb)

## Template
- All Cards regions use `@/cards-container` as the region template
- Style applied via `templateOptions` (e.g., `t-CardsRegion--styleA`)
