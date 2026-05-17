# Page Structure

## Overview
The application has 25 pages organized into logical sections, each demonstrating specific Cards region capabilities.

## Page Categories

### Infrastructure
| Page | Name | Purpose |
|---|---|---|
| 0 | Global Page - Desktop | Shared dynamic actions (navigation title tooltips) |

### Landing
| Page | Name | Regions | Purpose |
|---|---|---|---|
| 1 | Home | About This App (staticContent), App Navigation (list), Sample Cards (staticContent hero) | Entry point, app description, section navigation |

### Basics (pages 3, 12, 13, 18, 20)
| Page | Name | Key Feature |
|---|---|---|
| 3 | Basic Cards | Three card styles (A, B, C) with RDS tabs |
| 12 | Faceted Search with Cards | Cards + Faceted Search with 5 facets (dept, job, mgr, salary, search) |
| 13 | Star Icons | CSS-styled card icon initials |
| 18 | Color Coded Cards | Conditional CSS coloring based on column values |
| 20 | Basics | Index/landing page for Basics section |

### Images and Media (pages 2, 4, 5, 6, 7, 8, 10, 21)
| Page | Name | Media Source | REST? |
|---|---|---|---|
| 2 | BLOB Column | `PROFILE_IMAGE` BLOB | No |
| 4 | Image URL | External URLs | No |
| 5 | BLOB Column as URL | BLOB rendered as URL | No |
| 6 | Embedded Video | YouTube iframe | Yes |
| 7 | Background Image | TMDb posters + YouTube thumbnails | Yes |
| 8 | Application Static Files | `#APP_FILES#` | No |
| 10 | APEX Play List | YouTube playlist items | Yes |
| 21 | Images and Media | Index/landing page | — |

### Card Actions (pages 15, 16, 22)
| Page | Name | Pattern |
|---|---|---|
| 15 | Full Card Action | Entire card clickable |
| 16 | Conditional Actions | Multiple actions per card, conditionally shown |
| 22 | Card Actions | Index/landing page |

### Advanced (pages 9, 11, 17, 19)
| Page | Name | Feature |
|---|---|---|
| 9 | Template Directives | Dynamic template rendering |
| 11 | Gauge Meter Chart Cards | Oracle JET Data Visualizations in cards |
| 17 | No Data Found Message | Custom empty state messaging |
| 19 | Advanced | Index/landing page |

### Support
| Page | Name | Purpose |
|---|---|---|
| 14 | Employee Detail | Detail page linked from card actions |
| 23 | Help | Help page |
| 99999 | Login | Authentication |

## Page Patterns

### Standard Content Page Structure
```
page N (
    name: ...
    appearance { pageTemplate: @/standard }
    security { authorizationScheme: mustNotBePublicUser }

    region about-this-page    → staticContent, collapsible, collapsed by default
    region breadcrumb         → breadcrumb region
    region [cards]            → cards region (main content)
)
```

### Index Page Structure
```
page N (
    name: [Section Name]
    region about-this-page
    region navigation-list  → list region using section-specific list
)
```

### Page Templates Used
- `@/standard` — default content pages
- `@/left-side-column` — faceted search page (page 12)
- `@/bare` — minimal template (from Universal Theme)

### Common Region Patterns
- **About This Page**: collapsible, collapsed by default, icon, `renderComponents: belowContent`
- **Breadcrumb**: `@/title-bar` template, `@breadcrumb` component
- **Region Display Selector**: used on page 3 for style comparison tabs
