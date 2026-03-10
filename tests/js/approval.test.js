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
// Helper: reproduces reconnect cleanup from app.js _connectEventSource
// ---------------------------------------------------------------------------
function reconnectCleanup(shownIds) {
    document.querySelectorAll('.approval-prompt:not(.approval-allowed):not(.approval-denied)').forEach(el => {
        const id = el.getAttribute('data-approval-id');
        if (id) shownIds.delete(id);
        el.remove();
    });
    document.querySelectorAll('.ask-user-prompt:not(.ask-user-answered):not(.ask-user-cancelled)').forEach(el => {
        const id = el.getAttribute('data-ask-id');
        if (id) shownIds.delete(id);
        el.remove();
    });
}

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
