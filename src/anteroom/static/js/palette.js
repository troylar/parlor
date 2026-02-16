/* Command Palette: Cmd+K quick switcher */

const Palette = (() => {
    let items = [];
    let filteredItems = [];
    let activeIndex = 0;
    let isOpen = false;

    function init() {
        const overlay = document.getElementById('palette-overlay');
        const input = document.getElementById('palette-input');

        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                if (isOpen) {
                    close();
                } else {
                    open();
                }
            }
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });

        input.addEventListener('input', () => {
            filterItems(input.value);
        });

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                e.preventDefault();
                close();
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActive(activeIndex + 1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActive(activeIndex - 1);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                selectCurrent();
            }
        });
    }

    async function open() {
        isOpen = true;
        const overlay = document.getElementById('palette-overlay');
        const input = document.getElementById('palette-input');
        overlay.style.display = 'flex';
        input.value = '';
        input.focus();

        items = [];
        filteredItems = [];
        activeIndex = 0;

        items.push({
            type: 'action',
            label: 'New Chat',
            hint: 'Action',
            action: () => App.newConversation(),
        });

        // Theme switching commands
        Object.entries(App.THEMES).forEach(([key, theme]) => {
            items.push({
                type: 'theme',
                label: `Theme: ${theme.label}`,
                hint: 'Theme',
                action: () => App.setTheme(key),
            });
        });

        (App.state.availableModels || []).forEach(m => {
            items.push({
                type: 'model',
                label: m,
                hint: 'Model',
                action: () => App._selectModel(m),
            });
        });

        try {
            const projects = await App.api('/api/projects');
            projects.forEach(p => {
                items.push({
                    type: 'project',
                    label: p.name,
                    hint: 'Project',
                    action: async () => {
                        App.state.currentProjectId = p.id;
                        document.getElementById('project-select').value = p.id;
                        await App.loadProjects();
                        await Sidebar.refresh();
                        App.state.currentConversationId = null;
                        Chat.loadMessages([]);
                    },
                });
            });
        } catch {
            // ignore
        }

        try {
            const convs = await App.api('/api/conversations?limit=10');
            convs.forEach(c => {
                items.push({
                    type: 'conversation',
                    label: c.title,
                    hint: 'Conversation',
                    action: () => App.loadConversation(c.id),
                });
            });
        } catch {
            // ignore
        }

        renderItems(items);
    }

    function close() {
        isOpen = false;
        document.getElementById('palette-overlay').style.display = 'none';
        document.getElementById('palette-input').value = '';
        items = [];
        filteredItems = [];
        activeIndex = 0;
    }

    function filterItems(query) {
        const q = query.toLowerCase().trim();
        if (!q) {
            filteredItems = items;
            renderItems(filteredItems);
            return;
        }
        filteredItems = items.filter(item => item.label.toLowerCase().includes(q));
        renderItems(filteredItems);
    }

    function renderItems(list) {
        const results = document.getElementById('palette-results');
        results.innerHTML = '';
        activeIndex = 0;

        if (list.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'palette-empty';
            empty.textContent = 'No results';
            results.appendChild(empty);
            return;
        }

        list.forEach((item, idx) => {
            const el = document.createElement('div');
            el.className = 'palette-item' + (idx === 0 ? ' active' : '');
            el.dataset.index = idx;

            const icon = document.createElement('span');
            icon.className = 'palette-icon';
            if (item.type === 'model') icon.textContent = '\u2699';
            else if (item.type === 'project') icon.textContent = '\uD83D\uDCC1';
            else if (item.type === 'conversation') icon.textContent = '\uD83D\uDCAC';
            else if (item.type === 'theme') icon.textContent = '\uD83C\uDFA8';
            else icon.textContent = '\u26A1';
            el.appendChild(icon);

            const label = document.createElement('span');
            label.className = 'palette-label';
            label.textContent = item.label;
            el.appendChild(label);

            const hint = document.createElement('span');
            hint.className = 'palette-hint';
            hint.textContent = item.hint;
            el.appendChild(hint);

            el.addEventListener('click', () => {
                item.action();
                close();
            });

            el.addEventListener('mouseenter', () => {
                setActive(idx);
            });

            results.appendChild(el);
        });

        filteredItems = list;
    }

    function setActive(idx) {
        const results = document.getElementById('palette-results');
        const itemEls = results.querySelectorAll('.palette-item');
        if (itemEls.length === 0) return;

        activeIndex = Math.max(0, Math.min(idx, itemEls.length - 1));
        itemEls.forEach((el, i) => {
            el.classList.toggle('active', i === activeIndex);
        });

        itemEls[activeIndex].scrollIntoView({ block: 'nearest' });
    }

    function selectCurrent() {
        const list = filteredItems.length > 0 ? filteredItems : items;
        if (list.length > 0 && activeIndex < list.length) {
            list[activeIndex].action();
            close();
        }
    }

    return { init, open, close };
})();
