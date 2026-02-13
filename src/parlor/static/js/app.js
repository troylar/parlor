/* App state management and initialization */

const App = (() => {
    const state = {
        currentConversationId: null,
        isStreaming: false,
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

        // Check config status
        try {
            const config = await api('/api/config');
            if (!config.ai || !config.ai.base_url) {
                document.getElementById('setup-banner').style.display = '';
            }
            // Show MCP status in sidebar footer
            if (config.mcp_servers && config.mcp_servers.length > 0) {
                const connected = config.mcp_servers.filter(s => s.status === 'connected');
                const totalTools = connected.reduce((sum, s) => sum + s.tool_count, 0);
                document.getElementById('mcp-status').textContent =
                    `${totalTools} tools / ${connected.length} servers`;
            }
        } catch {
            // Config endpoint may not exist yet, that's ok
        }

        // Load conversations
        await Sidebar.refresh();

        // Load most recent conversation or show welcome
        const conversations = await api('/api/conversations');
        if (conversations && conversations.length > 0) {
            await loadConversation(conversations[0].id);
        }
    }

    async function newConversation() {
        const conv = await api('/api/conversations', { method: 'POST' });
        state.currentConversationId = conv.id;
        Chat.loadMessages([]);
        await Sidebar.refresh();
        Sidebar.setActive(conv.id);
        document.getElementById('message-input').focus();
    }

    async function loadConversation(id) {
        state.currentConversationId = id;
        const detail = await api(`/api/conversations/${id}`);
        Chat.loadMessages(detail.messages || []);
        Sidebar.setActive(id);
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

        // Load current config
        try {
            const config = await api('/api/config');
            promptTextarea.value = config.ai.system_prompt || '';
            modelInput.value = config.ai.model || '';

            modal.style.display = 'flex';
            suggestionsEl.innerHTML = '<span style="color:var(--text-muted);font-size:12px">Loading available models...</span>';

            // Fetch available models from API
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

            // Update active chip when user types
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

    document.addEventListener('DOMContentLoaded', init);

    return { state, api, _getCsrfToken, newConversation, loadConversation, formatTimestamp };
})();
