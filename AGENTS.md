# AGENTS.md — Sample Cards (Oracle APEX + APEXLang)

## Repo Identity
- Oracle APEX sample app showcasing the **Cards** region type
- Uses **APEXLang** (declarative DSL) — all definitions are `.apx` files, NOT traditional APEX SQL exports
- App ID: **100** | APEXLang version: **26.1.0+3102**
- Remote: `https://github.com/juanjmerono/sample-cards.git`

## Directory Structure
```
application.apx              ← app-level config (name, theme, auth, substitutions)
page-groups.apx              ← page group definitions
pages/                       ← 25 pages: p00000 (global) through p00023, p99999 (login)
shared-components/           ← auth, authz, lists, LOVs, static files, themes, REST sources
supporting-objects/          ← DB install scripts (tables, package, seed data)
workspace-components/        ← app groups, credentials, REST data source servers
deployments/default.json     ← deployment config (app id: 100)
.apex/apexlang.json          ← APEXLang version config
```

## Key Commands (APEXLang)
- Load the **apexlang** skill before any generation/editing work
- Validate: `node tools/apexctl.mjs apexlang compiler-truth audit --app-path <path> --verify-component-attributes`
- Check code only: `node tools/apexctl.mjs runtime preflight`
- Import requires explicit user confirmation — never infer import from prompt wording
- For Live DB operations: require `db_connection_name` AND APEX workspace name from user

## APEXLang Conventions
- One top-level declaration per block; separate siblings with a blank line
- File naming: `pNNNNN-kebab-case.apx` for pages (e.g., `p00001-home.apx`)
- References use `@` prefix (e.g., `@/standard`, `@universal-theme`)
- Template options: `#DEFAULT#` for defaults
- Always pass compiler-truth audit before import eligibility

## Supporting Objects (Database)
- Requires privileges: `createProcedure`, `createTable`, `createTrigger`, `createView`
- Tables: `EBA_DEMO_CARD_DEPT`, `EBA_DEMO_CARD_EMP`, `EBA_DEMO_CARD_VEHICLE_SPEED`, `EBA_DEMO_CARD_RANDOM_IMAGE`
- Package: `EBA_DEMO_CARD_PKG`
- Install scripts run by sequence number (20–200); deinstall drops all tables + package

## External API Dependencies
Pages 4, 5, 6, 7 use REST Data Sources requiring manual credential setup:
- **Google YouTube API** — credential name: `key`
- **The Movie Database (TMDb) API** — credential name: `api_key`
- Substitution strings `GOOGLE_API` and `MOVIEDB_API` hold doc URLs

## Auth & Security
- Authentication: Application Express Accounts
- Public user: `APEX_PUBLIC_USER`
- Deep linking enabled; session checksum salt configured
- Pages use `mustNotBePublicUser` authorization scheme
