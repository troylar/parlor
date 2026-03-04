/* Canvas panel: side-by-side markdown editing alongside chat (CodeMirror 6) */

const Canvas = (() => {
    let _canvasData = null;
    let _isDirty = false;
    let _mode = 'preview';
    let _cmView = null;
    let _suppressDirty = false;
    let _isStreaming = false;
    let _streamingContent = '';
    let _streamingLanguage = null;

    /* --- Excalidraw rendering (iframe-isolated) --- */
    let _excalidrawIframe = null;

    function _isExcalidraw(language) {
        return language === 'excalidraw';
    }

    function _destroyExcalidraw() {
        if (_excalidrawIframe) {
            _excalidrawIframe.remove();
            _excalidrawIframe = null;
        }
    }

    function _renderExcalidrawFallback(container, content, errorMsg) {
        let summary = '';
        try {
            const parsed = JSON.parse(content);
            const elems = parsed.elements || [];
            const types = {};
            for (const e of elems) {
                const t = e.type || 'unknown';
                types[t] = (types[t] || 0) + 1;
            }
            const parts = Object.entries(types).map(([t, n]) => n + ' ' + t + (n > 1 ? 's' : ''));
            summary = '<p style="margin:8px 0 4px;color:var(--text-secondary)">' +
                '<strong>Diagram contents:</strong> ' + elems.length + ' element' +
                (elems.length !== 1 ? 's' : '') +
                (parts.length ? ' (' + DOMPurify.sanitize(parts.join(', ')) + ')' : '') +
                '</p>';
        } catch { /* content isn't valid JSON — skip summary */ }

        const errorHtml = DOMPurify.sanitize(errorMsg);
        const contentPreview = DOMPurify.sanitize(content.slice(0, 5000));

        container.innerHTML =
            '<div style="padding:12px">' +
            '<p style="color:var(--text-muted);margin:0 0 8px">' + errorHtml + '</p>' +
            summary +
            '<div style="display:flex;gap:8px;margin:8px 0">' +
            '<button id="excalidraw-copy-json" style="' +
            'padding:4px 12px;border:1px solid var(--border);border-radius:4px;' +
            'background:var(--bg-secondary);color:var(--text-primary);cursor:pointer;font-size:0.85em' +
            '">Copy JSON</button>' +
            '<a href="https://excalidraw.com" target="_blank" rel="noopener noreferrer" style="' +
            'padding:4px 12px;border:1px solid var(--border);border-radius:4px;' +
            'background:var(--bg-secondary);color:var(--text-primary);text-decoration:none;font-size:0.85em;' +
            'display:inline-flex;align-items:center' +
            '">Open excalidraw.com</a>' +
            '</div>' +
            '<pre style="font-size:0.8em;max-height:300px;overflow:auto;margin-top:8px;' +
            'padding:8px;border-radius:4px;background:var(--bg-secondary)">' +
            contentPreview + '</pre>' +
            '</div>';

        const copyBtn = container.querySelector('#excalidraw-copy-json');
        if (copyBtn) {
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(content).then(() => {
                    copyBtn.textContent = 'Copied!';
                    setTimeout(() => { copyBtn.textContent = 'Copy JSON'; }, 1500);
                }).catch(() => {
                    copyBtn.textContent = 'Copy failed';
                    setTimeout(() => { copyBtn.textContent = 'Copy JSON'; }, 1500);
                });
            });
        }
    }

    function _setExcalidrawContainerMode(active) {
        const preview = document.getElementById('canvas-preview');
        if (preview) {
            preview.classList.toggle('excalidraw-active', active);
        }
    }

    function _renderExcalidraw(container, content) {
        let sceneData;
        try {
            sceneData = JSON.parse(content);
        } catch {
            _setExcalidrawContainerMode(false);
            _renderExcalidrawFallback(container, content,
                'Invalid Excalidraw JSON — could not parse diagram data.');
            return;
        }

        _setExcalidrawContainerMode(true);
        _destroyExcalidraw();
        container.innerHTML = '';

        // Load Excalidraw in an iframe pointing to /excalidraw-viewer.
        // That endpoint serves its own HTML with a permissive CSP that allows
        // esm.sh imports. Scene data is sent via postMessage after load.
        // This isolates Excalidraw completely: CDN failures, React crashes, and
        // slow module loads cannot affect the main Anteroom page.
        const iframe = document.createElement('iframe');
        iframe.style.cssText = 'width:100%;height:100%;border:none;';
        iframe.src = '/excalidraw-viewer';
        container.appendChild(iframe);
        _excalidrawIframe = iframe;

        // Send scene data once the iframe is ready to receive messages
        iframe.addEventListener('load', () => {
            iframe.contentWindow.postMessage(
                { type: 'excalidraw-scene', scene: sceneData }, window.location.origin
            );
        });

        // If the iframe fails to render within 30s, show the fallback
        const parentTimeout = setTimeout(() => {
            console.error('[anteroom] Excalidraw iframe timed out');
            _setExcalidrawContainerMode(false);
            _renderExcalidrawFallback(container, content,
                'Excalidraw renderer timed out. You can copy the JSON below ' +
                'and paste it into excalidraw.com to view the diagram.');
        }, 30000);

        // Listen for status messages from the iframe
        const onMessage = (evt) => {
            if (evt.source !== iframe.contentWindow) return;
            const d = evt.data;
            if (d && d.type === 'excalidraw-ready') {
                clearTimeout(parentTimeout);
                window.removeEventListener('message', onMessage);
            } else if (d && d.type === 'excalidraw-error') {
                clearTimeout(parentTimeout);
                window.removeEventListener('message', onMessage);
                _setExcalidrawContainerMode(false);
                _renderExcalidrawFallback(container, content,
                    'Could not load the Excalidraw renderer. ' +
                    'You can copy the JSON below and paste it into excalidraw.com to view the diagram.');
            }
        };
        window.addEventListener('message', onMessage);
    }

    function init() {
        const closeBtn = document.getElementById('canvas-close');
        const saveBtn = document.getElementById('canvas-save');
        const toggleBtn = document.getElementById('btn-canvas-toggle');

        if (closeBtn) closeBtn.addEventListener('click', closeCanvas);
        if (saveBtn) saveBtn.addEventListener('click', saveCanvas);
        if (toggleBtn) toggleBtn.addEventListener('click', toggleCanvas);

        const modeToggle = document.getElementById('canvas-mode-toggle');
        if (modeToggle) {
            modeToggle.addEventListener('click', (e) => {
                const btn = e.target.closest('.canvas-mode-btn');
                if (!btn) return;
                const mode = btn.dataset.mode;
                if (mode) _setMode(mode);
            });
        }
    }

    function _mdToHtml(md) {
        if (!md) return '';
        return DOMPurify.sanitize(marked.parse(md), {
            ALLOWED_TAGS: [
                'p', 'br', 'strong', 'em', 'code', 'pre', 'blockquote',
                'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
                'hr', 'del', 'details', 'summary', 'span', 'div', 'sup', 'sub',
                'dl', 'dt', 'dd', 'kbd', 'var', 'samp', 'abbr', 'mark',
            ],
            ALLOWED_ATTR: [
                'href', 'src', 'alt', 'title', 'class',
                'target', 'rel', 'open', 'colspan', 'rowspan',
            ],
            ALLOW_DATA_ATTR: false,
        });
    }

    function _getMarkdown() {
        if (_cmView) {
            return _cmView.state.doc.toString();
        }
        return _canvasData ? _canvasData.content || '' : '';
    }

    function _initCodeMirror(content) {
        _destroyCodeMirror();
        const container = document.getElementById('canvas-cm-wrap');
        if (!container || typeof CM === 'undefined') return;

        _suppressDirty = true;

        const updateListener = CM.EditorView.updateListener.of((update) => {
            if (update.docChanged && !_suppressDirty) {
                _isDirty = true;
                _updateSaveBtn();
            }
        });

        _cmView = new CM.EditorView({
            state: CM.EditorState.create({
                doc: content || '',
                extensions: [
                    CM.lineNumbers(),
                    CM.highlightActiveLineGutter(),
                    CM.highlightActiveLine(),
                    CM.drawSelection(),
                    CM.history(),
                    CM.foldGutter(),
                    CM.indentOnInput(),
                    CM.bracketMatching(),
                    CM.highlightSelectionMatches(),
                    CM.keymap.of([
                        ...CM.defaultKeymap,
                        ...CM.historyKeymap,
                        ...CM.searchKeymap,
                        CM.indentWithTab,
                    ]),
                    CM.markdown({ base: CM.markdownLanguage }),
                    CM.syntaxHighlighting(CM.defaultHighlightStyle),
                    CM.syntaxHighlighting(CM.appHighlight),
                    CM.appTheme,
                    CM.EditorView.lineWrapping,
                    CM.placeholder('Start writing...'),
                    updateListener,
                ],
            }),
            parent: container,
        });

        _suppressDirty = false;
    }

    function _destroyCodeMirror() {
        if (_cmView) {
            _cmView.destroy();
            _cmView = null;
        }
    }

    function _setCmContent(text) {
        if (!_cmView) return;
        _suppressDirty = true;
        _cmView.dispatch({
            changes: { from: 0, to: _cmView.state.doc.length, insert: text },
        });
        _suppressDirty = false;
    }

    function _setMode(mode) {
        _mode = mode;

        const cmWrap = document.getElementById('canvas-cm-wrap');
        const preview = document.getElementById('canvas-preview');
        const toggle = document.getElementById('canvas-mode-toggle');

        if (!cmWrap || !preview) return;

        if (mode === 'edit') {
            cmWrap.style.display = '';
            preview.style.display = 'none';
            if (!_cmView) {
                _initCodeMirror(_canvasData ? _canvasData.content || '' : '');
            } else {
                _cmView.requestMeasure();
            }
        } else {
            const content = _getMarkdown();
            _renderPreview(content, _canvasData ? _canvasData.language : null);
            cmWrap.style.display = 'none';
            preview.style.display = '';
        }

        if (toggle) {
            toggle.querySelectorAll('.canvas-mode-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.mode === mode);
            });
        }
    }

    function _renderPreview(content, language) {
        const preview = document.getElementById('canvas-preview');
        if (!preview) return;
        if (!content) {
            preview.innerHTML = '<p style="color:var(--text-muted)">Empty canvas</p>';
            return;
        }

        if (_isExcalidraw(language)) {
            _renderExcalidraw(preview, content);
            return;
        }

        let md = content;
        if (language) {
            md = '```' + language + '\n' + content + '\n```';
        }
        preview.innerHTML = _mdToHtml(md);
        preview.querySelectorAll('pre code').forEach(block => {
            hljs.highlightElement(block);
        });
    }

    function openCanvas(data) {
        _canvasData = data;
        _isDirty = false;
        _mode = 'preview';

        const panel = document.getElementById('canvas-panel');
        const chatMain = document.querySelector('.chat-main');
        const titleEl = document.getElementById('canvas-title');
        const toggleBtn = document.getElementById('btn-canvas-toggle');

        if (!panel) return;

        if (titleEl) titleEl.textContent = data.title || 'Untitled';

        panel.style.display = '';
        if (chatMain) chatMain.classList.add('with-canvas');
        if (toggleBtn) toggleBtn.classList.add('active');

        const cmWrap = document.getElementById('canvas-cm-wrap');
        const preview = document.getElementById('canvas-preview');
        const isExcalidraw = _isExcalidraw(data.language);

        // Start in preview mode — defer CodeMirror init until edit mode
        if (cmWrap) cmWrap.style.display = 'none';
        if (preview) {
            preview.style.display = '';
            _renderPreview(data.content || '', data.language || null);
        }

        // Hide edit/save controls for Excalidraw (view-only)
        const toggle = document.getElementById('canvas-mode-toggle');
        const saveBtn = document.getElementById('canvas-save');
        if (toggle) {
            toggle.style.display = isExcalidraw ? 'none' : '';
            toggle.querySelectorAll('.canvas-mode-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.mode === 'preview');
            });
        }
        if (saveBtn) saveBtn.style.display = isExcalidraw ? 'none' : '';

        _updateSaveBtn();
    }

    function closeCanvas() {
        if (_isDirty) {
            if (!confirm('You have unsaved changes. Close anyway?')) return;
        }
        _destroyCodeMirror();
        _destroyExcalidraw();
        _setExcalidrawContainerMode(false);
        _hidePanel();
    }

    function _hidePanel() {
        const panel = document.getElementById('canvas-panel');
        const chatMain = document.querySelector('.chat-main');
        const toggleBtn = document.getElementById('btn-canvas-toggle');

        if (panel) panel.style.display = 'none';
        if (chatMain) chatMain.classList.remove('with-canvas');
        if (toggleBtn) toggleBtn.classList.remove('active');
    }

    async function saveCanvas() {
        if (!_canvasData || !App.state.currentConversationId) return;

        const content = _getMarkdown();
        const saveBtn = document.getElementById('canvas-save');
        const titleEl = document.getElementById('canvas-title');
        const title = titleEl ? titleEl.textContent : _canvasData.title;

        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        try {
            const updated = await App.api(
                `/api/conversations/${App.state.currentConversationId}/canvas`,
                {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content, title }),
                }
            );
            _canvasData = updated;
            _isDirty = false;
            if (saveBtn) {
                saveBtn.textContent = 'Saved';
                saveBtn.disabled = true;
                setTimeout(() => {
                    saveBtn.textContent = 'Save';
                    _updateSaveBtn();
                }, 1500);
            }
        } catch (err) {
            alert('Failed to save canvas: ' + err.message);
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        }
    }

    function handleCanvasCreated(data) {
        _isStreaming = false;
        _streamingContent = '';
        _streamingLanguage = null;
        openCanvas(data);
    }

    function _isValidExcalidrawJson(content) {
        try {
            const parsed = JSON.parse(content);
            return parsed && Array.isArray(parsed.elements);
        } catch { return false; }
    }

    function _updateExcalidrawIfValid(content) {
        // Only re-render if the content is valid Excalidraw JSON.
        // Invalid/truncated JSON keeps the previous render intact.
        if (!_isValidExcalidrawJson(content)) return;
        const preview = document.getElementById('canvas-preview');
        if (preview) _renderExcalidraw(preview, content);
    }

    function handleCanvasPatched(data) {
        if (!_canvasData) {
            openCanvas(data);
            return;
        }
        _canvasData = { ..._canvasData, ...data };

        const titleEl = document.getElementById('canvas-title');
        if (data.title && titleEl) titleEl.textContent = data.title;

        if (data.content != null) {
            if (_isExcalidraw(_canvasData.language)) {
                _updateExcalidrawIfValid(data.content);
            } else {
                _setCmContent(data.content);
                if (_mode === 'preview') {
                    _renderPreview(data.content, _canvasData.language);
                }
            }
        }

        _isDirty = false;
        _updateSaveBtn();
    }

    function handleCanvasUpdated(data) {
        _isStreaming = false;
        _streamingContent = '';
        _streamingLanguage = null;
        if (!_canvasData) {
            openCanvas(data);
            return;
        }
        _canvasData = { ..._canvasData, ...data };
        const titleEl = document.getElementById('canvas-title');

        if (data.content != null) {
            if (_isExcalidraw(_canvasData.language)) {
                _updateExcalidrawIfValid(data.content);
            } else {
                _setCmContent(data.content);
                if (_mode === 'preview') _renderPreview(data.content, _canvasData.language);
            }
            _isDirty = false;
            _updateSaveBtn();
        }
        if (titleEl) titleEl.textContent = data.title || _canvasData.title;
    }

    async function createNewCanvas() {
        if (!App.state.currentConversationId) return;
        try {
            const canvas = await App.api(
                `/api/conversations/${App.state.currentConversationId}/canvas`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: 'Untitled', content: '' }),
                }
            );
            openCanvas(canvas);
        } catch (err) {
            if (err.message && err.message.includes('409')) {
                try {
                    const existing = await App.api(
                        `/api/conversations/${App.state.currentConversationId}/canvas`
                    );
                    openCanvas(existing);
                } catch { /* ignore */ }
            } else {
                alert('Failed to create canvas: ' + err.message);
            }
        }
    }

    async function toggleCanvas() {
        const panel = document.getElementById('canvas-panel');
        if (panel && panel.style.display !== 'none') {
            closeCanvas();
            return;
        }
        if (_canvasData) {
            openCanvas(_canvasData);
            return;
        }
        if (!App.state.currentConversationId) return;
        try {
            const canvas = await App.api(
                `/api/conversations/${App.state.currentConversationId}/canvas`
            );
            openCanvas(canvas);
        } catch {
            if (!_canvasData) createNewCanvas();
        }
    }

    function resetCanvas() {
        _canvasData = null;
        _isDirty = false;
        _destroyCodeMirror();
        _destroyExcalidraw();
        _setExcalidrawContainerMode(false);
        _hidePanel();
    }

    async function loadForConversation(conversationId) {
        _canvasData = null;
        _isDirty = false;
        _destroyCodeMirror();
        try {
            const canvas = await App.api(`/api/conversations/${conversationId}/canvas`);
            openCanvas(canvas);
        } catch {
            _hidePanel();
        }
    }

    function _updateSaveBtn() {
        const btn = document.getElementById('canvas-save');
        if (btn) {
            btn.disabled = !_isDirty;
        }
    }

    function handleCanvasStreamStart(data) {
        _isStreaming = true;
        _streamingContent = '';
        // Use the stream event's language, fall back to the current canvas language
        _streamingLanguage = (data && data.language) || (_canvasData && _canvasData.language) || null;

        const panel = document.getElementById('canvas-panel');
        const chatMain = document.querySelector('.chat-main');
        const toggleBtn = document.getElementById('btn-canvas-toggle');

        if (panel && panel.style.display === 'none') {
            panel.style.display = '';
            if (chatMain) chatMain.classList.add('with-canvas');
            if (toggleBtn) toggleBtn.classList.add('active');
        }

        // Switch to preview mode for streaming
        const cmWrap = document.getElementById('canvas-cm-wrap');
        const preview = document.getElementById('canvas-preview');
        if (cmWrap) cmWrap.style.display = 'none';
        if (preview) {
            preview.style.display = '';
            preview.innerHTML = '<p style="color:var(--text-muted)">Generating...</p>';
        }

        const toggle = document.getElementById('canvas-mode-toggle');
        if (toggle) {
            toggle.querySelectorAll('.canvas-mode-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.mode === 'preview');
            });
        }
    }

    let _streamRafId = null;
    function handleCanvasStreaming(data) {
        if (!_isStreaming) return;
        _streamingContent += data.content_delta;

        // For excalidraw, show progress indicator instead of partial JSON
        if (_isExcalidraw(_streamingLanguage)) {
            if (!_streamRafId) {
                _streamRafId = requestAnimationFrame(() => {
                    _streamRafId = null;
                    const preview = document.getElementById('canvas-preview');
                    if (preview) {
                        const chars = _streamingContent.length;
                        preview.innerHTML =
                            '<p style="color:var(--text-muted)">Generating diagram... ' +
                            '(' + chars.toLocaleString() + ' chars)</p>';
                    }
                });
            }
            return;
        }

        if (!_streamRafId) {
            _streamRafId = requestAnimationFrame(() => {
                _streamRafId = null;
                _renderPreview(_streamingContent, _streamingLanguage);
            });
        }
    }

    function hasCanvas() {
        return _canvasData !== null;
    }

    function getMarkdown() {
        return _getMarkdown();
    }

    return {
        init, openCanvas, closeCanvas, saveCanvas,
        handleCanvasCreated, handleCanvasUpdated, handleCanvasPatched,
        handleCanvasStreamStart, handleCanvasStreaming,
        createNewCanvas, toggleCanvas, resetCanvas,
        loadForConversation, hasCanvas, getMarkdown,
    };
})();
