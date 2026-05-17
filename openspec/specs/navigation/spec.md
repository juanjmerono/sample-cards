# Navigation

## Overview
The application uses a hierarchical navigation system built on APEX lists and templates.

## Components

### Desktop Navigation Menu
- **List**: `desktop-navigation-menu`
- **Structure**: 6 top-level entries with nested children
- **Icons**: Font Awesome icon classes for each entry
- **Current Page Detection**: `isCurrent` type `pages` with page ID arrays
- **Hidden Entry**: "REST Data Sources" entry with condition `type: never`

### Home Page Navigation
- **List**: `home` — displayed on page 1 as a media list
- 4 entries: Basics, Images and Media, Card Actions, Advanced
- Each entry has `userDefinedAttributes[1]` with description text

### Section-Specific Lists
| List Name | Purpose | Pages |
|---|---|---|
| `basics` | Basics section index | 3, 12, 13, 18, 20 |
| `images-and-media` | Media section index | 2, 4, 5, 6, 7, 8, 10 |
| `card-actions` | Actions section index | 15, 16, 22 |
| `advanced` | Advanced section index | 9, 11, 17, 19 |

### Navigation Bar
- **List**: `desktop-navigation-bar`
- Entries: Help, App User (with username), Sign Out
- Sign Out is a child of App User entry (parent-child hierarchy)
- Separator entry between App User and Sign Out

### Breadcrumbs
- Shared breadcrumb component (`@breadcrumb`)
- Used on content pages (e.g., page 3, page 12)
- Template: `@/title-bar`

### Side Navigation Menu
- Template: `@/side-navigation-menu`
- Template options include: `js-defaultCollapsed`, `js-navCollapsed--hidden`, `t-TreeNav--styleA`
- Collapsible tree navigation in the left sidebar

### Global Page Behavior
- Page 0 (Global Page) has a dynamic action "Title the navigation"
- Fires on `ready` event
- Executes JavaScript to add `title` attributes to tree navigation items for tooltip hover

## Page Navigation Patterns
- Index pages (20, 21, 22, 19) serve as section landing pages
- Detail pages (e.g., page 14 — Employee Detail) linked from card actions
- Login page (99999) separate from content pages
