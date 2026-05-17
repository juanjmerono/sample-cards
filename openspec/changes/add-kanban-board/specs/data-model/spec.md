## ADDED Requirements

### Requirement: EBA_DEMO_CARD_TASK table
The system SHALL include a new table `EBA_DEMO_CARD_TASK` to store Kanban task data.

#### Scenario: Table exists with required columns
- **WHEN** the supporting objects are installed
- **THEN** the table `EBA_DEMO_CARD_TASK` exists with columns: ID (NUMBER PK), TITLE (VARCHAR2(200)), DESCRIPTION (VARCHAR2(4000)), STATUS (VARCHAR2(20)), PRIORITY (VARCHAR2(20)), ASSIGNEE (VARCHAR2(100)), SORT_ORDER (NUMBER), CREATED (DATE)

#### Scenario: Primary key is auto-generated
- **WHEN** a new row is inserted without an ID
- **THEN** the ID is auto-generated via a sequence or identity column

#### Scenario: Table is dropped on deinstall
- **WHEN** the deinstall script runs
- **THEN** the `EBA_DEMO_CARD_TASK` table is dropped

### Requirement: Seed data for Kanban tasks
The system SHALL seed approximately 15 sample tasks distributed across all three statuses (TODO, IN_PROGRESS, DONE) with varied priorities and assignees.

#### Scenario: Seed data includes tasks in all statuses
- **WHEN** the install scripts run
- **THEN** at least 5 tasks exist in each status: TODO, IN_PROGRESS, DONE

#### Scenario: Seed data includes varied priorities
- **WHEN** the install scripts run
- **THEN** tasks include all four priority levels: LOW, MEDIUM, HIGH, URGENT

#### Scenario: Seed data includes realistic task content
- **WHEN** the install scripts run
- **THEN** tasks have realistic titles and descriptions related to APEX development (e.g., "Design login page", "Test REST API integration")

### Requirement: Install script for task table
The system SHALL include an install script that creates the `EBA_DEMO_CARD_TASK` table and inserts seed data.

#### Scenario: Install script runs in correct sequence
- **WHEN** supporting objects are installed
- **THEN** the task table install script runs after the emp-dept-tables script (sequence > 40) and before the package script (sequence < 200)
