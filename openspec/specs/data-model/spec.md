# Data Model

## Overview
The application uses four database tables and one package, installed via supporting objects with ordered install scripts.

## Tables

### EBA_DEMO_CARD_DEPT
Department reference table (classic DEPT pattern).

| Column | Type | Description |
|---|---|---|
| DEPTNO | PK | Department number |
| DNAME | | Department name |

### EBA_DEMO_CARD_EMP
Employee table with profile images.

| Column | Type | Description |
|---|---|---|
| EMPNO | PK | Employee number |
| ENAME | | Employee name |
| JOB | | Job title |
| MGR | FK→EMPNO | Manager reference |
| HIREDATE | | Hire date |
| SAL | | Salary |
| COMM | | Commission |
| PROFILE_IMAGE | BLOB | Employee profile photo |
| MIMETYPE | | MIME type of profile image |
| FILENAME | | Original filename |
| IMAGE_LAST_UPDATE | | Last update timestamp |

### EBA_DEMO_CARD_VEHICLE_SPEED
Data for JET gauge meter chart visualization (page 11).

### EBA_DEMO_CARD_RANDOM_IMAGE
Random image source table.

## Package

### EBA_DEMO_CARD_PKG
PL/SQL package providing utility functions for the application.

## Install Scripts
Scripts run in sequence order (20 → 200):

| Seq | Script | Purpose |
|---|---|---|
| 20 | vehicle-speed | Vehicle speed table setup |
| 30 | random-image | Random image table setup |
| 40 | emp-dept-tables | Core EMP and DEPT table creation |
| 50 | load-dept-data | Seed department data |
| 60–190 | emp-{name} | Seed employee records (12 employees) |
| 200 | eba-demo-card-pkg | Package creation |

## Deinstall
Deinstall script drops all four tables and the package.

## LOVs (List of Values)
- **dept-dname**: Returns `DEPTNO`, displays `DNAME` from `EBA_DEMO_CARD_DEPT`
- **emp-mgr**: Returns `EMPNO`, displays `ENAME` from `EBA_DEMO_CARD_EMP`, sorted by `ENAME`
- **login-remember-username**: Static LOV with single entry "Remember username" (Y)
