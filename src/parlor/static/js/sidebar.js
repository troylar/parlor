/* Sidebar: conversation list, search, management */

const Sidebar = (() => {
    let conversations = [];
    let searchTimeout = null;

    function init() {
        document.getElementById('btn-new-chat').addEventListener('click', () => App.newConversation());

        const searchInput = document.getElementById('search-input');
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => search(searchInput.value), 300);
        });
    }

    function _projectParam() {
        const pid = App.state.currentProjectId;
        return pid ? `project_id=${encodeURIComponent(pid)}` : '';
    }

    async function refresh() {
        try {
            const pp = _projectParam();
            const url = pp ? `/api/conversations?${pp}` : '/api/conversations';
            conversations = await App.api(url);
            render();
        } catch {
            conversations = [];
            render();
        }
    }

    async function search(query) {
        try {
            const q = query.trim();
            const pp = _projectParam();
            const params = [];
            if (q) params.push(`search=${encodeURIComponent(q)}`);
            if (pp) params.push(pp);
            const qs = params.length ? `?${params.join('&')}` : '';
            const url = `/api/conversations${qs}`;
            conversations = await App.api(url);
            render();
        } catch {
            // keep current list on error
        }
    }

    function render() {
        const list = document.getElementById('conversation-list');
        list.innerHTML = '';

        if (conversations.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No conversations yet';
            list.appendChild(empty);
            return;
        }

        conversations.forEach(c => {
            const div = document.createElement('div');
            div.className = `conversation-item ${c.id === App.state.currentConversationId ? 'active' : ''}`;
            div.dataset.id = c.id;

            const title = document.createElement('span');
            title.className = 'conv-title';
            title.textContent = c.title;
            div.appendChild(title);

            const actions = document.createElement('span');
            actions.className = 'conv-actions';

            const renameBtn = document.createElement('button');
            renameBtn.title = 'Rename';
            renameBtn.innerHTML = '&#9998;';
            renameBtn.addEventListener('click', (e) => { e.stopPropagation(); rename(c.id); });
            actions.appendChild(renameBtn);

            const exportBtn = document.createElement('button');
            exportBtn.title = 'Export';
            exportBtn.innerHTML = '&#8681;';
            exportBtn.addEventListener('click', (e) => { e.stopPropagation(); exportConv(c.id); });
            actions.appendChild(exportBtn);

            const deleteBtn = document.createElement('button');
            deleteBtn.title = 'Delete';
            deleteBtn.innerHTML = '&#10005;';
            deleteBtn.addEventListener('click', (e) => { e.stopPropagation(); remove(c.id); });
            actions.appendChild(deleteBtn);

            div.appendChild(actions);
            div.addEventListener('click', () => select(c.id));
            div.addEventListener('dblclick', () => rename(c.id));
            list.appendChild(div);
        });
    }

    async function select(id) {
        await App.loadConversation(id);
        render();
    }

    function setActive(id) {
        document.querySelectorAll('.conversation-item').forEach(el => {
            el.classList.toggle('active', el.dataset.id === id);
        });
    }

    function _findItemById(id) {
        return [...document.querySelectorAll('.conversation-item')].find(el => el.dataset.id === id);
    }

    function updateTitle(id, title) {
        const item = _findItemById(id);
        if (item) {
            const el = item.querySelector('.conv-title');
            if (el) el.textContent = title;
        }
        const conv = conversations.find(c => c.id === id);
        if (conv) conv.title = title;
    }

    async function rename(id) {
        const item = _findItemById(id);
        if (!item) return;
        const titleEl = item.querySelector('.conv-title');
        const currentTitle = titleEl.textContent;

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'rename-input';
        input.value = currentTitle;
        titleEl.replaceWith(input);
        input.focus();
        input.select();

        const finish = async () => {
            const newTitle = input.value.trim();
            if (newTitle && newTitle !== currentTitle) {
                try {
                    await App.api(`/api/conversations/${id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: newTitle }),
                    });
                } catch { /* ignore */ }
            }
            await refresh();
        };

        input.addEventListener('blur', finish);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
            if (e.key === 'Escape') { input.value = currentTitle; input.blur(); }
        });
    }

    async function remove(id) {
        if (!confirm('Delete this conversation? This cannot be undone.')) return;
        try {
            await App.api(`/api/conversations/${id}`, { method: 'DELETE' });
            if (App.state.currentConversationId === id) {
                App.state.currentConversationId = null;
                Chat.loadMessages([]);
            }
            await refresh();
        } catch { /* ignore */ }
    }

    async function exportConv(id) {
        try {
            const response = await fetch(`/api/conversations/${id}/export`, { credentials: 'same-origin' });
            if (!response.ok) throw new Error('Export failed');
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="(.+?)"/);
            a.download = match ? match[1] : 'conversation.md';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch { /* ignore */ }
    }

    return { init, refresh, render, select, setActive, updateTitle, rename, remove, exportConv };
})();
