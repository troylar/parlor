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

    async function refresh() {
        try {
            conversations = await App.api('/api/conversations');
            render();
        } catch {
            conversations = [];
            render();
        }
    }

    async function search(query) {
        try {
            const q = query.trim();
            const url = q ? `/api/conversations?search=${encodeURIComponent(q)}` : '/api/conversations';
            conversations = await App.api(url);
            render();
        } catch {
            // keep current list on error
        }
    }

    function render() {
        const list = document.getElementById('conversation-list');
        if (conversations.length === 0) {
            list.innerHTML = '<div class="empty-state">No conversations yet</div>';
            return;
        }
        list.innerHTML = conversations.map(c => `
            <div class="conversation-item ${c.id === App.state.currentConversationId ? 'active' : ''}"
                 data-id="${c.id}">
                <span class="conv-title">${Chat.escapeHtml(c.title)}</span>
                <span class="conv-actions">
                    <button onclick="event.stopPropagation(); Sidebar.rename('${c.id}')" title="Rename">&#9998;</button>
                    <button onclick="event.stopPropagation(); Sidebar.exportConv('${c.id}')" title="Export">&#8681;</button>
                    <button onclick="event.stopPropagation(); Sidebar.remove('${c.id}')" title="Delete">&#10005;</button>
                </span>
            </div>
        `).join('');

        list.querySelectorAll('.conversation-item').forEach(el => {
            el.addEventListener('click', () => select(el.dataset.id));
            el.addEventListener('dblclick', () => rename(el.dataset.id));
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

    function updateTitle(id, title) {
        const el = document.querySelector(`.conversation-item[data-id="${id}"] .conv-title`);
        if (el) el.textContent = title;
        const conv = conversations.find(c => c.id === id);
        if (conv) conv.title = title;
    }

    async function rename(id) {
        const item = document.querySelector(`.conversation-item[data-id="${id}"]`);
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
            const exportHeaders = {};
            if (window.__PARLOR_TOKEN) exportHeaders['Authorization'] = `Bearer ${window.__PARLOR_TOKEN}`;
            const response = await fetch(`/api/conversations/${id}/export`, { headers: exportHeaders });
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
