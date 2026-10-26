// Chat multiplayer (Leo 2026-10-26: "we can still do the regular chat multiplayer"): the pane tells
// a shared chat's other people it types only when the person edits the prompt. A value applied
// from outside is not typing: a chat's draft coming back (read from the daemon on open, with the
// field's own normalizing, or the remount cache on a switch), or a failed prompt restored. Every
// clear says it stopped.
import { afterAll, afterEach, beforeAll, expect, test } from "bun:test";
import { JSDOM } from "jsdom";
import { act, createRef } from "react";
import type { Root } from "react-dom/client";
import { noChat } from "../src/gallery/fixtures/acpmux";
import type { MarkdownFieldElement } from "../src/agent-session/acpmux/MarkdownField";
import type { ComposerHandle } from "../src/agent-session/acpmux/Composer";
import { writePersistedDraft } from "../src/agent-session/acpmux/composerDraft";

const scope = globalThis as Record<string, unknown>;
const keys = [
  "window",
  "document",
  "navigator",
  "HTMLElement",
  "IS_REACT_ACT_ENVIRONMENT",
  "Node",
  "Element",
  "MutationObserver",
  "ResizeObserver",
  "getComputedStyle",
  "requestAnimationFrame",
  "cancelAnimationFrame",
  "localStorage",
] as const;
const saved = keys.map((key) => [key, key in scope, scope[key]] as const);
let dom: JSDOM;
let root: Root | undefined;
let host: HTMLElement;

beforeAll(() => {
  dom = new JSDOM("<!doctype html><div id=root></div>", { pretendToBeVisual: true, url: "https://cmux.test/" });
  const { window } = dom;
  Object.assign(scope, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
    window,
    document: window.document,
    navigator: window.navigator,
    HTMLElement: window.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: true,
    Node: window.Node,
    Element: window.Element,
    MutationObserver: window.MutationObserver,
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: (run: (time: number) => void) => setTimeout(() => run(0), 0),
    cancelAnimationFrame: (id: number) => clearTimeout(id),
    localStorage: window.localStorage,
  });
  // jsdom lays nothing out and its Range has no rects: ProseMirror measures one to scroll a paste
  // into view (a chip's text going back into the prompt goes in as a paste). An empty box will do.
  const box = () => ({ left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) });
  Object.assign(window.Range.prototype, {
    getClientRects: () => Object.assign([box()], { item: () => box() }),
    getBoundingClientRect: box,
  });
  host = window.document.getElementById("root")!;
});

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host.replaceChildren();
  // A saved draft (composerDraft.ts, the page's localStorage remount cache) would come back in
  // the next test's prompt. These tests open chats of their own: the module's pending draft
  // writes outlive a test file.
  dom.window.localStorage.clear();
});

afterAll(() => {
  dom.window.close();
  for (const [key, had, value] of saved) {
    if (had) scope[key] = value;
    else delete scope[key];
  }
});

function Chips() {
  return null;
}

async function render(sessionId: string, typed: string[], handle = createRef<ComposerHandle>()) {
  const { createRoot } = await import("react-dom/client");
  const { Composer } = await import("../src/agent-session/acpmux/Composer");
  const snapshot = noChat([], {
    sessionId,
    summary: { sessionId, harness: "claude", cwd: "/Users/you/src/app", turnCount: 0 },
  });
  root ??= createRoot(host);
  await act(async () =>
    root!.render(
      <Composer
        snapshot={snapshot}
        sessionId={sessionId}
        chips={Chips}
        onSend={() => true}
        onStop={() => {}}
        onProject={() => {}}
        onNewChat={() => {}}
        onTyping={(text) => typed.push(text)}
        handle={handle}
        localName="studio"
        projectChoices={[{ cwd: "/Users/you/src/app", label: "app" }]}
      />,
    ),
  );
  await until(() => !!host.querySelector('[role="combobox"]'), "the editor");
  return handle;
}

async function until(ready: () => boolean, what: string) {
  const deadline = performance.now() + 2_000;
  while (!ready()) {
    if (performance.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await settle();
  }
}

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const prompt = () => host.querySelector('[role="combobox"]')?.textContent ?? "";
const markdownField = () => host.querySelector<MarkdownFieldElement>(".acpmux-md-field")!.acpmuxMarkdownField!;
const page = () =>
  dom.window as unknown as { cmuxAcpmuxActions?: Record<string, (params: unknown) => Promise<unknown>> };

test("a draft read back from the daemon on open is not typing; an edit is", async () => {
  page().cmuxAcpmuxActions = { "chat.readDraft": async () => ({ draft: "half a thought" }) };
  try {
    const typed: string[] = [];
    await render("typing-restore", typed);
    await until(() => prompt() === "half a thought", "the restored draft");
    await settle();
    expect(typed).toEqual([]);
    await act(async () => markdownField().setCaret(Number.MAX_SAFE_INTEGER));
    await act(async () => markdownField().insertTyped(", finished"));
    await until(() => typed.length > 0, "the edit");
    expect(typed.at(-1)).toBe("half a thought, finished");
  } finally {
    delete page().cmuxAcpmuxActions;
  }
});

test("a switch to a chat whose draft the page kept is not typing", async () => {
  writePersistedDraft("typing-b", "kept for b");
  const typed: string[] = [];
  await render("typing-a", typed);
  await act(async () => markdownField().insertTyped("for a"));
  await until(() => typed.length > 0, "the edit");
  typed.length = 0;
  await render("typing-b", typed);
  await until(() => prompt() === "kept for b", "chat b's draft");
  await settle();
  expect(typed).toEqual([]);
});

test("a draft whose trailing space the field normalizes is not typing", async () => {
  page().cmuxAcpmuxActions = { "chat.readDraft": async () => ({ draft: "half a thought " }) };
  try {
    const typed: string[] = [];
    await render("typing-trailing", typed);
    await until(() => prompt().startsWith("half a thought"), "the restored draft");
    await settle();
    await settle();
    expect(typed).toEqual([]);
  } finally {
    delete page().cmuxAcpmuxActions;
  }
});

test("a failed prompt put back is not typing, and a send's clear says it stopped", async () => {
  const typed: string[] = [];
  const handle = await render("typing-sent", typed);
  await act(async () => markdownField().insertTyped("ship it"));
  await until(() => typed.at(-1) === "ship it", "the edit");
  // Sent: the composer clears itself, so this pane stops typing.
  await act(async () => void handle.current!.send());
  await until(() => prompt() === "", "the clear");
  expect(typed.at(-1)).toBe("");
  // The send failed and the prompt comes back: not the person's keystroke.
  typed.length = 0;
  await act(async () => handle.current!.restore("ship it", []));
  await until(() => prompt() === "ship it", "the restored prompt");
  await settle();
  expect(typed).toEqual([]);
});
