/* App state management, initialization, theme system */

const App = (() => {
    const state = {
        currentConversationId: null,
        currentProjectId: null,
        currentDatabase: null,
        isStreaming: false,
        availableModels: [],
        databases: [],
        clientId: crypto.randomUUID(),
    };

    let _eventSource = null;

    // --- Theme System ---

    const THEMES = {
        midnight: { label: 'Midnight', colors: ['#0f1117', '#1a1d27', '#3b82f6'] },
        dawn:     { label: 'Dawn',     colors: ['#faf8f5', '#f2efe9', '#7c5cbf'] },
        aurora:   { label: 'Aurora',    colors: ['#090b10', '#12151e', '#7c3aed'] },
        ember:    { label: 'Ember',     colors: ['#171210', '#211a16', '#e8913a'] },
    };

    function _migrateLocalStorage() {
        const migrations = [
            ['parlor_theme', 'anteroom_theme'],
            ['parlor_stream_raw_mode', 'anteroom_stream_raw_mode'],
        ];
        migrations.forEach(([oldKey, newKey]) => {
            if (localStorage.getItem(oldKey) !== null && localStorage.getItem(newKey) === null) {
                localStorage.setItem(newKey, localStorage.getItem(oldKey));
                localStorage.removeItem(oldKey);
            }
        });
    }

    function getTheme() {
        return localStorage.getItem('anteroom_theme') || 'midnight';
    }

    function setTheme(name) {
        if (!THEMES[name]) return;
        document.documentElement.setAttribute('data-theme', name);
        localStorage.setItem('anteroom_theme', name);
        _updateThemePicker();
    }

    function _updateThemePicker() {
        const picker = document.getElementById('theme-picker');
        if (!picker) return;
        const current = getTheme();
        picker.querySelectorAll('.theme-card').forEach(card => {
            card.classList.toggle('active', card.dataset.theme === current);
        });
    }

    function _renderThemePicker() {
        const picker = document.getElementById('theme-picker');
        if (!picker) return;
        picker.innerHTML = '';
        const current = getTheme();

        Object.entries(THEMES).forEach(([key, theme]) => {
            const card = document.createElement('div');
            card.className = 'theme-card' + (key === current ? ' active' : '');
            card.dataset.theme = key;

            const preview = document.createElement('div');
            preview.className = 'theme-preview';
            theme.colors.forEach(color => {
                const swatch = document.createElement('span');
                swatch.style.background = color;
                preview.appendChild(swatch);
            });

            const label = document.createElement('span');
            label.className = 'theme-card-label';
            label.textContent = theme.label;

            card.appendChild(preview);
            card.appendChild(label);
            card.addEventListener('click', () => setTheme(key));
            picker.appendChild(card);
        });
    }

    // --- Mobile Sidebar ---

    function _initMobileSidebar() {
        const menuBtn = document.getElementById('mobile-menu-btn');
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');

        if (menuBtn) {
            menuBtn.addEventListener('click', () => {
                sidebar.classList.toggle('open');
                overlay.classList.toggle('active', sidebar.classList.contains('open'));
            });
        }

        if (overlay) {
            overlay.addEventListener('click', () => {
                sidebar.classList.remove('open');
                overlay.classList.remove('active');
            });
        }
    }

    // --- CSRF & API ---

    function _getCsrfToken() {
        const match = document.cookie.split('; ').find(c => c.startsWith('anteroom_csrf='));
        return match ? match.split('=')[1] : '';
    }

    async function api(url, options = {}) {
        if (state.currentDatabase) {
            const sep = url.includes('?') ? '&' : '?';
            url += `${sep}db=${encodeURIComponent(state.currentDatabase)}`;
        }
        options.credentials = 'same-origin';
        if (!options.headers) options.headers = {};
        options.headers['X-Client-Id'] = state.clientId;
        if (['POST', 'PATCH', 'PUT', 'DELETE'].includes((options.method || '').toUpperCase())) {
            options.headers['X-CSRF-Token'] = _getCsrfToken();
        }
        const response = await fetch(url, options);
        if (response.status === 401) {
            window.location.reload();
            throw new Error('Session expired');
        }
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        if (response.status === 204) return null;
        const ct = response.headers.get('content-type') || '';
        if (ct.includes('application/json')) {
            return response.json();
        }
        return response;
    }

    // --- Init ---

    async function init() {
        // Approvals modal hook (if loaded)
        if (window.Approvals && typeof window.Approvals.showApprovalModal === 'function') {
            state._showApprovalModal = window.Approvals.showApprovalModal;
        }
        _migrateLocalStorage();
        Chat.init();
        Sidebar.init();
        Palette.init();
        Attachments.init();
        initRawToggle();
        _initMobileSidebar();
        _renderThemePicker();

        // Offline detection
        const offlineBanner = document.getElementById('offline-banner');
        window.addEventListener('offline', () => {
            if (offlineBanner) offlineBanner.style.display = '';
        });
        window.addEventListener('online', () => {
            if (offlineBanner) offlineBanner.style.display = 'none';
        });
        if (!navigator.onLine && offlineBanner) {
            offlineBanner.style.display = '';
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'N') {
                e.preventDefault();
                newConversation();
            }
            if (e.key === 'Escape' && state.isStreaming) {
                Chat.stopGeneration();
            }
        });

        // Settings modal
        initSettings();

        // Project modal
        initProjectModal();

        // MCP modal
        _initMcpModal();

        // Check config status and cache available models
        try {
            const config = await api('/api/config');
            if (!config.ai || !config.ai.base_url) {
                document.getElementById('setup-banner').style.display = '';
            }
            if (config.mcp_servers && config.mcp_servers.length > 0) {
                const connected = config.mcp_servers.filter(s => s.status === 'connected');
                const totalTools = connected.reduce((sum, s) => sum + s.tool_count, 0);
                document.getElementById('mcp-status').textContent =
                    `${totalTools} tools / ${connected.length} servers`;
            }
            if (config.identity && config.identity.user_id) {
                App.state.localUserId = config.identity.user_id;
            }
        } catch {
            // Config endpoint may not exist yet
        }

        // Fetch available models for all model selectors
        await refreshModels();

        initModelSelector();

        // Load databases
        _initDbModal();
        await loadDatabases();
        document.getElementById('btn-db-add').addEventListener('click', addDatabase);

        // Load projects
        await loadProjects();

        // Read URL params for shared DB links
        const urlParams = _readUrlParams();

        // Load conversations
        await Sidebar.refresh();

        // Load conversation from URL param, or most recent
        if (urlParams.conversationId) {
            try {
                await loadConversation(urlParams.conversationId);
            } catch {
                const conversations = await api('/api/conversations');
                if (conversations && conversations.length > 0) {
                    await loadConversation(conversations[0].id);
                }
            }
        } else {
            const conversations = await api('/api/conversations');
            if (conversations && conversations.length > 0) {
                await loadConversation(conversations[0].id);
            } else {
                _connectEventSource();
            }
        }
    }

    function initRawToggle() {
        const topbar = document.getElementById('chat-topbar');
        const btn = document.createElement('button');
        btn.className = 'btn-raw-toggle' + (Chat.isRawMode() ? ' active' : '');
        btn.title = 'Toggle raw text during streaming';
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg><span>Raw</span>';
        btn.addEventListener('click', () => {
            const newVal = !Chat.isRawMode();
            Chat.setRawMode(newVal);
            btn.classList.toggle('active', newVal);
        });
        topbar.appendChild(btn);
    }

    let _currentModel = '';

    function initModelSelector() {
        const btn = document.getElementById('model-selector-btn');
        const dropdown = document.getElementById('model-selector-dropdown');

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = dropdown.classList.contains('open');
            dropdown.classList.toggle('open');
            btn.classList.toggle('active');
            if (!isOpen) _renderModelDropdown();
        });

        document.addEventListener('click', () => {
            dropdown.classList.remove('open');
            btn.classList.remove('active');
        });

        dropdown.addEventListener('click', (e) => e.stopPropagation());
    }

    function _renderModelDropdown() {
        const dropdown = document.getElementById('model-selector-dropdown');
        dropdown.innerHTML = '';

        const defaultItem = document.createElement('div');
        defaultItem.className = 'model-selector-item' + (!_currentModel ? ' selected' : '');
        defaultItem.textContent = 'Default model';
        defaultItem.addEventListener('click', () => _selectModel(''));
        dropdown.appendChild(defaultItem);

        state.availableModels.forEach(m => {
            const item = document.createElement('div');
            item.className = 'model-selector-item' + (m === _currentModel ? ' selected' : '');
            item.textContent = m;
            item.addEventListener('click', () => _selectModel(m));
            dropdown.appendChild(item);
        });
    }

    async function _selectModel(model) {
        _currentModel = model;
        document.getElementById('model-selector-label').textContent = model || 'Default model';
        document.getElementById('model-selector-dropdown').classList.remove('open');
        document.getElementById('model-selector-btn').classList.remove('active');

        if (!state.currentConversationId) return;
        try {
            await api(`/api/conversations/${state.currentConversationId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model }),
            });
        } catch {
            // ignore
        }
    }

    async function refreshModels() {
        try {
            const models = await api('/api/models');
            if (Array.isArray(models) && models.length > 0) {
                state.availableModels = models;
                return;
            }
        } catch { /* fall through */ }
        // Fallback: try the validate endpoint
        try {
            const validation = await api('/api/config/validate', { method: 'POST' });
            if (validation.models && validation.models.length > 0) {
                state.availableModels = validation.models.sort();
            }
        } catch { /* keep existing list */ }
    }

    function _populateModelSelect(selectEl, selectedValue, includeDefault) {
        selectEl.innerHTML = '';
        if (includeDefault) {
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = 'Use global default';
            selectEl.appendChild(defaultOpt);
        }
        if (state.availableModels.length === 0) {
            if (!includeDefault) {
                const opt = document.createElement('option');
                opt.value = selectedValue || '';
                opt.textContent = selectedValue || 'No models available';
                selectEl.appendChild(opt);
            }
        } else {
            state.availableModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                selectEl.appendChild(opt);
            });
        }
        selectEl.value = selectedValue || '';
    }

    async function newConversation() {
        const opts = { method: 'POST' };
        if (state.currentProjectId) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify({ project_id: state.currentProjectId });
        }
        const conv = await api('/api/conversations', opts);
        state.currentConversationId = conv.id;
        Chat.loadMessages([]);
        _currentModel = '';
        document.getElementById('model-selector-label').textContent = 'Default model';
        await Sidebar.refresh();
        Sidebar.setActive(conv.id);
        _updateUrl();
        _connectEventSource();
        document.getElementById('message-input').focus();
    }

    async function loadConversation(id) {
        state.currentConversationId = id;
        const detail = await api(`/api/conversations/${id}`);
        Chat.loadMessages(detail.messages || []);
        Sidebar.setActive(id);
        _currentModel = detail.model || '';
        document.getElementById('model-selector-label').textContent = _currentModel || 'Default model';
        _updateUrl();
        _connectEventSource();
    }

    // --- URL Params ---

    function _readUrlParams() {
        const params = new URLSearchParams(window.location.search);
        const db = params.get('db');
        const c = params.get('c');
        if (db) state.currentDatabase = db;
        return { db, conversationId: c };
    }

    function _updateUrl() {
        const params = new URLSearchParams();
        if (state.currentDatabase) params.set('db', state.currentDatabase);
        if (state.currentConversationId) params.set('c', state.currentConversationId);
        const qs = params.toString();
        const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
        if (newUrl !== window.location.pathname + window.location.search) {
            window.history.replaceState(null, '', newUrl);
        }
    }

    // --- Real-time Event Source ---

    function _connectEventSource() {
        if (_eventSource) {
            _eventSource.close();
            _eventSource = null;
        }

        const db = state.currentDatabase || 'personal';
        let url = `/api/events?db=${encodeURIComponent(db)}&client_id=${encodeURIComponent(state.clientId)}`;
        if (state.currentConversationId) {
            url += `&conversation_id=${encodeURIComponent(state.currentConversationId)}`;
        }

        _eventSource = new EventSource(url);

        _eventSource.addEventListener('new_message', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            if (data.conversation_id === state.currentConversationId) {
                Chat.appendRemoteMessage(data.role, data.content);
            }
        });

        _eventSource.addEventListener('stream_start', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            if (data.conversation_id === state.currentConversationId) {
                Chat.startRemoteStream();
            }
        });

        _eventSource.addEventListener('stream_token', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            if (data.conversation_id === state.currentConversationId) {
                Chat.handleRemoteToken(data.content);
            }
        });

        _eventSource.addEventListener('stream_done', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            if (data.conversation_id === state.currentConversationId) {
                Chat.finalizeRemoteStream();
            }
        });

        _eventSource.addEventListener('title_changed', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            Sidebar.updateTitle(data.conversation_id, data.title);
        });

        _eventSource.addEventListener('conversation_created', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            Sidebar.refresh();
        });

        _eventSource.addEventListener('conversation_deleted', (e) => {
            const data = JSON.parse(e.data);
            if (data.client_id === state.clientId) return;
            if (data.conversation_id === state.currentConversationId) {
                state.currentConversationId = null;
                Chat.loadMessages([]);
            }
            Sidebar.refresh();
        });

        _eventSource.addEventListener('destructive_approval_requested', (e) => {
            const data = JSON.parse(e.data);
            // Only show if modal hook exists
            if (state._showApprovalModal) {
                state._showApprovalModal(data);
            } else {
                // Fallback: native confirm
                const ok = window.confirm(`${data.message}\n\nProceed?`);
                api('/api/approvals/respond', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ approval_id: data.approval_id, approved: ok })
                }).catch(() => {});
            }
        });

        _eventSource.onerror = () => {
            // Reconnect after a delay
            setTimeout(() => {
                if (_eventSource && _eventSource.readyState === EventSource.CLOSED) {
                    _connectEventSource();
                }
            }, 3000);
        };
    }

    function formatTimestamp(iso) {
        const d = new Date(iso);
        const now = new Date();
        const diff = now - d;
        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
        return d.toLocaleDateString();
    }

    function initSettings() {
        const modal = document.getElementById('settings-modal');
        const openBtn = document.getElementById('btn-settings');
        const closeBtn = document.getElementById('settings-close');
        const cancelBtn = document.getElementById('settings-cancel');
        const saveBtn = document.getElementById('settings-save');

        openBtn.addEventListener('click', openSettings);
        closeBtn.addEventListener('click', () => modal.style.display = 'none');
        cancelBtn.addEventListener('click', () => modal.style.display = 'none');
        saveBtn.addEventListener('click', saveSettings);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.style.display = 'none';
        });
    }

    async function openSettings() {
        const modal = document.getElementById('settings-modal');
        const modelSelect = document.getElementById('setting-model');
        const promptTextarea = document.getElementById('setting-system-prompt');

        _renderThemePicker();

        try {
            const config = await api('/api/config');
            promptTextarea.value = config.ai.system_prompt || '';

            modal.style.display = 'flex';
            await refreshModels();
            _populateModelSelect(modelSelect, config.ai.model || '', false);
        } catch (e) {
            modal.style.display = 'flex';
        }
    }

    async function saveSettings() {
        const model = document.getElementById('setting-model').value;
        const systemPrompt = document.getElementById('setting-system-prompt').value;

        try {
            await api('/api/config', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model, system_prompt: systemPrompt }),
            });
            document.getElementById('settings-modal').style.display = 'none';
        } catch (e) {
            alert('Failed to save settings: ' + e.message);
        }
    }

    // --- Projects ---

    let _projectsLoaded = false;

    async function loadProjects() {
        const list = document.getElementById('project-list');
        const activeBar = document.getElementById('project-active-bar');
        const activeName = document.getElementById('project-active-name');
        const select = document.getElementById('project-select');

        let projects = [];
        try {
            projects = await api('/api/projects');
            while (select.options.length > 1) select.remove(1);
            projects.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                select.appendChild(opt);
            });
            if (state.currentProjectId) {
                select.value = state.currentProjectId;
            }
        } catch {
            // ignore
        }

        _renderProjectList(projects);

        if (!_projectsLoaded) {
            _projectsLoaded = true;
            document.getElementById('btn-project-add').addEventListener('click', () => openProjectModal());
            document.getElementById('btn-project-edit').addEventListener('click', () => {
                if (state.currentProjectId) openProjectModal(state.currentProjectId);
            });
            document.getElementById('btn-project-delete').addEventListener('click', async () => {
                if (!state.currentProjectId) return;
                if (!confirm('Delete this project? Conversations will be kept but unlinked.')) return;
                try {
                    await api(`/api/projects/${state.currentProjectId}`, { method: 'DELETE' });
                    state.currentProjectId = null;
                    select.value = '';
                    await loadProjects();
                    await Sidebar.refresh();
                } catch { /* ignore */ }
            });
            document.getElementById('btn-project-clear').addEventListener('click', async () => {
                state.currentProjectId = null;
                select.value = '';
                await loadProjects();
                await Sidebar.refresh();
                state.currentConversationId = null;
                Chat.loadMessages([]);
            });
        }
    }

    function _renderProjectList(projects) {
        const list = document.getElementById('project-list');
        const activeBar = document.getElementById('project-active-bar');
        const activeName = document.getElementById('project-active-name');
        list.innerHTML = '';

        const folderSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>';

        // "All" item
        const allItem = document.createElement('div');
        allItem.className = 'project-item project-all' + (!state.currentProjectId ? ' active' : '');
        allItem.innerHTML = '<span class="project-item-name">All Conversations</span>';
        allItem.addEventListener('click', async () => {
            state.currentProjectId = null;
            document.getElementById('project-select').value = '';
            _renderProjectList(projects);
            await Sidebar.refresh();
            state.currentConversationId = null;
            Chat.loadMessages([]);
        });
        list.appendChild(allItem);

        if (projects.length === 0) {
            activeBar.style.display = 'none';
            return;
        }

        projects.forEach(p => {
            const item = document.createElement('div');
            item.className = 'project-item' + (state.currentProjectId === p.id ? ' active' : '');
            item.innerHTML = `<span class="project-item-icon">${folderSvg}</span><span class="project-item-name">${DOMPurify.sanitize(p.name)}</span>`;
            item.addEventListener('click', async () => {
                state.currentProjectId = p.id;
                document.getElementById('project-select').value = p.id;
                _renderProjectList(projects);
                await Sidebar.refresh();
                state.currentConversationId = null;
                Chat.loadMessages([]);
            });
            list.appendChild(item);
        });

        // Update active bar
        if (state.currentProjectId) {
            const active = projects.find(p => p.id === state.currentProjectId);
            if (active) {
                activeName.textContent = active.name;
                activeBar.style.display = 'flex';
            } else {
                activeBar.style.display = 'none';
            }
        } else {
            activeBar.style.display = 'none';
        }
    }

    function initProjectModal() {
        const modal = document.getElementById('project-modal');
        const closeBtn = document.getElementById('project-close');
        const cancelBtn = document.getElementById('project-cancel');
        const saveBtn = document.getElementById('project-save');

        closeBtn.addEventListener('click', () => modal.style.display = 'none');
        cancelBtn.addEventListener('click', () => modal.style.display = 'none');
        saveBtn.addEventListener('click', saveProject);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) modal.style.display = 'none';
        });
    }

    let _editingProjectId = null;

    async function openProjectModal(projectId) {
        const modal = document.getElementById('project-modal');
        const titleEl = document.getElementById('project-modal-title');
        const nameInput = document.getElementById('project-name');
        const instructionsInput = document.getElementById('project-instructions');
        const modelSelect = document.getElementById('project-model');

        _editingProjectId = projectId || null;
        let currentModel = '';

        if (projectId) {
            titleEl.textContent = 'Edit Project';
            try {
                const proj = await api(`/api/projects/${projectId}`);
                nameInput.value = proj.name || '';
                instructionsInput.value = proj.instructions || '';
                currentModel = proj.model || '';
            } catch {
                nameInput.value = '';
                instructionsInput.value = '';
            }
        } else {
            titleEl.textContent = 'New Project';
            nameInput.value = '';
            instructionsInput.value = '';
        }

        modal.style.display = 'flex';
        nameInput.focus();

        await refreshModels();
        _populateModelSelect(modelSelect, currentModel, true);
    }

    async function saveProject() {
        const name = document.getElementById('project-name').value.trim();
        const instructions = document.getElementById('project-instructions').value;
        const model = document.getElementById('project-model').value.trim();

        if (!name) {
            alert('Project name is required.');
            return;
        }

        try {
            const payload = { name, instructions, model: model || null };
            if (_editingProjectId) {
                await api(`/api/projects/${_editingProjectId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } else {
                const created = await api('/api/projects', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                state.currentProjectId = created.id;
            }
            document.getElementById('project-modal').style.display = 'none';
            await loadProjects();
            await Sidebar.refresh();
        } catch (e) {
            alert('Failed to save project: ' + e.message);
        }
    }

    // --- Databases ---

    async function loadDatabases() {
        try {
            // Fetch without db param so we always get the full list
            const response = await fetch('/api/databases', { credentials: 'same-origin' });
            if (response.ok) {
                state.databases = await response.json();
            }
        } catch { /* ignore */ }
        _renderDatabaseList();
    }

    function _renderDatabaseList() {
        const list = document.getElementById('db-list');
        if (!list) return;
        list.innerHTML = '';

        const dbSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>';

        state.databases.forEach(db => {
            const item = document.createElement('div');
            const isActive = (db.name === 'personal' && !state.currentDatabase) ||
                             db.name === state.currentDatabase;
            item.className = 'db-item' + (isActive ? ' active' : '');
            item.innerHTML = `<span class="db-item-icon">${dbSvg}</span><span class="db-item-name">${DOMPurify.sanitize(db.name)}</span>`;
            if (db.name !== 'personal') {
                const removeBtn = document.createElement('button');
                removeBtn.className = 'db-item-remove';
                removeBtn.title = 'Remove database';
                removeBtn.innerHTML = '&times;';
                removeBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    if (!confirm(`Remove database "${db.name}"? The database file will not be deleted.`)) return;
                    try {
                        await fetch(`/api/databases/${encodeURIComponent(db.name)}`, {
                            method: 'DELETE',
                            credentials: 'same-origin',
                            headers: { 'X-CSRF-Token': _getCsrfToken() },
                        });
                        if (state.currentDatabase === db.name) {
                            state.currentDatabase = null;
                        }
                        await loadDatabases();
                        await Sidebar.refresh();
                    } catch { /* ignore */ }
                });
                item.appendChild(removeBtn);
            }
            item.addEventListener('click', async () => {
                state.currentDatabase = db.name === 'personal' ? null : db.name;
                state.currentConversationId = null;
                Chat.loadMessages([]);
                _renderDatabaseList();
                _updateUrl();
                _connectEventSource();
                await Sidebar.refresh();
            });
            list.appendChild(item);
        });
    }

    function addDatabase() {
        const modal = document.getElementById('db-modal');
        const nameInput = document.getElementById('db-name-input');
        const pathInput = document.getElementById('db-path-input');
        const panel = document.getElementById('browse-panel');

        nameInput.value = '';
        pathInput.value = '';
        panel.style.display = 'none';
        modal.style.display = 'flex';
        nameInput.focus();
    }

    function _initDbModal() {
        const modal = document.getElementById('db-modal');
        const closeBtn = document.getElementById('db-modal-close');
        const cancelBtn = document.getElementById('db-modal-cancel');
        const saveBtn = document.getElementById('db-modal-save');
        const browseBtn = document.getElementById('btn-browse');

        const closeModal = () => { modal.style.display = 'none'; };
        closeBtn.addEventListener('click', closeModal);
        cancelBtn.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

        browseBtn.addEventListener('click', () => {
            const panel = document.getElementById('browse-panel');
            if (panel.style.display === 'none') {
                panel.style.display = 'flex';
                const current = document.getElementById('db-path-input').value.trim();
                _browseTo(current ? _parentDir(current) : '~');
            } else {
                panel.style.display = 'none';
            }
        });

        saveBtn.addEventListener('click', async () => {
            const name = document.getElementById('db-name-input').value.trim();
            const path = document.getElementById('db-path-input').value.trim();
            if (!name) { alert('Database name is required.'); return; }
            if (!path) { alert('Database path is required.'); return; }
            try {
                await fetch('/api/databases', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': _getCsrfToken(),
                    },
                    body: JSON.stringify({ name, path }),
                });
                modal.style.display = 'none';
                await loadDatabases();
            } catch (e) {
                alert('Failed to add database: ' + e.message);
            }
        });
    }

    function _parentDir(path) {
        const parts = path.replace(/\\/g, '/').split('/');
        parts.pop();
        return parts.join('/') || '/';
    }

    async function _browseTo(dir) {
        const currentEl = document.getElementById('browse-current');
        const entriesEl = document.getElementById('browse-entries');

        currentEl.textContent = 'Loading...';
        entriesEl.innerHTML = '';

        try {
            const data = await api(`/api/browse?path=${encodeURIComponent(dir)}`);
            currentEl.textContent = data.current;

            if (data.parent) {
                const upEl = document.createElement('div');
                upEl.className = 'browse-entry browse-up';
                upEl.innerHTML = '<span class="browse-entry-icon">\u2190</span><span class="browse-entry-name">..</span>';
                upEl.addEventListener('click', () => _browseTo(data.parent));
                entriesEl.appendChild(upEl);
            }

            data.entries.forEach(entry => {
                const el = document.createElement('div');
                el.className = 'browse-entry ' + (entry.type === 'dir' ? 'is-dir' : 'is-file');

                const icon = entry.type === 'dir'
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>'
                    : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>';

                el.innerHTML = `<span class="browse-entry-icon">${icon}</span><span class="browse-entry-name">${DOMPurify.sanitize(entry.name)}</span>`;

                el.addEventListener('click', () => {
                    const fullPath = data.current + (data.current.endsWith('/') ? '' : '/') + entry.name;
                    if (entry.type === 'dir') {
                        _browseTo(fullPath);
                    } else {
                        document.getElementById('db-path-input').value = fullPath;
                        document.getElementById('browse-panel').style.display = 'none';
                    }
                });
                entriesEl.appendChild(el);
            });

            if (data.entries.length === 0 && !data.parent) {
                const empty = document.createElement('div');
                empty.className = 'browse-entry';
                empty.innerHTML = '<span class="browse-entry-name" style="color:var(--text-muted)">Empty directory</span>';
                entriesEl.appendChild(empty);
            }
        } catch (e) {
            currentEl.textContent = 'Error: ' + e.message;
        }
    }

    // --- MCP Modal ---

    function _initMcpModal() {
        const modal = document.getElementById('mcp-modal');
        const closeBtn = document.getElementById('mcp-modal-close');
        const statusEl = document.getElementById('mcp-status');

        if (closeBtn) {
            closeBtn.addEventListener('click', () => { modal.style.display = 'none'; });
        }
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) modal.style.display = 'none';
            });
        }
        if (statusEl) {
            statusEl.style.cursor = 'pointer';
            statusEl.addEventListener('click', openMcpModal);
        }
    }

    async function openMcpModal() {
        const modal = document.getElementById('mcp-modal');
        const listEl = document.getElementById('mcp-server-list');
        listEl.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px">Loading...</div>';
        modal.style.display = 'flex';

        try {
            const config = await api('/api/config');
            const servers = config.mcp_servers || [];
            if (servers.length === 0) {
                listEl.innerHTML = '<div style="color:var(--text-muted);text-align:center;padding:20px">No MCP servers configured.</div>';
                return;
            }
            _renderMcpServers(servers);
        } catch (e) {
            listEl.innerHTML = `<div style="color:var(--error);text-align:center;padding:20px">Failed to load: ${DOMPurify.sanitize(e.message)}</div>`;
        }
    }

    function _renderMcpServers(servers) {
        const listEl = document.getElementById('mcp-server-list');
        listEl.innerHTML = '';

        servers.forEach(srv => {
            const row = document.createElement('div');
            row.className = 'mcp-server-row';

            const info = document.createElement('div');
            info.className = 'mcp-server-info';

            const name = document.createElement('span');
            name.className = 'mcp-server-name';
            name.textContent = srv.name;

            const badge = document.createElement('span');
            badge.className = 'mcp-status-badge mcp-status-' + srv.status;
            badge.textContent = srv.status;

            const detail = document.createElement('span');
            detail.className = 'mcp-server-detail';
            let detailText = srv.transport;
            if (srv.status === 'connected') {
                detailText += ` \u2022 ${srv.tool_count} tool${srv.tool_count !== 1 ? 's' : ''}`;
            }
            if (srv.error_message) {
                detailText += ` \u2022 ${srv.error_message}`;
            }
            detail.textContent = detailText;

            info.appendChild(name);
            info.appendChild(badge);
            info.appendChild(detail);

            const actions = document.createElement('div');
            actions.className = 'mcp-server-actions';

            if (srv.status === 'connected') {
                const reconnectBtn = document.createElement('button');
                reconnectBtn.className = 'btn-mcp-action';
                reconnectBtn.textContent = 'Reconnect';
                reconnectBtn.addEventListener('click', () => _mcpAction(srv.name, 'reconnect'));
                actions.appendChild(reconnectBtn);

                const disconnectBtn = document.createElement('button');
                disconnectBtn.className = 'btn-mcp-action btn-mcp-danger';
                disconnectBtn.textContent = 'Disconnect';
                disconnectBtn.addEventListener('click', () => _mcpAction(srv.name, 'disconnect'));
                actions.appendChild(disconnectBtn);
            } else {
                const connectBtn = document.createElement('button');
                connectBtn.className = 'btn-mcp-action btn-mcp-connect';
                connectBtn.textContent = 'Connect';
                connectBtn.addEventListener('click', () => _mcpAction(srv.name, 'connect'));
                actions.appendChild(connectBtn);
            }

            row.appendChild(info);
            row.appendChild(actions);
            listEl.appendChild(row);
        });
    }

    async function _mcpAction(name, action) {
        try {
            await api(`/api/mcp/servers/${encodeURIComponent(name)}/${action}`, { method: 'POST' });
            await openMcpModal();
            await _refreshMcpStatus();
        } catch (e) {
            alert(`MCP ${action} failed: ${e.message}`);
        }
    }

    async function _refreshMcpStatus() {
        try {
            const config = await api('/api/config');
            const statusEl = document.getElementById('mcp-status');
            if (config.mcp_servers && config.mcp_servers.length > 0) {
                const connected = config.mcp_servers.filter(s => s.status === 'connected');
                const totalTools = connected.reduce((sum, s) => sum + s.tool_count, 0);
                statusEl.textContent = `${totalTools} tools / ${connected.length} servers`;
            } else {
                statusEl.textContent = '';
            }
        } catch { /* ignore */ }
    }

    document.addEventListener('DOMContentLoaded', init);

    return {
        state, api, _getCsrfToken, _selectModel, newConversation, loadConversation,
        loadProjects, loadDatabases, addDatabase, refreshModels, formatTimestamp,
        getTheme, setTheme, THEMES, openMcpModal,
    };
window.App = App;
})();
