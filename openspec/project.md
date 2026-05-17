# Sample Cards — Project Baseline

## Identity
- **Name**: Sample Cards
- **Type**: Oracle APEX sample application
- **App ID**: 100
- **APEXLang Version**: 26.1.0+3102
- **Remote**: https://github.com/juanjmerono/sample-cards.git

## Purpose
Demo application showcasing the **Cards** region type in Oracle APEX. Demonstrates every major Cards capability: styles, media sources, actions, faceted search integration, REST Data Sources, template directives, and JET Data Visualizations.

## Tech Stack
```
┌──────────────────────────────────────────────┐
│              APPLICATION STACK               │
├──────────────────────────────────────────────┤
│  APEXLang 26.1.0  (declarative .apx files)   │
│       │                                      │
│       ▼                                      │
│  Oracle APEX (report region = Cards)         │
│       │                                      │
│       ▼                                      │
│  Oracle Database (tables, package, triggers) │
│       │                                      │
│       ▼                                      │
│  REST APIs (YouTube, TMDb)                   │
└──────────────────────────────────────────────┘
```

## Architecture Overview

### Page Map
```
┌─────────────────────────────────────────────────────────────────┐
│                        PAGE INVENTORY                           │
├──────────┬──────────────────────────────┬───────────────────────┤
│ Page     │ Name                         │ Category              │
├──────────┼──────────────────────────────┼───────────────────────┤
│ 0        │ Global Page - Desktop        │ Infrastructure        │
│ 1        │ Home                         │ Landing               │
│ 2        │ BLOB Column                  │ Media                 │
│ 3        │ Basic Cards (Styles A/B/C)   │ Basics                │
│ 4        │ Image URL                    │ Media                 │
│ 5        │ BLOB Column as URL           │ Media                 │
│ 6        │ Embedded Video               │ Media / REST          │
│ 7        │ Background Image             │ Media / REST          │
│ 8        │ Application Static Files     │ Media                 │
│ 9        │ Template Directives          │ Advanced              │
│ 10       │ APEX Play List               │ Media / REST          │
│ 11       │ Gauge Meter Chart Cards      │ Charts / JET          │
│ 12       │ Faceted Search with Cards    │ Basics / Search       │
│ 13       │ Star Icons                   │ Basics                │
│ 14       │ Employee Detail              │ Detail / Navigation   │
│ 15       │ Full Card Action             │ Card Actions          │
│ 16       │ Conditional Actions          │ Card Actions          │
│ 17       │ No Data Found Message        │ Advanced              │
│ 18       │ Color Coded Cards            │ Basics                │
│ 19       │ Advanced                     │ Advanced (index)      │
│ 20       │ Basics                       │ Basics (index)        │
│ 21       │ Images and Media             │ Media (index)         │
│ 22       │ Card Actions                 │ Card Actions (index)  │
│ 23       │ Help                         │ Support               │
│ 99999    │ Login                        │ Auth                  │
└──────────┴──────────────────────────────┴───────────────────────┘
```

### Navigation Hierarchy
```
Desktop Navigation Menu
├── Home
├── Basics
│   ├── Color Coded Cards (18)
│   ├── Faceted Search (12)
│   ├── Star Icons (13)
│   └── Styles (3)
├── Images and Media
│   ├── Application Static Files (8)
│   ├── Background Image (7)
│   ├── BLOB Column (2)
│   ├── BLOB Column as URL (5)
│   ├── Embedded Video (6)
│   ├── Image URL (4)
│   └── Video Images with Durations (10)
├── Card Actions
│   ├── Conditional Actions (16)
│   └── Full Card Action (15)
├── Advanced
│   ├── Charts (11)
│   ├── No Data Found (17)
│   └── Template Directives (9)
└── [hidden] REST Data Sources (6) — type: never
```

### Data Model
```
┌──────────────────────┐     ┌──────────────────────┐
│ EBA_DEMO_CARD_DEPT   │     │ EBA_DEMO_CARD_EMP    │
├──────────────────────┤     ├──────────────────────┤
│ DEPTNO (PK)          │◄────│ DEPTNO (FK)          │
│ DNAME                │     │ EMPNO (PK)           │
│                      │     │ ENAME                │
│                      │     │ JOB                  │
│                      │     │ MGR (FK→EMPNO)       │
│                      │     │ HIREDATE             │
│                      │     │ SAL                  │
│                      │     │ COMM                 │
│                      │     │ PROFILE_IMAGE (BLOB) │
│                      │     │ MIMETYPE             │
│                      │     │ FILENAME             │
│                      │     │ IMAGE_LAST_UPDATE    │
└──────────────────────┘     └──────────────────────┘

┌──────────────────────────────┐  ┌──────────────────────────────┐
│ EBA_DEMO_CARD_VEHICLE_SPEED  │  │ EBA_DEMO_CARD_RANDOM_IMAGE   │
├──────────────────────────────┤  ├──────────────────────────────┤
│ (JET gauge chart data)       │  │ (random image source)        │
└──────────────────────────────┘  └──────────────────────────────┘

Package: EBA_DEMO_CARD_PKG
```

### REST Data Sources
```
┌──────────────────────────┐     ┌──────────────────────────┐
│ APEX_Youtube_Videos      │     │ Movie_Database           │
│ googleapis.com/youtube/v3│     │ api.themoviedb.org/3     │
├──────────────────────────┤     ├──────────────────────────┤
│ Credential: google_api_key│    │ Credential: api_key      │
│ Used by: pages 6, 7, 10  │     │ Used by: pages 6, 7      │
└──────────────────────────┘     └──────────────────────────┘
```

### Authentication & Security
- **Scheme**: Application Express Accounts (oracleApexAccounts)
- **Public User**: APEX_PUBLIC_USER
- **Authorization**: `mustNotBePublicUser` on all content pages
- **Deep Linking**: enabled
- **Session Checksum Salt**: configured
- **Embed**: allow same-origin only

### Theme
- **Universal Theme** with custom template options
- **Template Option Groups** defined
- **Static Files**: app-icon.css, app-icon-512.png

## Key Conventions
- File naming: `pNNNNN-kebab-case.apx`
- One top-level declaration per block, blank line between siblings
- References use `@` prefix (e.g., `@/standard`, `@universal-theme`)
- Template options: `#DEFAULT#` for defaults
- Cards regions use `@/cards-container` template with style variants

## External Dependencies
| Dependency | Purpose | Credential Name | Substitution |
|---|---|---|---|
| Google YouTube API | Video embeds, durations | `key` | `GOOGLE_API` |
| TMDb API | Movie posters, backgrounds | `api_key` | `MOVIEDB_API` |

## Directory Structure
```
sample-cards/
├── application.apx              # App-level config
├── page-groups.apx              # Page groups (Administration)
├── pages/                       # 25 page definitions
├── shared-components/           # Auth, authz, lists, LOVs, REST, themes
│   ├── authentications.apx
│   ├── authorizations.apx
│   ├── lists.apx
│   ├── lovs.apx
│   ├── breadcrumbs.apx
│   ├── shortcuts.apx
│   ├── static-files.apx
│   ├── component-settings.apx
│   ├── rest-data-sources/       # 3 REST sources
│   └── themes/universal-theme/  # Theme customization
├── workspace-components/        # Credentials, REST servers, app groups
├── supporting-objects/          # DB install scripts (18 scripts)
├── deployments/default.json     # Deployment config
├── apex-exports/                # APEX export artifacts
└── openspec/                    # OpenSpec specs & changes
```
