// A shared chat's presence redraws only the header faces and the typing line (LivePresence.tsx):
// a teammate opening the chat or typing never sends the pane a snapshot, which would rebuild the
// whole transcript. Leo 2026-10-26: "we can still do the regular chat multiplayer".
import { afterAll, beforeAll, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html>", { url: "http://localhost/" });
const scope = globalThis as Record<string, unknown>;
const saved = {
  window: "window" in scope,
  document: "document" in scope,
  values: { window: scope.window, document: scope.document },
};

beforeAll(() => {
  scope.window = dom.window;
  scope.document = dom.window.document;
});
afterAll(() => {
  for (const key of ["window", "document"] as const)
    if (saved[key]) scope[key] = saved.values[key];
    else delete scope[key];
});

type Hooks = {
  answer: (method: string, params: Record<string, unknown>) => Promise<unknown>;
  deliver: (message: unknown) => void;
};

const owner = { id: "owner", name: "Leo", devices: 1, viewing: true, typing: false };
const ana = { id: "tailnet:ana@example.com", name: "Ana", devices: 1, viewing: true, typing: false };

test("a teammate's presence reaches the live faces without a pane snapshot", async () => {
  const { AcpmuxDirectClient } = await import("../direct");
  const { MockAcpmuxSocket, mockHost } = await import("../mock");
  const { shownPresence } = await import("./LivePresence");
  let hooks: Hooks | undefined;
  class SharedHost extends MockAcpmuxSocket {
    constructor() {
      super(() => Promise.resolve());
      hooks = this as unknown as Hooks;
      const answer = hooks.answer.bind(this);
      // An acpmux that serves presence answers it on attach.
      hooks.answer = async (method, params) => {
        const reply = await answer(method, params);
        return method === "_acpmux/attach"
          ? { ...(reply as object), presence: { sessionId: params.sessionId, participants: [owner] } }
          : reply;
      };
    }
  }
  let snapshots = 0;
  let summary: unknown;
  const client = await AcpmuxDirectClient.connect(
    mockHost,
    (snapshot) => {
      snapshots += 1;
      summary = (snapshot as { summary?: unknown }).summary;
    },
    undefined,
    () => new SharedHost() as unknown as WebSocket,
  );
  const settle = async (check: () => boolean) => {
    for (let i = 0; i < 200 && !check(); i += 1) await new Promise((resolve) => setTimeout(resolve, 5));
    expect(check()).toBe(true);
  };
  try {
    await settle(() => summary !== undefined);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const before = snapshots;
    const changes: unknown[] = [];
    const stop = shownPresence.subscribe(() => changes.push(shownPresence.get()));
    hooks!.deliver({
      jsonrpc: "2.0",
      method: "_acpmux/presence",
      params: { sessionId: mockHost.sessionId, participants: [owner, { ...ana, typing: true }] },
    });
    await settle(() => changes.length > 0);
    expect(shownPresence.get()?.viewing.map((p) => p.id)).toEqual([ana.id]);
    expect(shownPresence.get()?.typing.map((p) => p.id)).toEqual([ana.id]);
    // Ana stops typing: the faces change again, and still no snapshot.
    hooks!.deliver({
      jsonrpc: "2.0",
      method: "_acpmux/presence",
      params: { sessionId: mockHost.sessionId, participants: [owner, ana] },
    });
    await settle(() => changes.length > 1);
    expect(shownPresence.get()?.typing).toEqual([]);
    expect(snapshots).toBe(before);
    stop();
  } finally {
    client.close?.();
  }
  // Closed: nobody shows.
  expect(shownPresence.get()).toBeUndefined();
});
