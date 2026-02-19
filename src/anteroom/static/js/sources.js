/* Sources panel: global knowledge store management + chat reference picker */

const Sources = (() => {
    let _sources = [];
    let _currentView = 'list'; // list | detail | create
    let _currentSource = null;
    let _searchTimeout = null;
    let _selectedFile = null;
    let _createType = 'text';
    let _isEditing = false;
    let _viewMode = 'sources'; // sources | groups
    let _groups = [];
    let _currentGroup = null;

    // Source references attached to the next chat message
    let _attachedSources = [];   // [{id, title, type}]
    let _attachedTag = null;     // {id, name}
    let _attachedGroup = null;   // {id, name}

    function init() {
        const closeBtn = document.getElementById('sources-close');
        const toggleBtn = document.getElementById('btn-sources-toggle');
        const addBtn = document.getElementById('sources-add-btn');
        const searchInput = document.getElementById('sources-search');
        const typeFilter = document.getElementById('sources-type-filter');
        const createCancelBtn = document.getElementById('source-create-cancel');
        const createSaveBtn = document.getElementById('source-create-save');
        const fileDrop = document.getElementById('source-file-drop');
        const fileInput = document.getElementById('source-file-input');

        if (closeBtn) closeBtn.addEventListener('click', closePanel);
        if (toggleBtn) toggleBtn.addEventListener('click', togglePanel);
        if (addBtn) addBtn.addEventListener('click', () => {
            if (_viewMode === 'groups') {
                _createGroup();
            } else {
                showCreateView();
            }
        });
        if (createCancelBtn) createCancelBtn.addEventListener('click', showListView);
        if (createSaveBtn) createSaveBtn.addEventListener('click', saveSource);

        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(_searchTimeout);
                _searchTimeout = setTimeout(() => refreshList(), 300);
            });
        }

        if (typeFilter) {
            typeFilter.addEventListener('change', () => refreshList());
        }

        // Create form tabs
        const tabContainer = document.querySelector('.sources-create-tabs');
        if (tabContainer) {
            tabContainer.addEventListener('click', (e) => {
                const tab = e.target.closest('.sources-tab');
                if (!tab) return;
                _setCreateType(tab.dataset.type);
            });
        }

        // View tabs (Sources vs Groups)
        const viewTabs = document.querySelectorAll('.sources-view-tab');
        viewTabs.forEach(tab => {
            tab.addEventListener('click', () => _switchViewMode(tab.dataset.view));
        });

        // File drop zone
        if (fileDrop && fileInput) {
            fileDrop.addEventListener('click', () => fileInput.click());
            fileDrop.addEventListener('dragover', (e) => {
                e.preventDefault();
                fileDrop.classList.add('dragover');
            });
            fileDrop.addEventListener('dragleave', () => {
                fileDrop.classList.remove('dragover');
            });
            fileDrop.addEventListener('drop', (e) => {
                e.preventDefault();
                fileDrop.classList.remove('dragover');
                if (e.dataTransfer.files.length > 0) {
                    _selectedFile = e.dataTransfer.files[0];
                    document.getElementById('source-file-name').textContent = _selectedFile.name;
                }
            });
            fileInput.addEventListener('change', () => {
                if (fileInput.files.length > 0) {
                    _selectedFile = fileInput.files[0];
                    document.getElementById('source-file-name').textContent = _selectedFile.name;
                }
            });
        }
    }

    function _setCreateType(type) {
        _createType = type;
        const tabs = document.querySelectorAll('.sources-create-tabs .sources-tab');
        tabs.forEach(t => t.classList.toggle('active', t.dataset.type === type));

        document.getElementById('source-content-group').style.display = type === 'text' ? '' : 'none';
        document.getElementById('source-url-group').style.display = type === 'url' ? '' : 'none';
        document.getElementById('source-file-group').style.display = type === 'file' ? '' : 'none';
    }

    async function togglePanel() {
        const panel = document.getElementById('sources-panel');
        if (panel && panel.style.display !== 'none') {
            closePanel();
        } else {
            openPanel();
        }
    }

    async function openPanel() {
        const panel = document.getElementById('sources-panel');
        const chatMain = document.querySelector('.chat-main');
        const toggleBtn = document.getElementById('btn-sources-toggle');

        // Close canvas if open (mutual exclusion)
        const canvasPanel = document.getElementById('canvas-panel');
        if (canvasPanel && canvasPanel.style.display !== 'none') {
            Canvas.closeCanvas();
        }

        if (panel) panel.style.display = '';
        if (chatMain) chatMain.classList.add('with-sources');
        if (toggleBtn) toggleBtn.classList.add('active');

        showListView();
        await refreshList();
    }

    function closePanel() {
        const panel = document.getElementById('sources-panel');
        const chatMain = document.querySelector('.chat-main');
        const toggleBtn = document.getElementById('btn-sources-toggle');

        if (panel) panel.style.display = 'none';
        if (chatMain) chatMain.classList.remove('with-sources');
        if (toggleBtn) toggleBtn.classList.remove('active');
    }

    function showListView() {
        _currentView = 'list';
        _currentSource = null;
        _currentGroup = null;
        _isEditing = false;

        const isGroups = _viewMode === 'groups';
        document.getElementById('sources-list').style.display = isGroups ? 'none' : '';
        document.getElementById('sources-groups-list').style.display = isGroups ? '' : 'none';
        document.getElementById('sources-group-detail').style.display = 'none';
        document.getElementById('sources-detail').style.display = 'none';
        document.getElementById('sources-create').style.display = 'none';

        const toolbar = document.getElementById('sources-toolbar');
        if (toolbar) toolbar.style.display = isGroups ? 'none' : '';
    }

    function showCreateView() {
        _currentView = 'create';
        _selectedFile = null;
        _createType = 'text';
        document.getElementById('sources-list').style.display = 'none';
        document.getElementById('sources-groups-list').style.display = 'none';
        document.getElementById('sources-group-detail').style.display = 'none';
        document.getElementById('sources-detail').style.display = 'none';
        document.getElementById('sources-create').style.display = '';
        document.getElementById('sources-toolbar').style.display = 'none';

        // Reset form
        document.getElementById('source-title-input').value = '';
        document.getElementById('source-content-input').value = '';
        document.getElementById('source-url-input').value = '';
        document.getElementById('source-file-name').textContent = '';
        const fileInput = document.getElementById('source-file-input');
        if (fileInput) fileInput.value = '';
        _setCreateType('text');
    }

    async function showDetailView(sourceId) {
        _currentView = 'detail';
        _isEditing = false;
        document.getElementById('sources-list').style.display = 'none';
        document.getElementById('sources-groups-list').style.display = 'none';
        document.getElementById('sources-group-detail').style.display = 'none';
        document.getElementById('sources-create').style.display = 'none';
        document.getElementById('sources-toolbar').style.display = 'none';

        const detail = document.getElementById('sources-detail');
        detail.style.display = '';
        detail.innerHTML = '<div class="sources-loading">Loading...</div>';

        try {
            const source = await App.api(`/api/sources/${encodeURIComponent(sourceId)}`);
            _currentSource = source;
            _renderDetail(source);
        } catch (err) {
            detail.innerHTML = `<div class="sources-error">${DOMPurify.sanitize(err.message)}</div>`;
        }
    }

    function _renderDetail(source) {
        const detail = document.getElementById('sources-detail');
        const typeBadge = _typeBadge(source.type);
        const created = App.formatTimestamp(source.created_at);
        const isAttached = _attachedSources.some(s => s.id === source.id);

        let contentHtml = '';
        if (source.content) {
            const sanitized = DOMPurify.sanitize(source.content);
            contentHtml = `<div class="source-detail-content"><pre>${sanitized}</pre></div>`;
        }
        if (source.url) {
            const safeUrl = DOMPurify.sanitize(source.url);
            const isHttpUrl = /^https?:\/\//i.test(safeUrl);
            if (isHttpUrl) {
                contentHtml += `<div class="source-detail-url"><a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a></div>`;
            } else {
                contentHtml += `<div class="source-detail-url">${safeUrl}</div>`;
            }
        }
        if (source.filename) {
            contentHtml += `<div class="source-detail-meta">File: ${DOMPurify.sanitize(source.filename)}</div>`;
        }
        if (source.size_bytes) {
            contentHtml += `<div class="source-detail-meta">Size: ${_formatBytes(source.size_bytes)}</div>`;
        }

        const tagsHtml = (source.tags || []).map(t =>
            `<span class="source-tag" data-tag-id="${DOMPurify.sanitize(t.id)}">${DOMPurify.sanitize(t.name)}<button class="source-tag-remove" data-tag-id="${DOMPurify.sanitize(t.id)}" title="Remove tag">&times;</button></span>`
        ).join('');

        const chunksCount = (source.chunks || []).length;

        detail.innerHTML = `
            <div class="source-detail-header">
                <button class="source-back-btn" id="source-back-btn" title="Back to list">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
                </button>
                <div class="source-detail-title-row">
                    <h3 class="source-detail-title">${DOMPurify.sanitize(source.title)}</h3>
                    ${typeBadge}
                </div>
            </div>
            <div class="source-detail-info">
                <span class="source-detail-date">${created}</span>
                ${chunksCount > 0 ? `<span class="source-detail-chunks">${chunksCount} chunks</span>` : ''}
            </div>
            <div class="source-detail-tags" id="source-detail-tags">
                ${tagsHtml}
                <button class="source-tag-add-btn" id="source-tag-add-btn" title="Add tag">+ tag</button>
            </div>
            ${contentHtml}
            <div class="source-detail-actions">
                <button class="btn-modal-save source-attach-btn" id="source-attach-btn">${isAttached ? 'Detach from chat' : 'Attach to chat'}</button>
                <button class="btn-modal-save source-edit-btn" id="source-edit-btn">Edit</button>
                ${App.state.currentProjectId ? `<button class="btn-modal-save source-link-project-btn" id="source-link-project-btn">Link to project</button>` : ''}
                <button class="btn-modal-cancel source-delete-btn" id="source-delete-btn">Delete</button>
            </div>
        `;

        // Back button
        document.getElementById('source-back-btn').addEventListener('click', async () => {
            showListView();
            await refreshList();
        });

        // Attach/detach to chat
        document.getElementById('source-attach-btn').addEventListener('click', () => {
            if (isAttached) {
                _attachedSources = _attachedSources.filter(s => s.id !== source.id);
            } else {
                _attachedSources.push({ id: source.id, title: source.title, type: source.type });
            }
            _renderAttachedBar();
            _renderDetail(source); // refresh button label
        });

        // Edit button
        document.getElementById('source-edit-btn').addEventListener('click', () => {
            _showEditForm(source);
        });

        // Link to project
        const linkBtn = document.getElementById('source-link-project-btn');
        if (linkBtn) {
            linkBtn.addEventListener('click', async () => {
                try {
                    await App.api(`/api/projects/${encodeURIComponent(App.state.currentProjectId)}/sources`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ source_id: source.id }),
                    });
                    linkBtn.textContent = 'Linked!';
                    linkBtn.disabled = true;
                    setTimeout(() => { linkBtn.textContent = 'Link to project'; linkBtn.disabled = false; }, 1500);
                } catch (err) {
                    alert('Failed to link: ' + err.message);
                }
            });
        }

        // Delete button
        document.getElementById('source-delete-btn').addEventListener('click', async () => {
            if (!confirm('Delete this source? This cannot be undone.')) return;
            try {
                await App.api(`/api/sources/${encodeURIComponent(source.id)}`, { method: 'DELETE' });
                _attachedSources = _attachedSources.filter(s => s.id !== source.id);
                _renderAttachedBar();
                showListView();
                await refreshList();
            } catch (err) {
                alert('Failed to delete: ' + err.message);
            }
        });

        // Tag remove buttons
        detail.querySelectorAll('.source-tag-remove').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const tagId = btn.dataset.tagId;
                try {
                    await App.api(`/api/sources/${encodeURIComponent(source.id)}/tags/${encodeURIComponent(tagId)}`, { method: 'DELETE' });
                    await showDetailView(source.id);
                } catch (err) {
                    alert('Failed to remove tag: ' + err.message);
                }
            });
        });

        // Add tag button
        document.getElementById('source-tag-add-btn').addEventListener('click', () => {
            _showTagPicker(source);
        });
    }

    function _showEditForm(source) {
        _isEditing = true;
        const detail = document.getElementById('sources-detail');
        detail.innerHTML = `
            <div class="source-detail-header">
                <button class="source-back-btn" id="source-edit-cancel" title="Cancel editing">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
                </button>
                <h3 class="source-detail-title">Edit Source</h3>
            </div>
            <div class="sources-create-form">
                <div class="setting-group">
                    <label for="source-edit-title">Title</label>
                    <input type="text" id="source-edit-title" value="${DOMPurify.sanitize(source.title || '')}" autocomplete="off">
                </div>
                ${source.type === 'text' || source.type === 'file' ? `
                <div class="setting-group">
                    <label for="source-edit-content">Content</label>
                    <textarea id="source-edit-content" rows="12">${DOMPurify.sanitize(source.content || '')}</textarea>
                </div>` : ''}
                ${source.type === 'url' ? `
                <div class="setting-group">
                    <label for="source-edit-url">URL</label>
                    <input type="url" id="source-edit-url" value="${DOMPurify.sanitize(source.url || '')}" autocomplete="off">
                </div>` : ''}
                <div class="sources-create-actions">
                    <button class="btn-modal-cancel" id="source-edit-cancel-btn">Cancel</button>
                    <button class="btn-modal-save" id="source-edit-save-btn">Save</button>
                </div>
            </div>
        `;

        document.getElementById('source-edit-cancel').addEventListener('click', () => showDetailView(source.id));
        document.getElementById('source-edit-cancel-btn').addEventListener('click', () => showDetailView(source.id));
        document.getElementById('source-edit-save-btn').addEventListener('click', async () => {
            const payload = {};
            const newTitle = document.getElementById('source-edit-title').value.trim();
            if (newTitle && newTitle !== source.title) payload.title = newTitle;

            const contentEl = document.getElementById('source-edit-content');
            if (contentEl && contentEl.value !== (source.content || '')) payload.content = contentEl.value;

            const urlEl = document.getElementById('source-edit-url');
            if (urlEl && urlEl.value.trim() !== (source.url || '')) payload.url = urlEl.value.trim();

            if (Object.keys(payload).length === 0) {
                showDetailView(source.id);
                return;
            }

            try {
                await App.api(`/api/sources/${encodeURIComponent(source.id)}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                await showDetailView(source.id);
            } catch (err) {
                alert('Failed to save: ' + err.message);
            }
        });
    }

    async function _showTagPicker(source) {
        const tagsContainer = document.getElementById('source-detail-tags');
        const addBtn = document.getElementById('source-tag-add-btn');
        if (!tagsContainer || !addBtn) return;

        // Replace add button with inline picker
        const existingTagIds = new Set((source.tags || []).map(t => t.id));
        let allTags = [];
        try {
            allTags = await App.api('/api/tags');
        } catch { return; }

        const available = allTags.filter(t => !existingTagIds.has(t.id));

        const picker = document.createElement('div');
        picker.className = 'source-tag-picker';

        if (available.length === 0) {
            picker.innerHTML = '<span class="source-tag-picker-empty">No more tags</span>';
        } else {
            available.forEach(tag => {
                const btn = document.createElement('button');
                btn.className = 'source-tag-picker-item';
                btn.textContent = tag.name;
                btn.addEventListener('click', async () => {
                    try {
                        await App.api(`/api/sources/${encodeURIComponent(source.id)}/tags/${encodeURIComponent(tag.id)}`, { method: 'POST' });
                        await showDetailView(source.id);
                    } catch (err) {
                        alert('Failed to add tag: ' + err.message);
                    }
                });
                picker.appendChild(btn);
            });
        }

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'source-tag-picker-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => showDetailView(source.id));
        picker.appendChild(cancelBtn);

        addBtn.replaceWith(picker);
    }

    async function refreshList() {
        const listEl = document.getElementById('sources-list');
        if (!listEl || _currentView !== 'list') return;

        const search = (document.getElementById('sources-search') || {}).value || '';
        const typeFilter = (document.getElementById('sources-type-filter') || {}).value || '';

        let url = '/api/sources?limit=100';
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (typeFilter) url += `&type=${encodeURIComponent(typeFilter)}`;

        // Filter by project if one is active
        if (App.state.currentProjectId) {
            url += `&project_id=${encodeURIComponent(App.state.currentProjectId)}`;
        }

        try {
            const data = await App.api(url);
            _sources = data.sources || [];
            _renderList();
        } catch (err) {
            listEl.innerHTML = `<div class="sources-error">${DOMPurify.sanitize(err.message)}</div>`;
        }
    }

    function _renderList() {
        const listEl = document.getElementById('sources-list');
        if (!listEl) return;
        listEl.innerHTML = '';

        // "Attach by tag" link at the top
        const tagLink = document.createElement('div');
        tagLink.className = 'sources-attach-by-tag';
        tagLink.innerHTML = _attachedTag
            ? `<span class="source-ref-chip source-ref-tag" style="margin:0">tag: ${DOMPurify.sanitize(_attachedTag.name)}<button class="source-ref-remove" title="Remove">&times;</button></span>`
            : '<button class="sources-attach-tag-btn" id="sources-attach-tag-btn">Attach by tag...</button>';
        listEl.appendChild(tagLink);

        if (_attachedTag) {
            tagLink.querySelector('.source-ref-remove').addEventListener('click', () => {
                _attachedTag = null;
                _renderAttachedBar();
                _renderList();
            });
        } else {
            const btn = tagLink.querySelector('#sources-attach-tag-btn');
            if (btn) btn.addEventListener('click', _showTagAttachPicker);
        }

        if (_sources.length === 0) {
            listEl.innerHTML += '<div class="sources-empty">No sources yet. Click + to add one.</div>';
            return;
        }

        _sources.forEach(source => {
            const item = document.createElement('div');
            item.className = 'source-item';
            item.dataset.id = source.id;

            const isAttached = _attachedSources.some(s => s.id === source.id);
            const typeBadge = _typeBadge(source.type);
            const title = DOMPurify.sanitize(source.title || 'Untitled');
            const date = App.formatTimestamp(source.created_at);

            item.innerHTML = `
                <div class="source-item-main">
                    <div class="source-item-title">${isAttached ? '<span class="source-attached-dot"></span>' : ''}${title}</div>
                    <div class="source-item-meta">
                        ${typeBadge}
                        <span class="source-item-date">${date}</span>
                    </div>
                </div>
                <button class="source-item-attach" title="${isAttached ? 'Detach' : 'Attach to chat'}">
                    ${isAttached ? '&times;' : '+'}
                </button>
            `;

            item.querySelector('.source-item-main').addEventListener('click', () => showDetailView(source.id));
            item.querySelector('.source-item-attach').addEventListener('click', (e) => {
                e.stopPropagation();
                if (isAttached) {
                    _attachedSources = _attachedSources.filter(s => s.id !== source.id);
                } else {
                    _attachedSources.push({ id: source.id, title: source.title, type: source.type });
                }
                _renderAttachedBar();
                _renderList();
            });
            listEl.appendChild(item);
        });
    }

    function _typeBadge(type) {
        const icons = {
            text: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
            file: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
            url: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>',
        };
        return `<span class="source-type-badge source-type-${DOMPurify.sanitize(type)}">${icons[type] || ''} ${DOMPurify.sanitize(type)}</span>`;
    }

    function _formatBytes(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    async function saveSource() {
        const title = document.getElementById('source-title-input').value.trim();
        if (!title) {
            alert('Title is required.');
            return;
        }

        const saveBtn = document.getElementById('source-create-save');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        try {
            if (_createType === 'file') {
                if (!_selectedFile) {
                    alert('Please select a file.');
                    return;
                }
                const formData = new FormData();
                formData.append('file', _selectedFile);
                formData.append('title', title);
                await App.api('/api/sources/upload', {
                    method: 'POST',
                    body: formData,
                });
            } else {
                const payload = { type: _createType, title };
                if (_createType === 'text') {
                    payload.content = document.getElementById('source-content-input').value;
                    if (!payload.content) {
                        alert('Content is required for text sources.');
                        return;
                    }
                } else if (_createType === 'url') {
                    payload.url = document.getElementById('source-url-input').value.trim();
                    if (!payload.url) {
                        alert('URL is required for URL sources.');
                        return;
                    }
                }
                await App.api('/api/sources', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            }

            showListView();
            await refreshList();
        } catch (err) {
            alert('Failed to create source: ' + err.message);
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        }
    }

    // --- View mode switching (Sources vs Groups) ---

    function _switchViewMode(mode) {
        _viewMode = mode;
        document.querySelectorAll('.sources-view-tab').forEach(t =>
            t.classList.toggle('active', t.dataset.view === mode));

        const toolbar = document.getElementById('sources-toolbar');
        const addBtn = document.getElementById('sources-add-btn');

        if (mode === 'sources') {
            document.getElementById('sources-list').style.display = '';
            document.getElementById('sources-groups-list').style.display = 'none';
            document.getElementById('sources-group-detail').style.display = 'none';
            document.getElementById('sources-detail').style.display = 'none';
            document.getElementById('sources-create').style.display = 'none';
            if (toolbar) toolbar.style.display = '';
            if (addBtn) addBtn.title = 'Add source';
            _currentGroup = null;
            refreshList();
        } else {
            document.getElementById('sources-list').style.display = 'none';
            document.getElementById('sources-groups-list').style.display = '';
            document.getElementById('sources-group-detail').style.display = 'none';
            document.getElementById('sources-detail').style.display = 'none';
            document.getElementById('sources-create').style.display = 'none';
            if (toolbar) toolbar.style.display = 'none';
            if (addBtn) addBtn.title = 'Create group';
            _currentGroup = null;
            _refreshGroups();
        }
    }

    // --- Group management ---

    async function _refreshGroups() {
        const listEl = document.getElementById('sources-groups-list');
        if (!listEl) return;

        try {
            const data = await App.api('/api/source-groups');
            _groups = data.groups || [];
            _renderGroups();
        } catch (err) {
            listEl.innerHTML = `<div class="sources-error">${DOMPurify.sanitize(err.message)}</div>`;
        }
    }

    function _renderGroups() {
        const listEl = document.getElementById('sources-groups-list');
        if (!listEl) return;
        listEl.innerHTML = '';

        if (_groups.length === 0) {
            listEl.innerHTML = '<div class="sources-empty">No groups yet. Click + to create one.</div>';
            return;
        }

        _groups.forEach(group => {
            const item = document.createElement('div');
            item.className = 'source-item source-group-item';
            item.dataset.id = group.id;

            const isAttached = _attachedGroup && _attachedGroup.id === group.id;
            const name = DOMPurify.sanitize(group.name);
            const desc = group.description ? DOMPurify.sanitize(group.description) : '';
            const memberCount = group.source_count || 0;

            item.innerHTML = `
                <div class="source-item-main">
                    <div class="source-item-title">${isAttached ? '<span class="source-attached-dot"></span>' : ''}${name}</div>
                    <div class="source-item-meta">
                        <span class="source-type-badge source-type-group"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="18" rx="2"/><line x1="8" y1="3" x2="8" y2="21"/></svg> group</span>
                        <span class="source-item-date">${memberCount} source${memberCount !== 1 ? 's' : ''}</span>
                        ${desc ? `<span class="source-item-date">${desc}</span>` : ''}
                    </div>
                </div>
                <button class="source-item-attach" title="${isAttached ? 'Detach' : 'Attach group to chat'}">
                    ${isAttached ? '&times;' : '+'}
                </button>
            `;

            item.querySelector('.source-item-main').addEventListener('click', () => _showGroupDetail(group.id));
            item.querySelector('.source-item-attach').addEventListener('click', (e) => {
                e.stopPropagation();
                if (isAttached) {
                    _attachedGroup = null;
                } else {
                    _attachedGroup = { id: group.id, name: group.name };
                }
                _renderAttachedBar();
                _renderGroups();
            });
            listEl.appendChild(item);
        });
    }

    async function _showGroupDetail(groupId) {
        const detailEl = document.getElementById('sources-group-detail');
        const groupsListEl = document.getElementById('sources-groups-list');
        if (!detailEl) return;

        groupsListEl.style.display = 'none';
        detailEl.style.display = '';
        detailEl.innerHTML = '<div class="sources-loading">Loading...</div>';

        try {
            const group = await App.api(`/api/source-groups/${encodeURIComponent(groupId)}`);
            _currentGroup = group;

            // Fetch members - list sources filtered by group
            let members = [];
            try {
                const data = await App.api(`/api/sources?group_id=${encodeURIComponent(groupId)}&limit=100`);
                members = data.sources || [];
            } catch { /* empty group */ }

            _renderGroupDetail(group, members);
        } catch (err) {
            detailEl.innerHTML = `<div class="sources-error">${DOMPurify.sanitize(err.message)}</div>`;
        }
    }

    function _renderGroupDetail(group, members) {
        const detailEl = document.getElementById('sources-group-detail');
        const isAttached = _attachedGroup && _attachedGroup.id === group.id;

        let membersHtml = '';
        if (members.length === 0) {
            membersHtml = '<div class="sources-empty">No sources in this group yet.</div>';
        } else {
            membersHtml = members.map(src => `
                <div class="source-item source-group-member">
                    <div class="source-item-main">
                        <div class="source-item-title">${DOMPurify.sanitize(src.title)}</div>
                        <div class="source-item-meta">${_typeBadge(src.type)}</div>
                    </div>
                    <button class="source-item-attach source-group-remove-member" data-source-id="${DOMPurify.sanitize(src.id)}" title="Remove from group">&times;</button>
                </div>
            `).join('');
        }

        detailEl.innerHTML = `
            <div class="source-detail-header">
                <button class="source-back-btn" id="group-back-btn" title="Back to groups">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
                </button>
                <div class="source-detail-title-row">
                    <h3 class="source-detail-title">${DOMPurify.sanitize(group.name)}</h3>
                    <span class="source-type-badge source-type-group">group</span>
                </div>
            </div>
            ${group.description ? `<div class="source-detail-info"><span>${DOMPurify.sanitize(group.description)}</span></div>` : ''}
            <div class="source-detail-actions">
                <button class="btn-modal-save source-attach-btn" id="group-attach-btn">${isAttached ? 'Detach from chat' : 'Attach to chat'}</button>
                <button class="btn-modal-save" id="group-add-source-btn">Add source</button>
                <button class="btn-modal-cancel source-delete-btn" id="group-delete-btn">Delete group</button>
            </div>
            <div class="source-group-members-label">Members (${members.length})</div>
            <div class="source-group-members" id="source-group-members">${membersHtml}</div>
        `;

        // Back button
        document.getElementById('group-back-btn').addEventListener('click', () => {
            detailEl.style.display = 'none';
            document.getElementById('sources-groups-list').style.display = '';
            _currentGroup = null;
            _refreshGroups();
        });

        // Attach/detach
        document.getElementById('group-attach-btn').addEventListener('click', () => {
            if (isAttached) {
                _attachedGroup = null;
            } else {
                _attachedGroup = { id: group.id, name: group.name };
            }
            _renderAttachedBar();
            _renderGroupDetail(group, members);
        });

        // Add source to group
        document.getElementById('group-add-source-btn').addEventListener('click', () => {
            _showAddSourceToGroupPicker(group, members);
        });

        // Delete group
        document.getElementById('group-delete-btn').addEventListener('click', async () => {
            if (!confirm('Delete this group? Sources in the group will not be deleted.')) return;
            try {
                await App.api(`/api/source-groups/${encodeURIComponent(group.id)}`, { method: 'DELETE' });
                if (_attachedGroup && _attachedGroup.id === group.id) {
                    _attachedGroup = null;
                    _renderAttachedBar();
                }
                detailEl.style.display = 'none';
                document.getElementById('sources-groups-list').style.display = '';
                _refreshGroups();
            } catch (err) {
                alert('Failed to delete group: ' + err.message);
            }
        });

        // Remove member buttons
        detailEl.querySelectorAll('.source-group-remove-member').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const sourceId = btn.dataset.sourceId;
                try {
                    await App.api(`/api/source-groups/${encodeURIComponent(group.id)}/sources/${encodeURIComponent(sourceId)}`, { method: 'DELETE' });
                    _showGroupDetail(group.id);
                } catch (err) {
                    alert('Failed to remove: ' + err.message);
                }
            });
        });
    }

    async function _showAddSourceToGroupPicker(group, currentMembers) {
        const membersEl = document.getElementById('source-group-members');
        if (!membersEl) return;

        const memberIds = new Set(currentMembers.map(s => s.id));

        try {
            const data = await App.api('/api/sources?limit=100');
            const allSources = (data.sources || []).filter(s => !memberIds.has(s.id));

            if (allSources.length === 0) {
                alert('No more sources to add.');
                return;
            }

            const picker = document.createElement('div');
            picker.className = 'source-tag-picker';
            picker.innerHTML = '<div style="padding:4px 8px;opacity:0.6;font-size:12px">Pick a source to add:</div>';

            allSources.slice(0, 20).forEach(src => {
                const btn = document.createElement('button');
                btn.className = 'source-tag-picker-item';
                btn.textContent = src.title;
                btn.addEventListener('click', async () => {
                    try {
                        await App.api(`/api/source-groups/${encodeURIComponent(group.id)}/sources/${encodeURIComponent(src.id)}`, { method: 'POST' });
                        _showGroupDetail(group.id);
                    } catch (err) {
                        alert('Failed to add: ' + err.message);
                    }
                });
                picker.appendChild(btn);
            });

            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'source-tag-picker-cancel';
            cancelBtn.textContent = 'Cancel';
            cancelBtn.addEventListener('click', () => _showGroupDetail(group.id));
            picker.appendChild(cancelBtn);

            membersEl.innerHTML = '';
            membersEl.appendChild(picker);
        } catch (err) {
            alert('Failed to load sources: ' + err.message);
        }
    }

    async function _showTagAttachPicker() {
        const listEl = document.getElementById('sources-list');
        const btn = document.getElementById('sources-attach-tag-btn');
        if (!btn) return;

        let allTags = [];
        try {
            allTags = await App.api('/api/tags');
        } catch { return; }

        if (allTags.length === 0) {
            alert('No tags found. Tag some sources first.');
            return;
        }

        const picker = document.createElement('div');
        picker.className = 'source-tag-picker';
        allTags.forEach(tag => {
            const tagBtn = document.createElement('button');
            tagBtn.className = 'source-tag-picker-item';
            tagBtn.textContent = tag.name;
            tagBtn.addEventListener('click', () => {
                _attachedTag = { id: tag.id, name: tag.name };
                _renderAttachedBar();
                _renderList();
            });
            picker.appendChild(tagBtn);
        });

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'source-tag-picker-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => _renderList());
        picker.appendChild(cancelBtn);

        btn.replaceWith(picker);
    }

    async function _createGroup() {
        const name = prompt('Group name:');
        if (!name || !name.trim()) return;
        const desc = prompt('Description (optional):') || '';

        try {
            await App.api('/api/source-groups', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim(), description: desc.trim() }),
            });
            _refreshGroups();
        } catch (err) {
            alert('Failed to create group: ' + err.message);
        }
    }

    // --- Attached sources bar (shown above input area) ---

    function _renderAttachedBar() {
        let bar = document.getElementById('sources-attached-bar');
        const inputArea = document.getElementById('input-area');
        if (!inputArea) return;

        const hasRefs = _attachedSources.length > 0 || _attachedTag || _attachedGroup;

        if (!hasRefs) {
            if (bar) bar.remove();
            return;
        }

        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'sources-attached-bar';
            bar.className = 'sources-attached-bar';
            inputArea.insertBefore(bar, inputArea.firstChild);
        }

        bar.innerHTML = '';
        _attachedSources.forEach(src => {
            const chip = document.createElement('span');
            chip.className = 'source-ref-chip';
            chip.innerHTML = `<span class="source-ref-icon">${_typeIcon(src.type)}</span>${DOMPurify.sanitize(src.title)}<button class="source-ref-remove" title="Remove">&times;</button>`;
            chip.querySelector('.source-ref-remove').addEventListener('click', () => {
                _attachedSources = _attachedSources.filter(s => s.id !== src.id);
                _renderAttachedBar();
                if (_currentView === 'list') _renderList();
            });
            bar.appendChild(chip);
        });

        if (_attachedTag) {
            const chip = document.createElement('span');
            chip.className = 'source-ref-chip source-ref-tag';
            chip.innerHTML = `tag: ${DOMPurify.sanitize(_attachedTag.name)}<button class="source-ref-remove" title="Remove">&times;</button>`;
            chip.querySelector('.source-ref-remove').addEventListener('click', () => {
                _attachedTag = null;
                _renderAttachedBar();
            });
            bar.appendChild(chip);
        }

        if (_attachedGroup) {
            const chip = document.createElement('span');
            chip.className = 'source-ref-chip source-ref-group';
            chip.innerHTML = `group: ${DOMPurify.sanitize(_attachedGroup.name)}<button class="source-ref-remove" title="Remove">&times;</button>`;
            chip.querySelector('.source-ref-remove').addEventListener('click', () => {
                _attachedGroup = null;
                _renderAttachedBar();
            });
            bar.appendChild(chip);
        }
    }

    function _typeIcon(type) {
        if (type === 'text') return '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/></svg>';
        if (type === 'file') return '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/></svg>';
        if (type === 'url') return '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>';
        return '';
    }

    // --- Public API for Chat.js ---

    function getAttachedSources() {
        return {
            source_ids: _attachedSources.map(s => s.id),
            source_tag: _attachedTag ? _attachedTag.id : null,
            source_group_id: _attachedGroup ? _attachedGroup.id : null,
        };
    }

    function clearAttached() {
        _attachedSources = [];
        _attachedTag = null;
        _attachedGroup = null;
        _renderAttachedBar();
    }

    function hasAttached() {
        return _attachedSources.length > 0 || _attachedTag !== null || _attachedGroup !== null;
    }

    function attachTag(tag) {
        _attachedTag = tag;
        _renderAttachedBar();
    }

    function attachGroup(group) {
        _attachedGroup = group;
        _renderAttachedBar();
    }

    return {
        init, togglePanel, openPanel, closePanel,
        refreshList, showCreateView, showDetailView,
        getAttachedSources, clearAttached, hasAttached,
        attachTag, attachGroup,
    };
})();
