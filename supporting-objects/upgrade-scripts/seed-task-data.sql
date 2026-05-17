begin
    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Design new landing page', 'Create wireframes and mockups for the new product landing page', 'TODO', 'HIGH', 'Diana', 10
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Design new landing page');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Update API documentation', 'Document the new REST endpoints added in sprint 12', 'TODO', 'MEDIUM', 'Wei', 20
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Update API documentation');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Fix login bug', 'Users report intermittent login failures on mobile devices', 'TODO', 'URGENT', 'King', 30
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Fix login bug');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Refactor database queries', 'Optimize slow queries in the reporting module', 'IN_PROGRESS', 'HIGH', 'Blake', 10
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Refactor database queries');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Implement search feature', 'Add full-text search to the product catalog', 'IN_PROGRESS', 'MEDIUM', 'Allen', 20
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Implement search feature');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Code review pull requests', 'Review pending PRs from the team', 'IN_PROGRESS', 'LOW', 'Jones', 30
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Code review pull requests');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Setup CI/CD pipeline', 'Configure automated build and deployment', 'DONE', 'HIGH', 'Sarita', 10
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Setup CI/CD pipeline');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Database migration', 'Migrate legacy data to new schema', 'DONE', 'URGENT', 'Martin', 20
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Database migration');

    insert into eba_demo_card_task (title, description, status, priority, assignee, sort_order)
    select 'Write unit tests', 'Add test coverage for core business logic', 'DONE', 'MEDIUM', 'Susan', 30
    from dual where not exists (select 1 from eba_demo_card_task where title = 'Write unit tests');
end;
/
