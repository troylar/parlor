/* Chat UI: SSE streaming, message rendering, markdown */

const Chat = (() => {
    let eventSource = null;
    let currentAssistantEl = null;
    let currentAssistantContent = '';

    // Configure marked for safe link rendering
    const renderer = new marked.Renderer();
    const originalLink = renderer.link.bind(renderer);
    renderer.link = function(href, title, text) {
        const html = originalLink(href, title, text);
        return html.replace('<a ', '<a target="_blank" rel="noopener noreferrer" ');
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

        setStreaming(true);
        showThinking();

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

        try {
            const response = await fetch(`/api/conversations/${App.state.currentConversationId}/chat`, {
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
        contentEl.innerHTML = renderMarkdown(currentAssistantContent);
        renderMath(contentEl);
        scrollToBottom();
    }

    function finalizeAssistant() {
        if (!currentAssistantEl) return;
        const contentEl = currentAssistantEl.querySelector('.message-content');
        contentEl.innerHTML = renderMarkdown(currentAssistantContent);
        renderMath(contentEl);
        addCodeCopyButtons(contentEl);
        addMessageCopyButton(currentAssistantEl, currentAssistantContent);
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

    function addMessageCopyButton(msgEl, content) {
        const actions = document.createElement('div');
        actions.className = 'message-actions';
        const copyBtn = document.createElement('button');
        copyBtn.className = 'btn-copy-message';
        copyBtn.textContent = 'Copy';
        copyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(content).then(() => {
                copyBtn.textContent = 'Copied!';
                setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
            });
        });
        actions.appendChild(copyBtn);
        msgEl.appendChild(actions);
    }

    function _sanitizeId(id) {
        return String(id).replace(/[^a-zA-Z0-9\-_]/g, '');
    }

    function appendMessage(role, content) {
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
                addMessageCopyButton(el, msg.content);
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

    return { init, sendMessage, loadMessages, stopGeneration, setStreaming, escapeHtml };
})();
