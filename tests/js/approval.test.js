/**
 * JS unit tests for approval/ask_user prompt rendering (#864).
 *
 * These tests verify DOM insertion logic and reconnect cleanup
 * by reproducing the same patterns used in chat.js and app.js.
 */

import { describe, it, expect, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// Helper: reproduces _insertPromptCard from chat.js
// ---------------------------------------------------------------------------
function insertPromptCard(container, el) {
    const assistantMsgs = container.querySelectorAll('.message.assistant');
    const lastAssistant = assistantMsgs.length > 0 ? assistantMsgs[assistantMsgs.length - 1] : null;
    if (lastAssistant) {
        let insertBefore = lastAssistant.nextSibling;
        while (insertBefore && (insertBefore.classList?.contains('approval-prompt') || insertBefore.classList?.contains('ask-user-prompt'))) {
            insertBefore = insertBefore.nextSibling;
        }
        if (insertBefore) {
            container.insertBefore(el, insertBefore);
        } else {
            container.appendChild(el);
        }
    } else {
        container.appendChild(el);
    }
}

// ---------------------------------------------------------------------------
// Load the actual reconnect cleanup logic from the production source (#864).
// This is the same function used by chat.js and app.js — no duplication.
// ---------------------------------------------------------------------------
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const cleanupSrc = readFileSync(
    resolve(__dirname, '../../src/anteroom/static/js/prompt-cleanup.js'),
    'utf-8',
);
// Evaluate the production script to define cleanupPendingPrompts as a global,
// exactly as a browser <script> tag would.
const _fn = new Function(cleanupSrc + '\nreturn cleanupPendingPrompts;');
const reconnectCleanup = _fn();

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------
function makeContainer() {
    const c = document.createElement('div');
    c.id = 'messages-container';
    document.body.appendChild(c);
    return c;
}

function makeMsg(role) {
    const el = document.createElement('div');
    el.className = `message ${role}`;
    return el;
}

function makePromptCard(type, id) {
    const el = document.createElement('div');
    if (type === 'approval') {
        el.className = 'approval-prompt';
        el.setAttribute('data-approval-id', id);
    } else {
        el.className = 'ask-user-prompt';
        el.setAttribute('data-ask-id', id);
    }
    return el;
}

// ---------------------------------------------------------------------------
// Tests: prompt card insertion ordering
// ---------------------------------------------------------------------------
describe('_insertPromptCard ordering', () => {
    let container;

    beforeEach(() => {
        document.body.innerHTML = '';
        container = makeContainer();
    });

    it('inserts after last assistant message, before user message', () => {
        const assistant = makeMsg('assistant');
        const user = makeMsg('user');
        container.appendChild(assistant);
        container.appendChild(user);

        const card = makePromptCard('ask-user', 'ask-1');
        insertPromptCard(container, card);

        const children = [...container.children];
        expect(children[0]).toBe(assistant);
        expect(children[1]).toBe(card);
        expect(children[2]).toBe(user);
    });

    it('appends to end when no assistant message exists', () => {
        const user = makeMsg('user');
        container.appendChild(user);

        const card = makePromptCard('approval', 'appr-1');
        insertPromptCard(container, card);

        const children = [...container.children];
        expect(children[0]).toBe(user);
        expect(children[1]).toBe(card);
    });

    it('inserts after the LAST assistant message when multiple exist', () => {
        const a1 = makeMsg('assistant');
        const u1 = makeMsg('user');
        const a2 = makeMsg('assistant');
        const u2 = makeMsg('user');
        container.appendChild(a1);
        container.appendChild(u1);
        container.appendChild(a2);
        container.appendChild(u2);

        const card = makePromptCard('ask-user', 'ask-2');
        insertPromptCard(container, card);

        const children = [...container.children];
        expect(children[0]).toBe(a1);
        expect(children[1]).toBe(u1);
        expect(children[2]).toBe(a2);
        expect(children[3]).toBe(card);
        expect(children[4]).toBe(u2);
    });

    it('appends to end when last assistant is the last child', () => {
        const a1 = makeMsg('assistant');
        container.appendChild(a1);

        const card = makePromptCard('approval', 'appr-2');
        insertPromptCard(container, card);

        const children = [...container.children];
        expect(children[0]).toBe(a1);
        expect(children[1]).toBe(card);
    });

    it('handles empty container gracefully', () => {
        const card = makePromptCard('ask-user', 'ask-3');
        insertPromptCard(container, card);

        expect(container.children.length).toBe(1);
        expect(container.children[0]).toBe(card);
    });

    it('multiple prompt cards stack after same assistant', () => {
        const assistant = makeMsg('assistant');
        const user = makeMsg('user');
        container.appendChild(assistant);
        container.appendChild(user);

        const card1 = makePromptCard('approval', 'appr-3');
        insertPromptCard(container, card1);
        const card2 = makePromptCard('ask-user', 'ask-4');
        insertPromptCard(container, card2);

        const children = [...container.children];
        // Cards skip over existing prompt cards, so they stack in FIFO order
        expect(children[0]).toBe(assistant);
        expect(children[1]).toBe(card1);
        expect(children[2]).toBe(card2);
        expect(children[3]).toBe(user);
    });
});

// ---------------------------------------------------------------------------
// Tests: reconnect cleanup
// ---------------------------------------------------------------------------
describe('reconnect cleanup', () => {
    let shownIds;

    beforeEach(() => {
        document.body.innerHTML = '';
        shownIds = new Set();
    });

    it('removes pending approval prompts', () => {
        const card = makePromptCard('approval', 'appr-pending');
        document.body.appendChild(card);
        shownIds.add('appr-pending');

        reconnectCleanup(shownIds);

        expect(document.querySelector('.approval-prompt')).toBeNull();
        expect(shownIds.has('appr-pending')).toBe(false);
    });

    it('preserves resolved (allowed) approval prompts', () => {
        const card = makePromptCard('approval', 'appr-allowed');
        card.classList.add('approval-allowed');
        document.body.appendChild(card);
        shownIds.add('appr-allowed');

        reconnectCleanup(shownIds);

        expect(document.querySelector('.approval-prompt')).not.toBeNull();
        expect(shownIds.has('appr-allowed')).toBe(true);
    });

    it('preserves resolved (denied) approval prompts', () => {
        const card = makePromptCard('approval', 'appr-denied');
        card.classList.add('approval-denied');
        document.body.appendChild(card);
        shownIds.add('appr-denied');

        reconnectCleanup(shownIds);

        expect(document.querySelector('.approval-prompt')).not.toBeNull();
    });

    it('removes pending ask_user prompts', () => {
        const card = makePromptCard('ask-user', 'ask-pending');
        document.body.appendChild(card);
        shownIds.add('ask-pending');

        reconnectCleanup(shownIds);

        expect(document.querySelector('.ask-user-prompt')).toBeNull();
        expect(shownIds.has('ask-pending')).toBe(false);
    });

    it('preserves answered ask_user prompts', () => {
        const card = makePromptCard('ask-user', 'ask-answered');
        card.classList.add('ask-user-answered');
        document.body.appendChild(card);
        shownIds.add('ask-answered');

        reconnectCleanup(shownIds);

        expect(document.querySelector('.ask-user-prompt')).not.toBeNull();
        expect(shownIds.has('ask-answered')).toBe(true);
    });

    it('preserves cancelled ask_user prompts', () => {
        const card = makePromptCard('ask-user', 'ask-cancelled');
        card.classList.add('ask-user-cancelled');
        document.body.appendChild(card);

        reconnectCleanup(shownIds);

        expect(document.querySelector('.ask-user-prompt')).not.toBeNull();
    });

    it('handles mix of pending and resolved prompts', () => {
        const pending1 = makePromptCard('approval', 'p1');
        const resolved1 = makePromptCard('approval', 'r1');
        resolved1.classList.add('approval-allowed');
        const pending2 = makePromptCard('ask-user', 'p2');
        const resolved2 = makePromptCard('ask-user', 'r2');
        resolved2.classList.add('ask-user-answered');

        document.body.appendChild(pending1);
        document.body.appendChild(resolved1);
        document.body.appendChild(pending2);
        document.body.appendChild(resolved2);
        shownIds.add('p1');
        shownIds.add('r1');
        shownIds.add('p2');
        shownIds.add('r2');

        reconnectCleanup(shownIds);

        expect(document.querySelectorAll('.approval-prompt').length).toBe(1);
        expect(document.querySelectorAll('.ask-user-prompt').length).toBe(1);
        expect(shownIds.has('p1')).toBe(false);
        expect(shownIds.has('r1')).toBe(true);
        expect(shownIds.has('p2')).toBe(false);
        expect(shownIds.has('r2')).toBe(true);
    });
});

// ---------------------------------------------------------------------------
// Inline reproductions of resolveApprovalCard / resolveAskUserCard (#870).
// chat.js is an IIFE and cannot be imported as an ES module, so we reproduce
// the exact logic here to unit-test it in isolation with jsdom.
//
// jsdom does not implement CSS.escape; test IDs are safe ASCII so we skip
// escaping here. The escaping behaviour is covered by the Playwright E2E tests
// which run in a real browser that has CSS.escape.
// ---------------------------------------------------------------------------
function resolveApprovalCard(approvalId, approved, reason) {
    const el = document.querySelector(`[data-approval-id="${approvalId}"]`);
    if (!el || el.classList.contains('approval-allowed') || el.classList.contains('approval-denied') || el.classList.contains('approval-expired')) return;
    if (reason === 'timed_out') {
        el.classList.add('approval-expired');
    } else {
        el.classList.add(approved ? 'approval-allowed' : 'approval-denied');
    }
    const status = document.createElement('div');
    status.className = 'approval-status';
    if (reason === 'timed_out') {
        status.textContent = 'Expired \u2014 the agent moved on';
    } else {
        status.textContent = approved ? 'Allowed' : 'Denied';
    }
    const actionsEl = el.querySelector('.approval-actions');
    if (actionsEl) actionsEl.replaceWith(status);
}

function resolveAskUserCard(askId, reason) {
    const el = document.querySelector(`[data-ask-id="${askId}"]`);
    if (!el || el.classList.contains('ask-user-answered') || el.classList.contains('ask-user-cancelled') || el.classList.contains('ask-user-expired')) return;
    el.classList.add('ask-user-expired');
    const status = document.createElement('div');
    status.className = 'ask-user-status';
    status.textContent = 'Expired \u2014 the agent moved on';
    const actionsEl = el.querySelector('.ask-user-actions');
    if (actionsEl) actionsEl.replaceWith(status);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function makeApprovalCard(id) {
    const el = document.createElement('div');
    el.className = 'approval-prompt';
    el.setAttribute('data-approval-id', id);
    const actions = document.createElement('div');
    actions.className = 'approval-actions';
    el.appendChild(actions);
    document.body.appendChild(el);
    return el;
}

function makeAskUserCard(id) {
    const el = document.createElement('div');
    el.className = 'ask-user-prompt';
    el.setAttribute('data-ask-id', id);
    const actions = document.createElement('div');
    actions.className = 'ask-user-actions';
    el.appendChild(actions);
    document.body.appendChild(el);
    return el;
}

// ---------------------------------------------------------------------------
// Tests: resolveApprovalCard timeout expiry (#870)
// ---------------------------------------------------------------------------
describe('resolveApprovalCard — timed_out', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    it('adds approval-expired class when reason is timed_out', () => {
        const card = makeApprovalCard('appr-exp-1');
        resolveApprovalCard('appr-exp-1', false, 'timed_out');
        expect(card.classList.contains('approval-expired')).toBe(true);
    });

    it('does not add approval-allowed or approval-denied when timed_out', () => {
        const card = makeApprovalCard('appr-exp-2');
        resolveApprovalCard('appr-exp-2', true, 'timed_out');
        expect(card.classList.contains('approval-allowed')).toBe(false);
        expect(card.classList.contains('approval-denied')).toBe(false);
    });

    it('replaces approval-actions with approval-status element', () => {
        const card = makeApprovalCard('appr-exp-3');
        resolveApprovalCard('appr-exp-3', false, 'timed_out');
        expect(card.querySelector('.approval-actions')).toBeNull();
        expect(card.querySelector('.approval-status')).not.toBeNull();
    });

    it('status text includes "Expired"', () => {
        const card = makeApprovalCard('appr-exp-4');
        resolveApprovalCard('appr-exp-4', false, 'timed_out');
        const status = card.querySelector('.approval-status');
        expect(status.textContent).toContain('Expired');
    });

    it('status text says "Expired — the agent moved on"', () => {
        const card = makeApprovalCard('appr-exp-5');
        resolveApprovalCard('appr-exp-5', false, 'timed_out');
        const status = card.querySelector('.approval-status');
        expect(status.textContent).toBe('Expired \u2014 the agent moved on');
    });

    it('is idempotent — second call on expired card does nothing', () => {
        const card = makeApprovalCard('appr-exp-6');
        resolveApprovalCard('appr-exp-6', false, 'timed_out');
        resolveApprovalCard('appr-exp-6', false, 'timed_out');
        expect(card.querySelectorAll('.approval-status').length).toBe(1);
    });

    it('does not affect already-allowed card', () => {
        const card = makeApprovalCard('appr-exp-7');
        card.classList.add('approval-allowed');
        resolveApprovalCard('appr-exp-7', false, 'timed_out');
        expect(card.classList.contains('approval-expired')).toBe(false);
        expect(card.classList.contains('approval-allowed')).toBe(true);
    });

    it('does not affect already-denied card', () => {
        const card = makeApprovalCard('appr-exp-8');
        card.classList.add('approval-denied');
        resolveApprovalCard('appr-exp-8', false, 'timed_out');
        expect(card.classList.contains('approval-expired')).toBe(false);
    });

    it('normal approval (no timed_out) still adds approval-allowed', () => {
        const card = makeApprovalCard('appr-exp-9');
        resolveApprovalCard('appr-exp-9', true, undefined);
        expect(card.classList.contains('approval-allowed')).toBe(true);
        expect(card.classList.contains('approval-expired')).toBe(false);
        const status = card.querySelector('.approval-status');
        expect(status.textContent).toBe('Allowed');
    });

    it('normal denial (no timed_out) still adds approval-denied', () => {
        const card = makeApprovalCard('appr-exp-10');
        resolveApprovalCard('appr-exp-10', false, undefined);
        expect(card.classList.contains('approval-denied')).toBe(true);
        expect(card.classList.contains('approval-expired')).toBe(false);
        const status = card.querySelector('.approval-status');
        expect(status.textContent).toBe('Denied');
    });

    it('is a no-op for a non-existent element', () => {
        // Should not throw
        expect(() => resolveApprovalCard('does-not-exist', false, 'timed_out')).not.toThrow();
    });

    it('expired card is removed by reconnect cleanup', () => {
        // cleanupPendingPrompts uses :not(.approval-allowed):not(.approval-denied),
        // so .approval-expired cards (lacking those classes) are removed on reconnect.
        makeApprovalCard('appr-exp-cleanup');
        resolveApprovalCard('appr-exp-cleanup', false, 'timed_out');
        const shownIds = new Set(['appr-exp-cleanup']);
        reconnectCleanup(shownIds);
        expect(document.querySelector('.approval-prompt')).toBeNull();
        expect(shownIds.has('appr-exp-cleanup')).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// Tests: resolveAskUserCard timeout expiry (#870)
// ---------------------------------------------------------------------------
describe('resolveAskUserCard — timed_out', () => {
    beforeEach(() => {
        document.body.innerHTML = '';
    });

    it('adds ask-user-expired class', () => {
        const card = makeAskUserCard('ask-exp-1');
        resolveAskUserCard('ask-exp-1', 'timed_out');
        expect(card.classList.contains('ask-user-expired')).toBe(true);
    });

    it('does not add ask-user-answered or ask-user-cancelled', () => {
        const card = makeAskUserCard('ask-exp-2');
        resolveAskUserCard('ask-exp-2', 'timed_out');
        expect(card.classList.contains('ask-user-answered')).toBe(false);
        expect(card.classList.contains('ask-user-cancelled')).toBe(false);
    });

    it('replaces ask-user-actions with ask-user-status element', () => {
        const card = makeAskUserCard('ask-exp-3');
        resolveAskUserCard('ask-exp-3', 'timed_out');
        expect(card.querySelector('.ask-user-actions')).toBeNull();
        expect(card.querySelector('.ask-user-status')).not.toBeNull();
    });

    it('status text includes "Expired"', () => {
        const card = makeAskUserCard('ask-exp-4');
        resolveAskUserCard('ask-exp-4', 'timed_out');
        const status = card.querySelector('.ask-user-status');
        expect(status.textContent).toContain('Expired');
    });

    it('status text says "Expired — the agent moved on"', () => {
        const card = makeAskUserCard('ask-exp-5');
        resolveAskUserCard('ask-exp-5', 'timed_out');
        const status = card.querySelector('.ask-user-status');
        expect(status.textContent).toBe('Expired \u2014 the agent moved on');
    });

    it('is idempotent — second call on expired card does nothing', () => {
        const card = makeAskUserCard('ask-exp-6');
        resolveAskUserCard('ask-exp-6', 'timed_out');
        resolveAskUserCard('ask-exp-6', 'timed_out');
        expect(card.querySelectorAll('.ask-user-status').length).toBe(1);
    });

    it('does not affect already-answered card', () => {
        const card = makeAskUserCard('ask-exp-7');
        card.classList.add('ask-user-answered');
        resolveAskUserCard('ask-exp-7', 'timed_out');
        expect(card.classList.contains('ask-user-expired')).toBe(false);
        expect(card.classList.contains('ask-user-answered')).toBe(true);
    });

    it('does not affect already-cancelled card', () => {
        const card = makeAskUserCard('ask-exp-8');
        card.classList.add('ask-user-cancelled');
        resolveAskUserCard('ask-exp-8', 'timed_out');
        expect(card.classList.contains('ask-user-expired')).toBe(false);
    });

    it('is a no-op for a non-existent element', () => {
        expect(() => resolveAskUserCard('does-not-exist', 'timed_out')).not.toThrow();
    });

    it('expired card is removed by reconnect cleanup', () => {
        // cleanupPendingPrompts uses :not(.ask-user-answered):not(.ask-user-cancelled),
        // so .ask-user-expired cards (lacking those classes) are removed on reconnect.
        makeAskUserCard('ask-exp-9');
        resolveAskUserCard('ask-exp-9', 'timed_out');
        const shownIds = new Set(['ask-exp-9']);
        reconnectCleanup(shownIds);
        expect(document.querySelector('.ask-user-prompt')).toBeNull();
        expect(shownIds.has('ask-exp-9')).toBe(false);
    });
});
