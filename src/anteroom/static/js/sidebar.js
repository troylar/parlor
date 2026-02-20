/* Sidebar: conversation list with nested folders, tags, drag-and-drop */

const Sidebar = (() => {
    let conversations = [];
    let folders = [];
    let allTags = [];
    let searchTimeout = null;
    function init() {
        const newChatBtn = document.getElementById('btn-new-chat');
        newChatBtn.addEventListener('click', () => App.newConversation());

        const searchInput = document.getElementById('search-input');
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => search(searchInput.value), 300);
        });

        document.getElementById('btn-folder-add').addEventListener('click', () => _createFolder());
    }

    function _projectParam() {
        const pid = App.state.currentProjectId;
        return pid ? `project_id=${encodeURIComponent(pid)}` : '';
    }

    async function refresh() {
        try {
            const pp = _projectParam();
            const qs = pp ? `?${pp}` : '';
            const convUrl = `/api/conversations${qs}`;
            const folderUrl = pp ? `/api/folders?${pp}` : '/api/folders';
            [conversations, folders, allTags] = await Promise.all([
                App.api(convUrl),
                App.api(folderUrl).catch(e => { console.error('Failed to fetch folders:', e); return []; }),
                App.api('/api/tags').catch(() => []),
            ]);
            render();
        } catch {
            conversations = [];
            folders = [];
            allTags = [];
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
            conversations = await App.api(`/api/conversations${qs}`);
            render();
        } catch { /* keep current list */ }
    }

    // --- Tree helpers ---

    function _buildFolderTree(folderList) {
        const map = {};
        folderList.forEach(f => { map[f.id] = { ...f, children: [] }; });
        const roots = [];
        folderList.forEach(f => {
            if (f.parent_id && map[f.parent_id]) {
                map[f.parent_id].children.push(map[f.id]);
            } else {
                roots.push(map[f.id]);
            }
        });
        return roots;
    }

    // --- Render ---

    function render() {
        const list = document.getElementById('conversation-list');
        list.innerHTML = '';

        if (conversations.length === 0 && folders.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No conversations yet';
            list.appendChild(empty);
            return;
        }

        const folderedConvIds = new Set();
        const tree = _buildFolderTree(folders);

        // Render folder tree recursively
        tree.forEach(folder => _renderFolder(list, folder, 0, folderedConvIds));

        // Unfiled drop zone
        const unfiledZone = document.createElement('div');
        unfiledZone.className = 'unfiled-zone';
        unfiledZone.dataset.folderId = '';
        _setupDropTarget(unfiledZone, null);
        list.appendChild(unfiledZone);

        // Unfiled conversations
        const unfiled = conversations.filter(c => !folderedConvIds.has(c.id));
        unfiled.forEach(c => {
            unfiledZone.appendChild(_createConversationItem(c));
        });
    }

    function _renderFolder(container, folder, depth, folderedConvIds) {
        const folderConvs = conversations.filter(c => c.folder_id === folder.id);
        folderConvs.forEach(c => folderedConvIds.add(c.id));

        // Folder header
        const header = document.createElement('div');
        header.className = 'folder-header' + (folder.collapsed ? ' collapsed' : '');
        header.style.paddingLeft = (12 + depth * 16) + 'px';
        header.dataset.folderId = folder.id;
        header.innerHTML = `
            <span class="folder-chevron"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span>
            <span class="folder-name">${_escapeHtml(folder.name)}</span>
            <span class="folder-actions">
                <button title="Add subfolder" data-action="subfolder">+</button>
                <button title="Rename" data-action="rename">&#9998;</button>
                <button title="Delete" data-action="delete">&#10005;</button>
            </span>
        `;

        header.addEventListener('click', (e) => {
            if (e.target.closest('.folder-actions button')) return;
            const newCollapsed = !header.classList.contains('collapsed');
            header.classList.toggle('collapsed', newCollapsed);
            const contents = header.nextElementSibling;
            if (contents && contents.classList.contains('folder-contents')) {
                contents.classList.toggle('collapsed', newCollapsed);
            }
            _updateFolderCollapsed(folder.id, newCollapsed);
        });

        header.querySelector('[data-action="subfolder"]').addEventListener('click', (e) => {
            e.stopPropagation();
            _createFolder(folder.id);
        });
        header.querySelector('[data-action="rename"]').addEventListener('click', (e) => {
            e.stopPropagation();
            _renameFolder(folder.id, folder.name);
        });
        header.querySelector('[data-action="delete"]').addEventListener('click', (e) => {
            e.stopPropagation();
            _deleteFolder(folder.id);
        });

        _setupDropTarget(header, folder.id);
        container.appendChild(header);

        // Folder contents
        const contents = document.createElement('div');
        contents.className = 'folder-contents' + (folder.collapsed ? ' collapsed' : '');
        contents.dataset.folderId = folder.id;
        _setupDropTarget(contents, folder.id);

        // Render child folders first
        if (folder.children) {
            folder.children.forEach(child => _renderFolder(contents, child, depth + 1, folderedConvIds));
        }

        // Render conversations in this folder
        folderConvs.forEach(c => {
            contents.appendChild(_createConversationItem(c, depth + 1));
        });

        container.appendChild(contents);
    }

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // --- Conversation item ---

    function _createConversationItem(c, depth) {
        const div = document.createElement('div');
        div.className = `conversation-item ${c.id === App.state.currentConversationId ? 'active' : ''}`;
        div.dataset.id = c.id;
        div.draggable = true;
        if (depth) div.style.paddingLeft = (12 + depth * 16) + 'px';

        // Drag events
        div.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/plain', c.id);
            e.dataTransfer.effectAllowed = 'move';
            div.classList.add('dragging');
            setTimeout(() => document.querySelectorAll('.folder-header, .folder-contents, .unfiled-zone').forEach(el => {
                el.classList.add('drop-candidate');
            }), 0);
        });
        div.addEventListener('dragend', () => {
            div.classList.remove('dragging');
            document.querySelectorAll('.drop-candidate, .drag-over').forEach(el => {
                el.classList.remove('drop-candidate', 'drag-over');
            });
        });

        const title = document.createElement('span');
        title.className = 'conv-title';
        title.textContent = c.title;
        div.appendChild(title);

        // Tags
        if (c.tags && c.tags.length > 0) {
            const tagRow = document.createElement('span');
            tagRow.className = 'conv-tags';
            c.tags.forEach(t => {
                const tag = document.createElement('span');
                tag.className = 'conv-tag';
                tag.style.backgroundColor = t.color + '30';
                tag.style.color = t.color;
                tag.style.borderColor = t.color + '50';
                tag.textContent = t.name;
                tagRow.appendChild(tag);
            });
            div.appendChild(tagRow);
        }

        const actions = document.createElement('span');
        actions.className = 'conv-actions';

        // Tag button
        const tagBtn = document.createElement('button');
        tagBtn.title = 'Tags';
        tagBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
        tagBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            _showTagDropdown(tagBtn, c.id, c.tags || []);
        });
        actions.appendChild(tagBtn);

        // Move to folder button
        if (folders.length > 0) {
            const moveBtn = document.createElement('button');
            moveBtn.title = 'Move to folder';
            moveBtn.className = 'move-to-folder-btn';
            moveBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>';
            moveBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _showFolderDropdown(moveBtn, c.id, c.folder_id);
            });
            actions.appendChild(moveBtn);
        }

        // Copy to another database
        if (App.state.databases.length > 1) {
            const copyBtn = document.createElement('button');
            copyBtn.title = 'Copy to database';
            copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
            copyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _showCopyDbDropdown(copyBtn, c.id);
            });
            actions.appendChild(copyBtn);
        }

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

        return div;
    }

    // --- Drag-and-drop ---

    function _setupDropTarget(el, folderId) {
        el.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            e.dataTransfer.dropEffect = 'move';
            // Only highlight the direct target, not ancestors
            document.querySelectorAll('.drag-over').forEach(d => d.classList.remove('drag-over'));
            el.classList.add('drag-over');
        });
        el.addEventListener('dragleave', (e) => {
            if (!el.contains(e.relatedTarget)) {
                el.classList.remove('drag-over');
            }
        });
        el.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            el.classList.remove('drag-over');
            const conversationId = e.dataTransfer.getData('text/plain');
            if (conversationId) {
                await _moveToFolder(conversationId, folderId);
            }
        });
    }

    // --- Tag dropdown ---

    function _positionDropdown(dropdown, anchor) {
        const rect = anchor.getBoundingClientRect();
        dropdown.style.position = 'fixed';
        dropdown.style.top = (rect.bottom + 4) + 'px';
        dropdown.style.left = Math.min(rect.left, window.innerWidth - 180) + 'px';
        dropdown.style.right = 'auto';
    }

    function _showTagDropdown(btn, conversationId, currentTags) {
        document.querySelectorAll('.tag-dropdown').forEach(d => d.remove());

        const dropdown = document.createElement('div');
        dropdown.className = 'tag-dropdown';

        const currentIds = new Set(currentTags.map(t => t.id));

        allTags.forEach(t => {
            const item = document.createElement('div');
            item.className = 'tag-dropdown-item' + (currentIds.has(t.id) ? ' active' : '');
            const dot = document.createElement('span');
            dot.className = 'tag-dot';
            dot.style.backgroundColor = t.color;
            item.appendChild(dot);
            item.appendChild(document.createTextNode(t.name));
            item.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (currentIds.has(t.id)) {
                    await App.api(`/api/conversations/${conversationId}/tags/${t.id}`, { method: 'DELETE' });
                } else {
                    await App.api(`/api/conversations/${conversationId}/tags/${t.id}`, { method: 'POST' });
                }
                dropdown.remove();
                await refresh();
            });
            dropdown.appendChild(item);
        });

        const newItem = document.createElement('div');
        newItem.className = 'tag-dropdown-item tag-dropdown-new';
        newItem.textContent = '+ New tag...';
        newItem.addEventListener('click', async (e) => {
            e.stopPropagation();
            dropdown.remove();
            await _createTag(conversationId);
        });
        dropdown.appendChild(newItem);

        document.body.appendChild(dropdown);
        _positionDropdown(dropdown, btn);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btn) {
                dropdown.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }

    async function _createTag(conversationId) {
        const name = prompt('Tag name:');
        if (!name || !name.trim()) return;
        try {
            const tag = await App.api('/api/tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() }),
            });
            if (conversationId) {
                await App.api(`/api/conversations/${conversationId}/tags/${tag.id}`, { method: 'POST' });
            }
            await refresh();
        } catch (e) {
            console.error('[debug] failed to create tag');
        }
    }

    // --- Folder dropdown ---

    function _showFolderDropdown(btn, conversationId, currentFolderId) {
        document.querySelectorAll('.folder-dropdown').forEach(d => d.remove());

        const dropdown = document.createElement('div');
        dropdown.className = 'folder-dropdown';

        const noFolder = document.createElement('div');
        noFolder.className = 'folder-dropdown-item';
        noFolder.textContent = 'No folder';
        if (!currentFolderId) noFolder.style.fontWeight = '600';
        noFolder.addEventListener('click', async (e) => {
            e.stopPropagation();
            dropdown.remove();
            await _moveToFolder(conversationId, null);
        });
        dropdown.appendChild(noFolder);

        const tree = _buildFolderTree(folders);
        _renderFolderDropdownItems(dropdown, tree, 0, conversationId, currentFolderId);

        document.body.appendChild(dropdown);
        _positionDropdown(dropdown, btn);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btn) {
                dropdown.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }

    function _renderFolderDropdownItems(dropdown, folderList, depth, conversationId, currentFolderId) {
        folderList.forEach(f => {
            const item = document.createElement('div');
            item.className = 'folder-dropdown-item';
            item.style.paddingLeft = (8 + depth * 12) + 'px';
            item.textContent = f.name;
            if (f.id === currentFolderId) item.style.fontWeight = '600';
            item.addEventListener('click', async (e) => {
                e.stopPropagation();
                dropdown.remove();
                await _moveToFolder(conversationId, f.id);
            });
            dropdown.appendChild(item);
            if (f.children && f.children.length > 0) {
                _renderFolderDropdownItems(dropdown, f.children, depth + 1, conversationId, currentFolderId);
            }
        });
    }

    // --- Copy to DB dropdown ---

    function _showCopyDbDropdown(btn, conversationId) {
        document.querySelectorAll('.db-copy-dropdown').forEach(d => d.remove());

        const dropdown = document.createElement('div');
        dropdown.className = 'db-copy-dropdown';

        const currentDb = App.state.currentDatabase || 'personal';
        App.state.databases.forEach(db => {
            if (db.name === currentDb) return;
            const item = document.createElement('div');
            item.className = 'folder-dropdown-item';
            item.textContent = db.name;
            item.addEventListener('click', async (e) => {
                e.stopPropagation();
                dropdown.remove();
                try {
                    await App.api(`/api/conversations/${conversationId}/copy?target_db=${encodeURIComponent(db.name)}`
                        , { method: 'POST' });
                } catch (err) {
                    console.error('[debug] copy failed');
                }
            });
            dropdown.appendChild(item);
        });

        document.body.appendChild(dropdown);
        _positionDropdown(dropdown, btn);

        const closeHandler = (e) => {
            if (!dropdown.contains(e.target) && e.target !== btn) {
                dropdown.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    }

    // --- Folder CRUD ---

    async function _moveToFolder(conversationId, folderId) {
        try {
            await App.api(`/api/conversations/${conversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder_id: folderId || '' }),
            });
            await refresh();
        } catch { /* ignore */ }
    }

    async function _createFolder(parentId) {
        const name = prompt(parentId ? 'Subfolder name:' : 'Folder name:');
        if (!name || !name.trim()) return;
        try {
            const payload = { name: name.trim() };
            if (parentId) payload.parent_id = parentId;
            if (App.state.currentProjectId) {
                payload.project_id = App.state.currentProjectId;
            }
            await App.api('/api/folders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            await refresh();
        } catch (e) {
            console.error('[debug] failed to create folder');
        }
    }

    async function _renameFolder(folderId, currentName) {
        const name = prompt('Rename folder:', currentName);
        if (!name || !name.trim() || name.trim() === currentName) return;
        try {
            await App.api(`/api/folders/${folderId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() }),
            });
            await refresh();
        } catch { /* ignore */ }
    }

    async function _deleteFolder(folderId) {
        if (!confirm('Delete this folder and all subfolders? Conversations will be kept but unfoldered.')) return;
        try {
            await App.api(`/api/folders/${folderId}`, { method: 'DELETE' });
            await refresh();
        } catch { /* ignore */ }
    }

    async function _updateFolderCollapsed(folderId, collapsed) {
        try {
            await App.api(`/api/folders/${folderId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ collapsed }),
            });
        } catch { /* ignore */ }
    }

    // --- Conversation actions ---

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
            const response = await App.api(`/api/conversations/${id}/export`);
            const blob = response instanceof Response ? await response.blob() : new Blob([JSON.stringify(response)]);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            const disposition = (response instanceof Response ? response.headers.get('Content-Disposition') : '') || '';
            const match = disposition.match(/filename="(.+?)"/);
            let filename = 'conversation.md';
            if (match) {
                filename = match[1].replace(/[\/\\:*?"<>|\x00-\x1f]/g, '_').substring(0, 255);
            }
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch { /* ignore */ }
    }

    return { init, refresh, render, select, setActive, updateTitle, rename, remove, exportConv };
})();
