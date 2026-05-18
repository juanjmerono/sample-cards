## MODIFIED Requirements

### Requirement: Desktop Navigation Menu includes Kanban Board entry
The desktop navigation menu SHALL include a "Kanban Board" entry under the Advanced section, linking to page 24.

#### Scenario: Kanban Board appears in navigation menu
- **WHEN** a user opens the desktop navigation menu
- **THEN** a "Kanban Board" entry is visible under the "Advanced" parent entry with a `fa-columns` icon

#### Scenario: Kanban Board entry highlights when on page 24
- **WHEN** the user is on page 24
- **THEN** the "Kanban Board" navigation entry is highlighted as the current page

### Requirement: Advanced list includes Kanban Board entry
The `advanced` shared component list SHALL include a "Kanban Board" entry linking to page 24.

#### Scenario: Kanban Board appears in Advanced list
- **WHEN** the Advanced list is rendered (e.g., on the home page)
- **THEN** a "Kanban Board" entry is visible with a `fa-columns` icon and a description of the drag & drop Kanban feature
