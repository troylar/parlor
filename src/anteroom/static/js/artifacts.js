/* Artifact & Pack browser panel — IIFE following Sources pattern. */
const Artifacts = (() => {
    'use strict';

    let _artifacts = [];
    let _packs = [];
    let _currentTab = 'artifacts';
    let _currentView = 'list';

    function _escapeHtml(str) {
        if (!str) return '';
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    /* ── type / source badges ── */

    const _TYPE_COLORS = {
        skill:          { bg: 'rgba(59,130,246,0.1)',  fg: '#3b82f6' },
        rule:           { bg: 'rgba(168,85,247,0.1)',  fg: '#a855f7' },
        instruction:    { bg: 'rgba(34,197,94,0.1)',   fg: '#22c55e' },
        context:        { bg: 'rgba(251,191,36,0.1)',  fg: '#fbbf24' },
        memory:         { bg: 'rgba(239,68,68,0.1)',   fg: '#ef4444' },
        mcp_server:     { bg: 'rgba(14,165,233,0.1)',  fg: '#0ea5e9' },
        config_overlay: { bg: 'rgba(156,163,175,0.1)', fg: '#9ca3af' },
    };

    const _SOURCE_COLORS = {
        built_in: { bg: 'rgba(156,163,175,0.1)', fg: '#9ca3af' },
        global:   { bg: 'rgba(59,130,246,0.1)',   fg: '#3b82f6' },
        team:     { bg: 'rgba(168,85,247,0.1)',   fg: '#a855f7' },
        project:  { bg: 'rgba(34,197,94,0.1)',    fg: '#22c55e' },
        local:    { bg: 'rgba(251,191,36,0.1)',   fg: '#fbbf24' },
        inline:   { bg: 'rgba(239,68,68,0.1)',    fg: '#ef4444' },
    };

    function _badge(text, colors) {
        const c = colors || { bg: 'rgba(156,163,175,0.1)', fg: '#9ca3af' };
        return '<span class="artifact-badge" style="background:' + c.bg + ';color:' + c.fg + '">' + _escapeHtml(text) + '</span>';
    }

    function _typeBadge(type) { return _badge(type, _TYPE_COLORS[type]); }
    function _sourceBadge(source) { return _badge(source, _SOURCE_COLORS[source]); }

    /* ── panel open / close ── */

    function togglePanel() {
        const panel = document.getElementById('artifacts-panel');
        if (panel && panel.style.display !== 'none') {
            closePanel();
        } else {
            openPanel();
        }
    }

    async function openPanel() {
        const canvasPanel = document.getElementById('canvas-panel');
        if (canvasPanel && canvasPanel.style.display !== 'none' && typeof Canvas !== 'undefined') {
            Canvas.closeCanvas();
        }
        const sourcesPanel = document.getElementById('sources-panel');
        if (sourcesPanel && sourcesPanel.style.display !== 'none' && typeof Sources !== 'undefined') {
            Sources.closePanel();
        }

        document.getElementById('artifacts-panel').style.display = '';
        document.querySelector('.chat-main').classList.add('with-artifacts');
        const btn = document.getElementById('btn-artifacts-toggle');
        if (btn) btn.classList.add('active');

        _showListView();
        await _refreshCurrentTab();
    }

    function closePanel() {
        document.getElementById('artifacts-panel').style.display = 'none';
        document.querySelector('.chat-main').classList.remove('with-artifacts');
        const btn = document.getElementById('btn-artifacts-toggle');
        if (btn) btn.classList.remove('active');
    }

    /* ── tab switching ── */

    function _switchTab(tab) {
        _currentTab = tab;
        document.querySelectorAll('.artifacts-view-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.view === tab);
        });
        const typeFilter = document.getElementById('artifacts-type-filter');
        const sourceFilter = document.getElementById('artifacts-source-filter');
        if (typeFilter) typeFilter.style.display = tab === 'artifacts' ? '' : 'none';
        if (sourceFilter) sourceFilter.style.display = tab === 'artifacts' ? '' : 'none';
        _showListView();
        _refreshCurrentTab();
    }

    /* ── view switching ── */

    function _showListView() {
        _currentView = 'list';
        document.getElementById('artifacts-list').style.display = '';
        document.getElementById('artifacts-detail').style.display = 'none';
        document.getElementById('artifacts-toolbar').style.display = '';
    }

    function _showDetailView() {
        _currentView = 'detail';
        document.getElementById('artifacts-list').style.display = 'none';
        document.getElementById('artifacts-detail').style.display = '';
        document.getElementById('artifacts-toolbar').style.display = 'none';
    }

    /* ── refresh lists ── */

    async function _refreshCurrentTab() {
        if (_currentTab === 'artifacts') {
            await _refreshArtifacts();
        } else {
            await _refreshPacks();
        }
    }

    async function _refreshArtifacts() {
        const list = document.getElementById('artifacts-list');
        list.innerHTML = '<div class="artifacts-loading">Loading...</div>';
        try {
            const params = new URLSearchParams();
            const typeFilter = document.getElementById('artifacts-type-filter');
            const sourceFilter = document.getElementById('artifacts-source-filter');
            if (typeFilter && typeFilter.value) params.set('type', typeFilter.value);
            if (sourceFilter && sourceFilter.value) params.set('source', sourceFilter.value);
            const qs = params.toString();
            _artifacts = await App.api('/api/artifacts' + (qs ? '?' + qs : ''));
            list.innerHTML = '';
            if (!_artifacts || _artifacts.length === 0) {
                list.innerHTML = '<div class="artifacts-empty">No artifacts found.</div>';
                return;
            }
            _artifacts.forEach(art => {
                const item = document.createElement('div');
                item.className = 'artifact-item';
                item.innerHTML =
                    '<div class="artifact-item-main">' +
                        '<div class="artifact-item-title">' + _escapeHtml(art.fqn || art.name) + '</div>' +
                        '<div class="artifact-item-meta">' +
                            _typeBadge(art.artifact_type || art.type) +
                            _sourceBadge(art.source) +
                            (art.version ? '<span class="artifact-version">v' + _escapeHtml(String(art.version)) + '</span>' : '') +
                        '</div>' +
                    '</div>';
                item.addEventListener('click', () => _showArtifactDetail(art.fqn));
                list.appendChild(item);
            });
        } catch (err) {
            list.innerHTML = '<div class="artifacts-error">' + DOMPurify.sanitize(err.message || String(err)) + '</div>';
        }
    }

    async function _refreshPacks() {
        const list = document.getElementById('artifacts-list');
        list.innerHTML = '<div class="artifacts-loading">Loading...</div>';
        try {
            _packs = await App.api('/api/packs');
            list.innerHTML = '';
            if (!_packs || _packs.length === 0) {
                list.innerHTML = '<div class="artifacts-empty">No packs installed.</div>';
                return;
            }
            _packs.forEach(pack => {
                const item = document.createElement('div');
                item.className = 'artifact-item';
                const count = pack.artifact_count != null ? pack.artifact_count : '?';
                item.innerHTML =
                    '<div class="artifact-item-main">' +
                        '<div class="artifact-item-title">' + _escapeHtml(pack.namespace + '/' + pack.name) + '</div>' +
                        '<div class="artifact-item-meta">' +
                            '<span class="artifact-version">' + _escapeHtml(pack.version || '') + '</span>' +
                            '<span class="artifact-count">' + _escapeHtml(String(count)) + ' artifacts</span>' +
                        '</div>' +
                    '</div>';
                item.addEventListener('click', () => _showPackDetail(pack.namespace, pack.name));
                list.appendChild(item);
            });
        } catch (err) {
            list.innerHTML = '<div class="artifacts-error">' + DOMPurify.sanitize(err.message || String(err)) + '</div>';
        }
    }

    /* ── detail views ── */

    async function _showArtifactDetail(fqn) {
        _showDetailView();
        const detail = document.getElementById('artifacts-detail');
        detail.innerHTML = '<div class="artifacts-loading">Loading...</div>';
        try {
            const art = await App.api('/api/artifacts/' + encodeURIComponent(fqn));
            let html =
                '<div class="artifact-detail-header">' +
                    '<button class="artifact-back-btn" id="artifact-back-btn">&larr;</button>' +
                    '<span class="artifact-detail-title">' + _escapeHtml(art.name) + '</span>' +
                '</div>' +
                '<div class="artifact-detail-body">' +
                    '<div class="artifact-detail-meta">' +
                        '<div>' + _typeBadge(art.artifact_type || art.type) + ' ' + _sourceBadge(art.source) + '</div>' +
                        '<div class="artifact-detail-fqn">' + _escapeHtml(art.fqn) + '</div>' +
                        (art.version ? '<div class="artifact-version">Version ' + _escapeHtml(String(art.version)) + '</div>' : '') +
                    '</div>' +
                    '<div class="artifact-detail-content"><pre>' + _escapeHtml(art.content || '') + '</pre></div>';

            if (art.versions && art.versions.length > 0) {
                html += '<div class="artifact-detail-versions"><strong>Version History</strong><ul>';
                art.versions.forEach(v => {
                    html += '<li>v' + _escapeHtml(String(v.version)) + ' &mdash; ' + _escapeHtml(v.created_at || '') + '</li>';
                });
                html += '</ul></div>';
            }

            if (art.source !== 'built_in') {
                html += '<div class="artifact-detail-actions">' +
                    '<button class="artifact-delete-btn" id="artifact-delete-btn">Delete</button>' +
                    '</div>';
            }

            html += '</div>';
            detail.innerHTML = html;

            document.getElementById('artifact-back-btn').addEventListener('click', () => {
                _showListView();
                _refreshArtifacts();
            });

            const delBtn = document.getElementById('artifact-delete-btn');
            if (delBtn) {
                delBtn.addEventListener('click', async () => {
                    if (!confirm('Delete artifact ' + art.fqn + '?')) return;
                    try {
                        await App.api('/api/artifacts/' + encodeURIComponent(art.fqn), { method: 'DELETE' });
                        _showListView();
                        await _refreshArtifacts();
                    } catch (e) {
                        alert('Delete failed: ' + (e.message || e));
                    }
                });
            }
        } catch (err) {
            detail.innerHTML = '<div class="artifacts-error">' + DOMPurify.sanitize(err.message || String(err)) + '</div>';
        }
    }

    async function _showPackDetail(namespace, name) {
        _showDetailView();
        const detail = document.getElementById('artifacts-detail');
        detail.innerHTML = '<div class="artifacts-loading">Loading...</div>';
        try {
            const pack = await App.api('/api/packs/' + encodeURIComponent(namespace) + '/' + encodeURIComponent(name));
            let html =
                '<div class="artifact-detail-header">' +
                    '<button class="artifact-back-btn" id="artifact-back-btn">&larr;</button>' +
                    '<span class="artifact-detail-title">' + _escapeHtml(namespace + '/' + name) + '</span>' +
                '</div>' +
                '<div class="artifact-detail-body">' +
                    '<div class="artifact-detail-meta">' +
                        (pack.version ? '<div class="artifact-version">Version ' + _escapeHtml(pack.version) + '</div>' : '') +
                        (pack.description ? '<div class="artifact-detail-desc">' + _escapeHtml(pack.description) + '</div>' : '') +
                    '</div>';

            if (pack.artifacts && pack.artifacts.length > 0) {
                html += '<div class="artifact-detail-content"><strong>Artifacts (' + pack.artifacts.length + ')</strong><ul>';
                pack.artifacts.forEach(a => {
                    html += '<li>' + _typeBadge(a.artifact_type || a.type) + ' ' + _escapeHtml(a.name) + '</li>';
                });
                html += '</ul></div>';
            }

            html += '<div class="artifact-detail-actions">' +
                '<button class="artifact-delete-btn" id="pack-delete-btn">Remove Pack</button>' +
                '</div>';

            html += '</div>';
            detail.innerHTML = html;

            document.getElementById('artifact-back-btn').addEventListener('click', () => {
                _showListView();
                _refreshPacks();
            });

            document.getElementById('pack-delete-btn').addEventListener('click', async () => {
                if (!confirm('Remove pack ' + namespace + '/' + name + '?')) return;
                try {
                    await App.api('/api/packs/' + encodeURIComponent(namespace) + '/' + encodeURIComponent(name), { method: 'DELETE' });
                    _showListView();
                    await _refreshPacks();
                } catch (e) {
                    alert('Remove failed: ' + (e.message || e));
                }
            });
        } catch (err) {
            detail.innerHTML = '<div class="artifacts-error">' + DOMPurify.sanitize(err.message || String(err)) + '</div>';
        }
    }

    /* ── init ── */

    function init() {
        const closeBtn = document.getElementById('artifacts-close');
        if (closeBtn) closeBtn.addEventListener('click', closePanel);

        const toggleBtn = document.getElementById('btn-artifacts-toggle');
        if (toggleBtn) toggleBtn.addEventListener('click', togglePanel);

        document.querySelectorAll('.artifacts-view-tab').forEach(tab => {
            tab.addEventListener('click', () => _switchTab(tab.dataset.view));
        });

        const typeFilter = document.getElementById('artifacts-type-filter');
        if (typeFilter) typeFilter.addEventListener('change', () => _refreshArtifacts());

        const sourceFilter = document.getElementById('artifacts-source-filter');
        if (sourceFilter) sourceFilter.addEventListener('change', () => _refreshArtifacts());
    }

    document.addEventListener('DOMContentLoaded', init);

    return { init, togglePanel, openPanel, closePanel };
})();
