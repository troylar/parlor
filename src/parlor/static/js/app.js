/* App state management and initialization */

const App = (() => {
    const state = {
        currentConversationId: null,
        currentProjectId: null,
        isStreaming: false,
        availableModels: [],
    };

    function _getCsrfToken() {
        const match = document.cookie.split('; ').find(c => c.startsWith('parlor_csrf='));
        return match ? match.split('=')[1] : '';
    }

    async function api(url, options = {}) {
        options.credentials = 'same-origin';
        if (!options.headers) options.headers = {};
        if (['POST', 'PATCH', 'PUT', 'DELETE'].includes((options.method || '').toUpperCase())) {
            options.headers['X-CSRF-Token'] = _getCsrfToken();
        }
        const response = await fetch(url, options);
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

    async function init() {
        Chat.init();
        Sidebar.init();
        Attachments.init();

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
        } catch {
            // Config endpoint may not exist yet
        }

        // Fetch available models for model selector
        try {
            const validation = await api('/api/config/validate', { method: 'POST' });
            if (validation.models && validation.models.length > 0) {
                state.availableModels = validation.models.sort();
            }
        } catch {
            // Models not available
        }

        populateModelSelector();

        // Model selector change handler
        document.getElementById('model-select-chat').addEventListener('change', async (e) => {
            if (!state.currentConversationId) return;
            try {
                await api(`/api/conversations/${state.currentConversationId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: e.target.value }),
                });
            } catch {
                // ignore
            }
        });

        // Load projects
        await loadProjects();

        // Load conversations
        await Sidebar.refresh();

        // Load most recent conversation or show welcome
        const conversations = await api('/api/conversations');
        if (conversations && conversations.length > 0) {
            await loadConversation(conversations[0].id);
        }
    }

    function populateModelSelector() {
        const select = document.getElementById('model-select-chat');
        // Keep the default option, remove any others
        while (select.options.length > 1) select.remove(1);
        state.availableModels.forEach(m => {
            const opt = document.createElement('option');
            opt.value = m;
            opt.textContent = m;
            select.appendChild(opt);
        });
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
        document.getElementById('model-select-chat').value = '';
        await Sidebar.refresh();
        Sidebar.setActive(conv.id);
        document.getElementById('message-input').focus();
    }

    async function loadConversation(id) {
        state.currentConversationId = id;
        const detail = await api(`/api/conversations/${id}`);
        Chat.loadMessages(detail.messages || []);
        Sidebar.setActive(id);
        // Set model selector to conversation's model
        document.getElementById('model-select-chat').value = detail.model || '';
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
        const modelInput = document.getElementById('setting-model');
        const suggestionsEl = document.getElementById('model-suggestions');
        const promptTextarea = document.getElementById('setting-system-prompt');

        try {
            const config = await api('/api/config');
            promptTextarea.value = config.ai.system_prompt || '';
            modelInput.value = config.ai.model || '';

            modal.style.display = 'flex';
            suggestionsEl.innerHTML = '<span style="color:var(--text-muted);font-size:12px">Loading available models...</span>';

            const validation = await api('/api/config/validate', { method: 'POST' });
            suggestionsEl.innerHTML = '';

            if (validation.models && validation.models.length > 0) {
                validation.models.sort().forEach(m => {
                    const chip = document.createElement('span');
                    chip.className = 'model-chip' + (m === modelInput.value ? ' active' : '');
                    chip.textContent = m;
                    chip.addEventListener('click', () => {
                        modelInput.value = m;
                        suggestionsEl.querySelectorAll('.model-chip').forEach(c => c.classList.remove('active'));
                        chip.classList.add('active');
                    });
                    suggestionsEl.appendChild(chip);
                });
            }

            modelInput.addEventListener('input', () => {
                suggestionsEl.querySelectorAll('.model-chip').forEach(c => {
                    c.classList.toggle('active', c.textContent === modelInput.value);
                });
            });
        } catch (e) {
            modal.style.display = 'flex';
            suggestionsEl.innerHTML = '<span style="color:var(--text-muted);font-size:12px">Could not fetch models from API</span>';
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

    async function loadProjects() {
        const select = document.getElementById('project-select');
        const editBtn = document.getElementById('btn-project-edit');
        const deleteBtn = document.getElementById('btn-project-delete');

        try {
            const projects = await api('/api/projects');
            // Keep first option ("All Conversations"), remove the rest
            while (select.options.length > 1) select.remove(1);
            projects.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id;
                opt.textContent = p.name;
                select.appendChild(opt);
            });
            // Restore selection
            if (state.currentProjectId) {
                select.value = state.currentProjectId;
            }
        } catch {
            // ignore
        }

        // Show/hide edit + delete buttons based on selection
        const updateButtons = () => {
            const hasProject = !!select.value;
            editBtn.style.display = hasProject ? '' : 'none';
            deleteBtn.style.display = hasProject ? '' : 'none';
        };
        updateButtons();

        select.addEventListener('change', async () => {
            state.currentProjectId = select.value || null;
            updateButtons();
            await Sidebar.refresh();
            // If changing project, clear current conversation
            state.currentConversationId = null;
            Chat.loadMessages([]);
        });

        document.getElementById('btn-project-add').addEventListener('click', () => openProjectModal());
        editBtn.addEventListener('click', () => {
            if (state.currentProjectId) openProjectModal(state.currentProjectId);
        });
        deleteBtn.addEventListener('click', async () => {
            if (!state.currentProjectId) return;
            if (!confirm('Delete this project? Conversations will be kept but unlinked.')) return;
            try {
                await api(`/api/projects/${state.currentProjectId}`, { method: 'DELETE' });
                state.currentProjectId = null;
                await loadProjects();
                await Sidebar.refresh();
            } catch {
                // ignore
            }
        });
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
        const modelInput = document.getElementById('project-model');

        _editingProjectId = projectId || null;

        if (projectId) {
            titleEl.textContent = 'Edit Project';
            try {
                const proj = await api(`/api/projects/${projectId}`);
                nameInput.value = proj.name || '';
                instructionsInput.value = proj.instructions || '';
                modelInput.value = proj.model || '';
            } catch {
                nameInput.value = '';
                instructionsInput.value = '';
                modelInput.value = '';
            }
        } else {
            titleEl.textContent = 'New Project';
            nameInput.value = '';
            instructionsInput.value = '';
            modelInput.value = '';
        }

        modal.style.display = 'flex';
        nameInput.focus();
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
            // Reload project dropdown (remove old listeners by re-populating)
            const select = document.getElementById('project-select');
            const projects = await api('/api/projects');
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
            document.getElementById('btn-project-edit').style.display = state.currentProjectId ? '' : 'none';
            document.getElementById('btn-project-delete').style.display = state.currentProjectId ? '' : 'none';
            await Sidebar.refresh();
        } catch (e) {
            alert('Failed to save project: ' + e.message);
        }
    }

    document.addEventListener('DOMContentLoaded', init);

    return { state, api, _getCsrfToken, newConversation, loadConversation, loadProjects, formatTimestamp };
})();
