(function() {
    'use strict';

    var config = [
        { regionId: 'region-todo',    status: 'TODO',        icon: 'u-color-7', label: 'To Do',         countId: 'count-todo' },
        { regionId: 'region-in-progress', status: 'IN_PROGRESS', icon: 'u-color-1', label: 'In Progress',   countId: 'count-in-progress' },
        { regionId: 'region-done',    status: 'DONE',        icon: 'u-color-3', label: 'Done',          countId: 'count-done' }
    ];

    var sortableInstances = [];
    var refreshCounter = { pending: 0, total: 0 };

    function wrapRegions() {
        if ($('#kanban-board-wrap').length) return;
        var $first = $('#' + config[0].regionId);
        if (!$first.length) return;
        var $wrap = $('<div class="kanban-board-wrap" id="kanban-board-wrap"></div>');
        $wrap.insertBefore($first);
        config.forEach(function(c) {
            var $region = $('#' + c.regionId);
            if ($region.length) {
                $wrap.append($region);
            }
        });
    }

    function injectHeaders() {
        config.forEach(function(c) {
            var $region = $('#' + c.regionId);
            if (!$region.length) return;
            if ($region.find('#' + c.countId).length) return;
            var $header = $('<div class="kanban-column-header">' +
                '<span class="kanban-column-title"><span class="t-Icon fa-circle ' + c.icon + '"></span> ' + c.label + '</span>' +
                '<span class="kanban-count" id="' + c.countId + '">(0)</span>' +
            '</div>');
            var $body = $region.find('.t-Region-body');
            if ($body.length) {
                $body.prepend($header);
            } else {
                $region.prepend($header);
            }
        });
    }

    function getCardsList($region) {
        var $cards = $region.find('.a-TMV--cards');
        if (!$cards.length) {
            $cards = $region.find('.t-Cards');
        }
        if (!$cards.length) {
            $cards = $region.find('.a-Cards');
        }
        if (!$cards.length) {
            $cards = $region.find('ul').first();
        }
        if ($cards.length && $cards[0].tagName !== 'UL') {
            var $ul = $cards.find('ul').first();
            if ($ul.length) {
                $cards = $ul;
            }
        }
        return $cards;
    }

    function getCardsItems($region) {
        var $list = getCardsList($region);
        var $items = $list.children('li');
        if (!$items.length) {
            $items = $list.find('li');
        }
        return $items;
    }

    function updateCounts() {
        config.forEach(function(c) {
            var $region = $('#' + c.regionId);
            var count = getCardsItems($region).length;
            $('#' + c.countId).text('(' + count + ')');
        });
    }

    function addTaskIdAttrs() {
        config.forEach(function(c) {
            var $region = $('#' + c.regionId);
            var $items = getCardsItems($region);
            if (!$items.length) return;
            $items.each(function() {
                var $item = $(this);
                if (!$item.attr('data-task-id')) {
                    var taskId = $item.data('id') || $item.attr('id');
                    if (taskId) {
                        $item.attr('data-task-id', taskId);
                        $item.attr('draggable', 'true');
                    }
                }
            });
        });
    }

    function initSortable() {
        sortableInstances.forEach(function(s) { s.destroy(); });
        sortableInstances = [];
        config.forEach(function(c) {
            var $region = $('#' + c.regionId);
            if (!$region.length) return;
            var $cardsContainer = getCardsList($region);
            if ($cardsContainer.length) {
                var s = new Sortable($cardsContainer[0], {
                    group: { name: 'kanban', pull: true, put: true },
                    animation: 150,
                    ghostClass: 'kanban-ghost',
                    dragClass: 'kanban-drag',
                    draggable: 'li[data-task-id]',
                    onEnd: function(evt) {
                        var $draggedItem = $(evt.item);
                        var taskId = $draggedItem.attr('data-task-id');
                        var newRegionEl = null;
                        var $to = $(evt.to);
                        var $parentRegion = $to.closest('[id^="region-"]');
                        if ($parentRegion.length) {
                            newRegionEl = $parentRegion[0];
                        }
                        if (!newRegionEl) {
                            var $parentRegion2 = $draggedItem.closest('[id^="region-"]');
                            if ($parentRegion2.length) {
                                newRegionEl = $parentRegion2[0];
                            }
                        }
                        var newRegionId = newRegionEl ? newRegionEl.id : null;
                        var newStatus = null;
                        config.forEach(function(cfg) {
                            if (newRegionId.startsWith(cfg.regionId)) newStatus = cfg.status;
                        });
                        var newPosition = evt.newIndex;

                        if (!taskId) {
                            console.error('Kanban: Could not determine task ID');
                            return;
                        }

                        if (!newStatus) {
                            console.error('Kanban: Could not determine new status. newRegionId:', newRegionId, 'evt.to:', evt.to.tagName, 'evt.to.id:', evt.to.id);
                            return;
                        }

                        if (evt.from === evt.to) {
                            return;
                        }

                        var oldRegionEl = null;
                        var $from = $(evt.from);
                        var $parentFrom = $from.closest('[id^="region-"]');
                        if ($parentFrom.length) {
                            oldRegionEl = $parentFrom[0];
                        }
                        var oldRegionId = oldRegionEl ? oldRegionEl.id : null;
                        var oldStatus = null;
                        config.forEach(function(cfg) {
                            if (oldRegionId && oldRegionId.startsWith(cfg.regionId)) oldStatus = cfg.status;
                        });

                        apex.server.process(
                            'UPDATE_TASK_STATUS',
                            {
                                x01: newStatus,
                                x02: (newPosition + 1) * 10,
                                x03: taskId
                            },
                            {
                                success: function() {
                                    var oldConfig = null;
                                    var newConfig = null;
                                    config.forEach(function(cfg) {
                                        if (oldStatus && cfg.status === oldStatus) oldConfig = cfg;
                                        if (cfg.status === newStatus) newConfig = cfg;
                                    });
                                    if (oldConfig) {
                                        var $oldRegion = $('#' + oldConfig.regionId);
                                        $('#' + oldConfig.countId).text('(' + getCardsItems($oldRegion).length + ')');
                                    }
                                    if (newConfig) {
                                        var $newRegion = $('#' + newConfig.regionId);
                                        $('#' + newConfig.countId).text('(' + getCardsItems($newRegion).length + ')');
                                    }

                                    refreshCounter.pending = 0;
                                    refreshCounter.total = 3;
                                    apex.region('region-todo').refresh();
                                    apex.region('region-in-progress').refresh();
                                    apex.region('region-done').refresh();
                                },
                                error: function(jqXHR, textStatus, errorThrown) {
                                    apex.message.showErrors([{
                                        type: 'error',
                                        location: 'page',
                                        message: 'Failed to update task status: ' + errorThrown,
                                        unsafe: false
                                    }]);
                                    var $from = $(evt.from);
                                    $from.append($draggedItem);
                                }
                            }
                        );
                    }
                });
                sortableInstances.push(s);
            }
        });
    }

    function reinitAfterRefresh() {
        refreshCounter.pending++;
        if (refreshCounter.pending >= refreshCounter.total && refreshCounter.total > 0) {
            refreshCounter.pending = 0;
            refreshCounter.total = 0;
            setTimeout(function() {
                wrapRegions();
                injectHeaders();
                addTaskIdAttrs();
                updateCounts();
                initSortable();
            }, 150);
        }
    }

    window.KanbanBoard = {
        init: function() {
            setTimeout(function() {
                wrapRegions();
                injectHeaders();
                addTaskIdAttrs();
                updateCounts();
                initSortable();
            }, 50);

            $(document).off('apexafterrefresh.kanban').on('apexafterrefresh.kanban', function(e) {
                var regionIds = ['region-todo', 'region-in-progress', 'region-done'];
                var targetId = e.target.id || '';
                for (var i = 0; i < regionIds.length; i++) {
                    if (targetId.startsWith(regionIds[i])) {
                        reinitAfterRefresh();
                        break;
                    }
                }
            });
        }
    };
})();
