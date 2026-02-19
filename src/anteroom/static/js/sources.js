/* Sources panel: global knowledge store management */

const Sources = (() => {
    let _sources = [];
    let _currentView = 'list'; // list | detail | create
    let _currentSource = null;
    let _searchTimeout = null;
    let _selectedFile = null;
    let _createType = 'text';

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
        if (addBtn) addBtn.addEventListener('click', showCreateView);
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
        document.getElementById('sources-list').style.display = '';
        document.getElementById('sources-detail').style.display = 'none';
        document.getElementById('sources-create').style.display = 'none';
        document.querySelector('.sources-toolbar').style.display = '';
    }

    function showCreateView() {
        _currentView = 'create';
        _selectedFile = null;
        _createType = 'text';
        document.getElementById('sources-list').style.display = 'none';
        document.getElementById('sources-detail').style.display = 'none';
        document.getElementById('sources-create').style.display = '';
        document.querySelector('.sources-toolbar').style.display = 'none';

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
        document.getElementById('sources-list').style.display = 'none';
        document.getElementById('sources-create').style.display = 'none';
        document.querySelector('.sources-toolbar').style.display = 'none';

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

        let contentHtml = '';
        if (source.content) {
            const sanitized = DOMPurify.sanitize(source.content);
            contentHtml = `<div class="source-detail-content"><pre>${sanitized}</pre></div>`;
        }
        if (source.url) {
            const safeUrl = DOMPurify.sanitize(source.url);
            contentHtml += `<div class="source-detail-url"><a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a></div>`;
        }
        if (source.filename) {
            contentHtml += `<div class="source-detail-meta">File: ${DOMPurify.sanitize(source.filename)}</div>`;
        }
        if (source.size_bytes) {
            contentHtml += `<div class="source-detail-meta">Size: ${_formatBytes(source.size_bytes)}</div>`;
        }

        const tagsHtml = (source.tags || []).map(t =>
            `<span class="source-tag">${DOMPurify.sanitize(t.name)}</span>`
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
            ${tagsHtml ? `<div class="source-detail-tags">${tagsHtml}</div>` : ''}
            ${contentHtml}
            <div class="source-detail-actions">
                <button class="btn-modal-cancel source-delete-btn" id="source-delete-btn">Delete</button>
            </div>
        `;

        document.getElementById('source-back-btn').addEventListener('click', async () => {
            showListView();
            await refreshList();
        });

        document.getElementById('source-delete-btn').addEventListener('click', async () => {
            if (!confirm('Delete this source? This cannot be undone.')) return;
            try {
                await App.api(`/api/sources/${encodeURIComponent(source.id)}`, { method: 'DELETE' });
                showListView();
                await refreshList();
            } catch (err) {
                alert('Failed to delete: ' + err.message);
            }
        });
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

        if (_sources.length === 0) {
            listEl.innerHTML = '<div class="sources-empty">No sources yet. Click + to add one.</div>';
            return;
        }

        _sources.forEach(source => {
            const item = document.createElement('div');
            item.className = 'source-item';
            item.dataset.id = source.id;

            const typeBadge = _typeBadge(source.type);
            const title = DOMPurify.sanitize(source.title || 'Untitled');
            const date = App.formatTimestamp(source.created_at);

            item.innerHTML = `
                <div class="source-item-main">
                    <div class="source-item-title">${title}</div>
                    <div class="source-item-meta">
                        ${typeBadge}
                        <span class="source-item-date">${date}</span>
                    </div>
                </div>
            `;

            item.addEventListener('click', () => showDetailView(source.id));
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

    return {
        init, togglePanel, openPanel, closePanel,
        refreshList, showCreateView, showDetailView,
    };
})();
