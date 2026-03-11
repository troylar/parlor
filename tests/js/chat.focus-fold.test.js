import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";

import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function loadChat() {
  const dom = new JSDOM(
    `<!doctype html><html><body><div id="messages-container"></div></body></html>`,
    { url: "http://localhost/" },
  );
  const { window } = dom;

  const context = {
    window,
    document: window.document,
    navigator: window.navigator,
    localStorage: window.localStorage,
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
    setInterval: window.setInterval.bind(window),
    clearInterval: window.clearInterval.bind(window),
    console,
    DOMPurify: { sanitize: (value) => value },
    marked: {
      Renderer: function Renderer() {
        this.link = () => "";
      },
      use: () => {},
      parse: (text) => `<p>${text}</p>`,
    },
    hljs: { highlightElement: () => {} },
    renderMathInElement: () => {},
    App: {
      state: { clientId: "test-client" },
      _getCsrfToken: () => "csrf",
      openSettings: () => {},
    },
    Sidebar: { refresh: () => {}, updateTitle: () => {} },
    Canvas: {
      handleCanvasStreamStart: () => {},
      handleCanvasStreaming: () => {},
      handleCanvasCreated: () => {},
      handleCanvasUpdated: () => {},
      handleCanvasPatched: () => {},
    },
    Attachments: { getFiles: () => [], clear: () => {} },
    Sources: undefined,
    fetch: async () => ({ ok: true, headers: { get: () => "" }, json: async () => ({}) }),
    FormData: window.FormData,
    URL: window.URL,
  };
  context.globalThis = context;

  const scriptPath = path.resolve(__dirname, "../../src/anteroom/static/js/chat.js");
  const source = fs.readFileSync(scriptPath, "utf8") + "\n;globalThis.__chat = Chat;";
  vm.runInNewContext(source, context, { filename: "chat.js" });
  return { Chat: context.__chat, document: window.document };
}

describe("chat focus & fold helpers", () => {
  it("renders text into a reusable assistant segment before and after tool nodes", () => {
    const { Chat, document } = loadChat();
    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    let segment = Chat.__test__.renderAssistantTextSegment(contentEl, null, "Intro before tools.", false);
    const toolBatch = document.createElement("details");
    toolBatch.className = "tool-batch";
    contentEl.appendChild(toolBatch);
    segment = Chat.__test__.renderAssistantTextSegment(contentEl, null, "Follow-up after tools.", false);

    const children = Array.from(contentEl.children).map((el) => ({
      className: el.className,
      text: (el.textContent || "").trim(),
    }));

    expect(children).toEqual([
      { className: "assistant-text-segment", text: "Intro before tools." },
      { className: "tool-batch", text: "" },
      { className: "assistant-text-segment", text: "Follow-up after tools." },
    ]);
    expect(segment.className).toBe("assistant-text-segment");
  });

  it("reuses the existing segment when still attached to the same content container", () => {
    const { Chat, document } = loadChat();
    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    const first = Chat.__test__.renderAssistantTextSegment(contentEl, null, "alpha", false);
    const second = Chat.__test__.renderAssistantTextSegment(contentEl, first, "beta", false);

    expect(second).toBe(first);
    expect(contentEl.querySelectorAll(".assistant-text-segment")).toHaveLength(1);
    expect(first.textContent.trim()).toBe("beta");
  });

  it("creates a fresh segment when the previous one belongs to another container", () => {
    const { Chat, document } = loadChat();
    const oldContainer = document.createElement("div");
    const newContainer = document.createElement("div");
    const oldSegment = Chat.__test__.renderAssistantTextSegment(oldContainer, null, "old", false);

    const newSegment = Chat.__test__.renderAssistantTextSegment(newContainer, oldSegment, "new", false);

    expect(newSegment).not.toBe(oldSegment);
    expect(newContainer.querySelectorAll(".assistant-text-segment")).toHaveLength(1);
    expect(newSegment.textContent.trim()).toBe("new");
  });

  it("preserves chronology across multiple tool batches in one assistant turn", () => {
    const { Chat, document } = loadChat();
    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    Chat.__test__.renderAssistantTextSegment(contentEl, null, "First intro.", false);

    const firstBatch = document.createElement("details");
    firstBatch.className = "tool-batch";
    firstBatch.textContent = "batch one";
    contentEl.appendChild(firstBatch);

    Chat.__test__.renderAssistantTextSegment(contentEl, null, "Between batches.", false);

    const secondBatch = document.createElement("details");
    secondBatch.className = "tool-batch";
    secondBatch.textContent = "batch two";
    contentEl.appendChild(secondBatch);

    Chat.__test__.renderAssistantTextSegment(contentEl, null, "Final follow-up.", false);

    const children = Array.from(contentEl.children).map((el) => ({
      className: el.className,
      text: (el.textContent || "").trim(),
    }));

    expect(children).toEqual([
      { className: "assistant-text-segment", text: "First intro." },
      { className: "tool-batch", text: "batch one" },
      { className: "assistant-text-segment", text: "Between batches." },
      { className: "tool-batch", text: "batch two" },
      { className: "assistant-text-segment", text: "Final follow-up." },
    ]);
  });

  it("does not create an empty assistant segment until text actually arrives after a batch", () => {
    const { Chat, document } = loadChat();
    const contentEl = document.createElement("div");
    contentEl.className = "message-content";

    const batch = document.createElement("details");
    batch.className = "tool-batch";
    contentEl.appendChild(batch);

    const segment = Chat.__test__.ensureAssistantTextSegment(contentEl, null);
    expect(segment.textContent.trim()).toBe("");
    expect(contentEl.lastElementChild).toBe(segment);

    Chat.__test__.renderAssistantTextSegment(contentEl, segment, "Late text.", false);

    const children = Array.from(contentEl.children).map((el) => ({
      className: el.className,
      text: (el.textContent || "").trim(),
    }));

    expect(children).toEqual([
      { className: "tool-batch", text: "" },
      { className: "assistant-text-segment", text: "Late text." },
    ]);
  });
});
