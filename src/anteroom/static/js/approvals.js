// Web UI destructive command approvals
(function () {
  function _getCookie(name) {
    const parts = document.cookie.split('; ').map(v => v.split('='));
    for (const [k, v] of parts) {
      if (k === name) return v;
    }
    return '';
  }

  function _ensureModal() {
    if (document.getElementById('approval-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'approval-modal-overlay';
    overlay.style.display = 'none';
    overlay.innerHTML = `
      <div class="approval-backdrop"></div>
      <div class="approval-modal" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <div class="approval-header">
          <div id="approval-title" class="approval-title">Destructive command</div>
        </div>
        <div class="approval-body">
          <pre id="approval-message" class="approval-message"></pre>
        </div>
        <div class="approval-actions">
          <button id="approval-cancel" class="btn">Cancel</button>
          <button id="approval-proceed" class="btn btn-danger">Proceed</button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
  }

  async function _respond(approvalId, approved) {
    const csrf = _getCookie('anteroom_csrf');
    const res = await fetch('/api/approvals/respond', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      },
      body: JSON.stringify({ approval_id: approvalId, approved: !!approved }),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Approval respond failed: HTTP ${res.status} ${text}`);
    }
    return res.json().catch(() => ({}));
  }

  function showApprovalModal(payload) {
    _ensureModal();
    const overlay = document.getElementById('approval-modal-overlay');
    const msgEl = document.getElementById('approval-message');
    const cancelBtn = document.getElementById('approval-cancel');
    const proceedBtn = document.getElementById('approval-proceed');

    const approvalId = payload.approval_id;
    msgEl.textContent = payload.message || '';

    overlay.style.display = '';

    const cleanup = () => {
      overlay.style.display = 'none';
      cancelBtn.onclick = null;
      proceedBtn.onclick = null;
    };

    cancelBtn.onclick = async () => {
      try {
        await _respond(approvalId, false);
      } finally {
        cleanup();
      }
    };

    proceedBtn.onclick = async () => {
      try {
        await _respond(approvalId, true);
      } finally {
        cleanup();
      }
    };
  }

  // expose to app.js SSE listener
  window.Approvals = { showApprovalModal };
})();
