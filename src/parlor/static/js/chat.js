/* Chat UI: SSE streaming, message rendering, markdown */

const Chat = (() => {
    let eventSource = null;
    let currentAssistantEl = null;
    let currentAssistantContent = '';
    let _streamRawMode = localStorage.getItem('parlor_stream_raw_mode') !== 'false';

    // Configure marked for safe link rendering (marked v15 passes token object)
    const renderer = new marked.Renderer();
    const originalLink = renderer.link.bind(renderer);
    renderer.link = function(token) {
        try {
            const html = originalLink(token);
            if (!html) throw new Error('empty');
            return html.replace('<a ', '<a target="_blank" rel="noopener noreferrer" ');
        } catch {
            const href = (token && token.href) || (typeof token === 'string' ? token : '');
            const text = (token && token.text) || href;
            return `<a href="${DOMPurify.sanitize(href)}" target="_blank" rel="noopener noreferrer">${DOMPurify.sanitize(text)}</a>`;
        }
    };
    marked.use({ renderer });

    function init() {
        const sendBtn = document.getElementById('btn-send');
        const stopBtn = document.getElementById('btn-stop');
        const input = document.getElementById('message-input');

        sendBtn.addEventListener('click', sendMessage);
        stopBtn.addEventListener('click', stopGeneration);

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!App.state.isStreaming) {
                    sendMessage();
                }
            }
        });

        input.addEventListener('input', autoResizeInput);
    }

    function isRawMode() { return _streamRawMode; }

    function setRawMode(val) {
        _streamRawMode = val;
        localStorage.setItem('parlor_stream_raw_mode', val ? 'true' : 'false');
    }

    function autoResizeInput() {
        const input = document.getElementById('message-input');
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    }

    async function sendMessage() {
        const input = document.getElementById('message-input');
        const text = input.value.trim();
        if (!text || App.state.isStreaming) return;

        const conversationId = App.state.currentConversationId;
        if (!conversationId) {
            const conv = await App.api('/api/conversations', { method: 'POST' });
            App.state.currentConversationId = conv.id;
            Sidebar.refresh();
        }

        appendMessage('user', text);
        input.value = '';
        input.style.height = 'auto';

        const files = Attachments.getFiles();
        Attachments.clear();

        let body;
        let headers = { 'X-CSRF-Token': App._getCsrfToken() };
        if (files.length > 0) {
            const formData = new FormData();
            formData.append('message', text);
            files.forEach(f => formData.append('files', f));
            body = formData;
        } else {
            body = JSON.stringify({ message: text });
            headers['Content-Type'] = 'application/json';
        }

        await streamChatResponse(App.state.currentConversationId, body, headers);
    }

    async function streamChatResponse(conversationId, body, headers) {
        setStreaming(true);
        showThinking();

        if (!headers) {
            headers = {
                'Content-Type': 'application/json',
                'X-CSRF-Token': App._getCsrfToken(),
            };
        }

        try {
            const response = await fetch(`/api/conversations/${conversationId}/chat`, {
                method: 'POST',
                headers,
                body,
                credentials: 'same-origin',
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            hideThinking();
            currentAssistantContent = '';
            currentAssistantEl = appendMessage('assistant', '');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let eventType = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ') && eventType) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            if (data && typeof data === 'object') {
                                handleSSEEvent(eventType, data);
                            }
                        } catch (e) {
                            console.warn('Failed to parse SSE data:', e);
                        }
                        eventType = null;
                    }
                }
            }
        } catch (err) {
            hideThinking();
            if (currentAssistantEl) {
                showError(currentAssistantEl, err.message);
            } else {
                showError(null, err.message);
            }
        } finally {
            setStreaming(false);
        }
    }

    function handleSSEEvent(type, data) {
        switch (type) {
            case 'token':
                currentAssistantContent += data.content;
                renderAssistantContent();
                break;
            case 'tool_call_start':
                renderToolCallStart(data);
                break;
            case 'tool_call_end':
                renderToolCallEnd(data);
                break;
            case 'title':
                Sidebar.updateTitle(App.state.currentConversationId, data.title);
                break;
            case 'done':
                finalizeAssistant();
                break;
            case 'error':
                if (currentAssistantEl) {
                    showError(currentAssistantEl, data.message);
                }
                break;
        }
    }

    function renderAssistantContent() {
        if (!currentAssistantEl) return;
        const contentEl = currentAssistantEl.querySelector('.message-content');
        if (_streamRawMode) {
            contentEl.textContent = currentAssistantContent;
        } else {
            contentEl.innerHTML = renderMarkdown(currentAssistantContent);
            renderMath(contentEl);
        }
        scrollToBottom();
    }

    function finalizeAssistant() {
        if (!currentAssistantEl) return;
        const contentEl = currentAssistantEl.querySelector('.message-content');
        contentEl.innerHTML = renderMarkdown(currentAssistantContent);
        renderMath(contentEl);
        addCodeCopyButtons(contentEl);
        addMessageActions(currentAssistantEl, 'assistant', currentAssistantContent);
        currentAssistantEl = null;
        currentAssistantContent = '';
        scrollToBottom();
    }

    function renderMarkdown(text) {
        if (!text) return '';

        // Protect math blocks from markdown processing
        const mathBlocks = [];
        let protected_ = text;

        // Display math: $$...$$ and \[...\]
        protected_ = protected_.replace(/\$\$([\s\S]*?)\$\$/g, (match) => {
            mathBlocks.push(match);
            return `%%MATH_BLOCK_${mathBlocks.length - 1}%%`;
        });
        protected_ = protected_.replace(/\\\[([\s\S]*?)\\\]/g, (match) => {
            mathBlocks.push(match);
            return `%%MATH_BLOCK_${mathBlocks.length - 1}%%`;
        });

        // Inline math: $...$ and \(...\)
        protected_ = protected_.replace(/\$([^\$\n]+?)\$/g, (match) => {
            mathBlocks.push(match);
            return `%%MATH_BLOCK_${mathBlocks.length - 1}%%`;
        });
        protected_ = protected_.replace(/\\\(([\s\S]*?)\\\)/g, (match) => {
            mathBlocks.push(match);
            return `%%MATH_BLOCK_${mathBlocks.length - 1}%%`;
        });

        // Render markdown on the protected text
        let html = marked.parse(protected_);

        // Restore math blocks
        html = html.replace(/%%MATH_BLOCK_(\d+)%%/g, (_, idx) => {
            return mathBlocks[parseInt(idx)];
        });

        // Sanitize HTML to prevent XSS
        return DOMPurify.sanitize(html, {
            ALLOWED_TAGS: [
                'p', 'br', 'strong', 'em', 'code', 'pre', 'blockquote',
                'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
                'hr', 'del', 'details', 'summary', 'span', 'div', 'sup', 'sub',
                'dl', 'dt', 'dd', 'kbd', 'var', 'samp', 'abbr', 'mark',
            ],
            ALLOWED_ATTR: [
                'href', 'src', 'alt', 'title', 'class', 'id',
                'target', 'rel', 'open', 'colspan', 'rowspan',
            ],
            ALLOW_DATA_ATTR: false,
        });
    }

    function renderMath(el) {
        if (typeof renderMathInElement === 'function') {
            try {
                renderMathInElement(el, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '\\[', right: '\\]', display: true },
                        { left: '$', right: '$', display: false },
                        { left: '\\(', right: '\\)', display: false },
                    ],
                    throwOnError: false,
                    ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
                });
            } catch (e) {
                // silently ignore render errors
            }
        }
    }

    function highlightCode(el) {
        el.querySelectorAll('pre code').forEach(block => {
            hljs.highlightElement(block);
        });
    }

    function addCodeCopyButtons(el) {
        el.querySelectorAll('pre').forEach(pre => {
            const code = pre.querySelector('code');
            if (!code) return;

            const lang = (code.className.match(/language-(\w+)/) || [])[1] || '';
            const header = document.createElement('div');
            header.className = 'code-header';

            const langSpan = document.createElement('span');
            langSpan.textContent = lang;
            header.appendChild(langSpan);

            const copyBtn = document.createElement('button');
            copyBtn.className = 'btn-copy-code';
            copyBtn.textContent = 'Copy';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(code.textContent).then(() => {
                    copyBtn.textContent = 'Copied!';
                    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
                });
            });
            header.appendChild(copyBtn);

            pre.insertBefore(header, code);
            hljs.highlightElement(code);
        });
    }

    function addMessageActions(msgEl, role, content, msgData) {
        const actions = document.createElement('div');
        actions.className = 'message-actions';

        if (role === 'assistant') {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'btn-action-icon';
            copyBtn.title = 'Copy';
            copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(content).then(() => {
                    copyBtn.title = 'Copied!';
                    setTimeout(() => { copyBtn.title = 'Copy'; }, 2000);
                });
            });
            actions.appendChild(copyBtn);
        }

        if (role === 'user' && msgData) {
            // Edit button
            const editBtn = document.createElement('button');
            editBtn.className = 'btn-action-icon';
            editBtn.title = 'Edit';
            editBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
            editBtn.addEventListener('click', () => startEdit(msgEl, msgData));
            actions.appendChild(editBtn);
        }

        if (msgData) {
            // Fork button
            const forkBtn = document.createElement('button');
            forkBtn.className = 'btn-action-icon';
            forkBtn.title = 'Fork from here';
            forkBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v1a2 2 0 01-2 2H8a2 2 0 01-2-2V9"/><line x1="12" y1="12" x2="12" y2="15"/></svg>';
            forkBtn.addEventListener('click', () => forkConversation(msgData.position));
            actions.appendChild(forkBtn);
        }

        msgEl.appendChild(actions);
    }

    async function forkConversation(position) {
        const conversationId = App.state.currentConversationId;
        if (!conversationId) return;
        try {
            const newConv = await App.api(`/api/conversations/${conversationId}/fork`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ up_to_position: position }),
            });
            await App.loadConversation(newConv.id);
            await Sidebar.refresh();
        } catch (err) {
            alert('Fork failed: ' + err.message);
        }
    }

    function startEdit(msgEl, msgData) {
        const contentDiv = msgEl.querySelector('.message-content');
        const originalContent = msgData.content;
        const actionsDiv = msgEl.querySelector('.message-actions');
        if (actionsDiv) actionsDiv.style.display = 'none';

        const textarea = document.createElement('textarea');
        textarea.className = 'message-edit-textarea';
        textarea.value = originalContent;
        textarea.rows = Math.max(2, originalContent.split('\n').length);

        const btnRow = document.createElement('div');
        btnRow.className = 'message-edit-actions';

        const saveBtn = document.createElement('button');
        saveBtn.className = 'btn-modal-save';
        saveBtn.textContent = 'Save & Regenerate';
        saveBtn.addEventListener('click', () => saveEdit(msgEl, msgData, textarea.value));

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn-modal-cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => cancelEdit(msgEl, msgData, contentDiv, actionsDiv));

        btnRow.appendChild(cancelBtn);
        btnRow.appendChild(saveBtn);

        contentDiv.style.display = 'none';
        contentDiv.parentNode.insertBefore(textarea, contentDiv.nextSibling);
        contentDiv.parentNode.insertBefore(btnRow, textarea.nextSibling);
        textarea.focus();
    }

    function cancelEdit(msgEl, msgData, contentDiv, actionsDiv) {
        const textarea = msgEl.querySelector('.message-edit-textarea');
        const btnRow = msgEl.querySelector('.message-edit-actions');
        if (textarea) textarea.remove();
        if (btnRow) btnRow.remove();
        contentDiv.style.display = '';
        if (actionsDiv) actionsDiv.style.display = '';
    }

    async function saveEdit(msgEl, msgData, newContent) {
        newContent = newContent.trim();
        if (!newContent) return;

        const conversationId = App.state.currentConversationId;

        try {
            // 1. Update message content
            await App.api(`/api/conversations/${conversationId}/messages/${msgData.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: newContent }),
            });

            // 2. Delete messages after this position
            await App.api(`/api/conversations/${conversationId}/messages?after_position=${msgData.position}`, {
                method: 'DELETE',
            });

            // 3. Remove subsequent message DOM elements
            let sibling = msgEl.nextElementSibling;
            while (sibling) {
                const next = sibling.nextElementSibling;
                sibling.remove();
                sibling = next;
            }

            // 4. Restore edit UI
            const textarea = msgEl.querySelector('.message-edit-textarea');
            const btnRow = msgEl.querySelector('.message-edit-actions');
            const contentDiv = msgEl.querySelector('.message-content');
            if (textarea) textarea.remove();
            if (btnRow) btnRow.remove();
            contentDiv.style.display = '';
            contentDiv.textContent = newContent;

            const actionsDiv = msgEl.querySelector('.message-actions');
            if (actionsDiv) actionsDiv.style.display = '';

            // Update msgData so future edits use new content
            msgData.content = newContent;

            // 5. Regenerate AI response
            const body = JSON.stringify({ message: '', regenerate: true });
            const headers = {
                'Content-Type': 'application/json',
                'X-CSRF-Token': App._getCsrfToken(),
            };
            await streamChatResponse(conversationId, body, headers);

        } catch (err) {
            alert('Edit failed: ' + err.message);
        }
    }

    function _sanitizeId(id) {
        return String(id).replace(/[^a-zA-Z0-9\-_]/g, '');
    }

    function appendMessage(role, content, msgData) {
        const container = document.getElementById('messages-container');
        const welcome = document.getElementById('welcome-message');
        if (welcome) welcome.style.display = 'none';

        const el = document.createElement('div');
        el.className = `message ${role}`;

        const roleDiv = document.createElement('div');
        roleDiv.className = 'message-role';
        roleDiv.textContent = role === 'user' ? 'You' : 'Assistant';
        el.appendChild(roleDiv);

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        if (role === 'user') {
            contentDiv.textContent = content;
        } else {
            contentDiv.innerHTML = renderMarkdown(content);
        }
        el.appendChild(contentDiv);

        if (role === 'user') {
            const files = Attachments.getFiles();
            if (files.length > 0) {
                const attDiv = document.createElement('div');
                attDiv.className = 'message-attachments';
                files.forEach(f => {
                    const chip = document.createElement('div');
                    chip.className = 'attachment-chip';
                    if (f.type.startsWith('image/')) {
                        const img = document.createElement('img');
                        img.src = URL.createObjectURL(f);
                        chip.appendChild(img);
                    }
                    chip.appendChild(document.createTextNode(f.name));
                    attDiv.appendChild(chip);
                });
                el.appendChild(attDiv);
            }
        }

        container.appendChild(el);
        scrollToBottom();
        return el;
    }

    function showThinking() {
        const container = document.getElementById('messages-container');
        const el = document.createElement('div');
        el.className = 'thinking-indicator';
        el.id = 'thinking';
        el.innerHTML = '<span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>';
        container.appendChild(el);
        scrollToBottom();
    }

    function hideThinking() {
        const el = document.getElementById('thinking');
        if (el) el.remove();
    }

    function showError(msgEl, message) {
        const errDiv = document.createElement('div');
        errDiv.className = 'error-message';

        const errText = document.createElement('span');
        errText.textContent = `Error: ${message}`;
        errDiv.appendChild(errText);

        const retryBtn = document.createElement('button');
        retryBtn.className = 'btn-retry';
        retryBtn.textContent = 'Retry';
        retryBtn.addEventListener('click', () => {
            errDiv.remove();
            sendMessage();
        });
        errDiv.appendChild(retryBtn);

        if (msgEl) {
            msgEl.appendChild(errDiv);
        } else {
            document.getElementById('messages-container').appendChild(errDiv);
        }
        scrollToBottom();
    }

    function renderToolCallStart(data) {
        if (!currentAssistantEl) return;
        const contentEl = currentAssistantEl.querySelector('.message-content');
        const details = document.createElement('details');
        details.className = 'tool-call';
        details.id = `tool-${_sanitizeId(data.id)}`;

        const summary = document.createElement('summary');
        summary.textContent = `Tool: ${data.tool_name} `;
        const spinner = document.createElement('span');
        spinner.className = 'tool-spinner';
        summary.appendChild(spinner);
        details.appendChild(summary);

        const toolContent = document.createElement('div');
        toolContent.className = 'tool-content';
        const inputLabel = document.createElement('strong');
        inputLabel.textContent = 'Input:';
        toolContent.appendChild(inputLabel);
        const inputPre = document.createElement('pre');
        const inputCode = document.createElement('code');
        inputCode.textContent = JSON.stringify(data.input, null, 2);
        inputPre.appendChild(inputCode);
        toolContent.appendChild(inputPre);

        details.appendChild(toolContent);
        contentEl.appendChild(details);
        scrollToBottom();
    }

    function renderToolCallEnd(data) {
        const details = document.getElementById(`tool-${_sanitizeId(data.id)}`);
        if (!details) return;
        const spinner = details.querySelector('.tool-spinner');
        if (spinner) spinner.remove();

        const toolContent = details.querySelector('.tool-content');
        const outputLabel = document.createElement('strong');
        outputLabel.textContent = `Output (${data.status}):`;
        toolContent.appendChild(outputLabel);
        const outputPre = document.createElement('pre');
        const outputCode = document.createElement('code');
        outputCode.textContent = JSON.stringify(data.output, null, 2);
        outputPre.appendChild(outputCode);
        toolContent.appendChild(outputPre);
    }

    function setStreaming(streaming) {
        App.state.isStreaming = streaming;
        document.getElementById('btn-send').style.display = streaming ? 'none' : 'flex';
        document.getElementById('btn-stop').style.display = streaming ? 'flex' : 'none';
        document.getElementById('message-input').disabled = streaming;
        document.getElementById('btn-send').disabled = streaming;
    }

    async function stopGeneration() {
        if (!App.state.currentConversationId) return;
        try {
            await App.api(`/api/conversations/${App.state.currentConversationId}/stop`, { method: 'POST' });
        } catch (e) {
            // ignore
        }
    }

    function loadMessages(messages) {
        const container = document.getElementById('messages-container');
        container.innerHTML = '';
        const welcome = document.getElementById('welcome-message');

        if (messages.length === 0) {
            if (!welcome) {
                const w = document.createElement('div');
                w.id = 'welcome-message';
                w.className = 'welcome-message';
                w.innerHTML = '<h2>Welcome to AI Chat</h2><p>Start a conversation by typing a message below.</p>';
                container.appendChild(w);
            } else {
                welcome.style.display = '';
                container.appendChild(welcome);
            }
            return;
        }

        messages.forEach(msg => {
            const el = document.createElement('div');
            el.className = `message ${msg.role}`;

            const roleDiv = document.createElement('div');
            roleDiv.className = 'message-role';
            roleDiv.textContent = msg.role === 'user' ? 'You' : 'Assistant';
            el.appendChild(roleDiv);

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            if (msg.role === 'user') {
                contentDiv.textContent = msg.content;
            } else {
                contentDiv.innerHTML = renderMarkdown(msg.content);
            }
            el.appendChild(contentDiv);

            if (msg.role === 'assistant') {
                renderMath(contentDiv);
                addCodeCopyButtons(contentDiv);
            }

            if (msg.attachments && msg.attachments.length > 0) {
                const attDiv = document.createElement('div');
                attDiv.className = 'message-attachments';
                msg.attachments.forEach(att => {
                    const chip = document.createElement('div');
                    chip.className = 'attachment-chip';
                    chip.textContent = att.filename;
                    attDiv.appendChild(chip);
                });
                el.appendChild(attDiv);
            }

            if (msg.tool_calls && msg.tool_calls.length > 0) {
                msg.tool_calls.forEach(tc => {
                    const details = document.createElement('details');
                    details.className = 'tool-call';

                    const summary = document.createElement('summary');
                    summary.textContent = `Tool: ${tc.tool_name} (${tc.status})`;
                    details.appendChild(summary);

                    const toolContent = document.createElement('div');
                    toolContent.className = 'tool-content';

                    const inputLabel = document.createElement('strong');
                    inputLabel.textContent = 'Input:';
                    toolContent.appendChild(inputLabel);
                    const inputPre = document.createElement('pre');
                    const inputCode = document.createElement('code');
                    inputCode.textContent = JSON.stringify(tc.input, null, 2);
                    inputPre.appendChild(inputCode);
                    toolContent.appendChild(inputPre);

                    if (tc.output) {
                        const outputLabel = document.createElement('strong');
                        outputLabel.textContent = 'Output:';
                        toolContent.appendChild(outputLabel);
                        const outputPre = document.createElement('pre');
                        const outputCode = document.createElement('code');
                        outputCode.textContent = JSON.stringify(tc.output, null, 2);
                        outputPre.appendChild(outputCode);
                        toolContent.appendChild(outputPre);
                    }

                    details.appendChild(toolContent);
                    el.querySelector('.message-content').appendChild(details);
                });
            }

            // Add action buttons (copy, fork, edit)
            addMessageActions(el, msg.role, msg.content, msg);

            container.appendChild(el);
        });
        scrollToBottom();
    }

    function scrollToBottom() {
        const container = document.getElementById('messages-container');
        container.scrollTop = container.scrollHeight;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    return {
        init, sendMessage, loadMessages, stopGeneration, setStreaming, escapeHtml,
        streamChatResponse, isRawMode, setRawMode,
    };
})();
