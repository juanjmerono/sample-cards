## ADDED Requirements

### Requirement: Kanban board page display
The system SHALL display a Kanban board page (page 24) with three columns: "To Do", "In Progress", and "Done". Each column SHALL be a Cards region sourced from `EBA_DEMO_CARD_TASK` filtered by status.

#### Scenario: Page loads with three columns
- **WHEN** a user navigates to page 24
- **THEN** the page displays three columns side by side labeled "To Do", "In Progress", and "Done"

#### Scenario: Each column shows only tasks with matching status
- **WHEN** the page renders
- **THEN** the "To Do" column shows only tasks where `STATUS = 'TODO'`, "In Progress" shows `STATUS = 'IN_PROGRESS'`, and "Done" shows `STATUS = 'DONE'`

#### Scenario: Cards within each column are ordered by sort_order
- **WHEN** the page renders
- **THEN** cards within each column are ordered by `SORT_ORDER ASC`

### Requirement: Card content display
Each card in the Kanban board SHALL display the task title, description, assignee, and a color-coded priority badge.

#### Scenario: Card shows task title and description
- **WHEN** a card is rendered
- **THEN** the card title is the task `TITLE` and the card body shows the `DESCRIPTION`

#### Scenario: Card shows assignee
- **WHEN** a card is rendered
- **THEN** the card displays the `ASSIGNEE` value as a subtitle or badge

#### Scenario: Card shows color-coded priority
- **WHEN** a card is rendered
- **THEN** the card displays a visual indicator of `PRIORITY` (LOW=green, MEDIUM=blue, HIGH=orange, URGENT=red)

### Requirement: Drag and drop between columns
The system SHALL allow users to drag a card from one column and drop it into another column. The card's status SHALL update to match the target column.

#### Scenario: Drag card from To Do to In Progress
- **WHEN** a user drags a card from the "To Do" column and drops it in the "In Progress" column
- **THEN** the card's status is updated to `IN_PROGRESS` in the database and all three Cards regions refresh

#### Scenario: Drag card from In Progress to Done
- **WHEN** a user drags a card from the "In Progress" column and drops it in the "Done" column
- **THEN** the card's status is updated to `DONE` in the database and all three Cards regions refresh

#### Scenario: Drop persists via AJAX Callback
- **WHEN** a card is dropped into a new column
- **THEN** an AJAX Callback `UPDATE_TASK_STATUS` is invoked with the task ID, new status, and new position

#### Scenario: Error on failed update
- **WHEN** the AJAX Callback fails to update the database
- **THEN** the card reverts to its original column and position, and an error message is displayed

### Requirement: SortableJS integration
The page SHALL load SortableJS from CDN and initialize drag & drop on all three Cards region containers.

#### Scenario: SortableJS loads from CDN
- **WHEN** the page loads
- **THEN** SortableJS is loaded from `https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js`

#### Scenario: Drag & drop is initialized on all columns
- **WHEN** the page is ready
- **THEN** each of the three Cards region containers has SortableJS initialized with `group: "kanban"` to allow cross-column moves

#### Scenario: Fallback when CDN is unavailable
- **WHEN** SortableJS fails to load
- **THEN** a message is displayed to the user indicating drag & drop is unavailable, but cards still render

### Requirement: Column card count
Each column header SHALL display the count of cards in that column.

#### Scenario: Count updates after drag & drop
- **WHEN** a card is moved from "To Do" to "In Progress"
- **THEN** the "To Do" column count decreases by 1 and the "In Progress" column count increases by 1

### Requirement: About this page region
The page SHALL include an "About this page" collapsible region explaining the Kanban board feature.

#### Scenario: About region is present and collapsed by default
- **WHEN** the page loads
- **THEN** an "About this page" region is visible in collapsed state with an icon and description of the Kanban board
