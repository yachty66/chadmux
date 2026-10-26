import { expect, test } from "bun:test";
import { PresenceClient, TYPING_IDLE_MS, TYPING_REFRESH_MS } from "./presenceClient";

const ana = { id: "tailnet:ana@example.com", name: "Ana", devices: 1, viewing: true, typing: false };
const me = { id: "owner", name: "Leo", devices: 1, viewing: true, typing: false };

function client() {
  const sent: { sessionId: string; on: boolean }[] = [];
  let changes = 0;
  let clock = 1_000;
  const presence = new PresenceClient(
    (_method, params) => sent.push(params),
    () => (changes += 1),
    () => "owner",
    () => clock,
  );
  return { presence, sent, changes: () => changes, tick: (ms: number) => (clock += ms) };
}

test("an acpmux without presence gets no typing and shows nobody", () => {
  const { presence, sent } = client();
  presence.attached("s1", { events: [] });
  presence.typed("hello");
  expect(sent).toEqual([]);
  expect(presence.presenceOf("s1")).toBeUndefined();
});

test("the attach reply and notifications show the others, never this pane", () => {
  const { presence, changes } = client();
  presence.attached("s1", { presence: { sessionId: "s1", participants: [me] } });
  expect(presence.presenceOf("s1")).toBeUndefined();
  presence.notification({ sessionId: "s1", participants: [me, { ...ana, typing: true }] });
  expect(presence.presenceOf("s1")?.viewing.map((p) => p.id)).toEqual([ana.id]);
  expect(presence.presenceOf("s1")?.typing.map((p) => p.id)).toEqual([ana.id]);
  // Another chat's presence and a switch never show here.
  presence.notification({ sessionId: "s2", participants: [] });
  expect(presence.presenceOf("s2")).toBeUndefined();
  const before = changes();
  presence.notification({ sessionId: "s1", participants: [me, { ...ana, typing: true }] });
  expect(changes()).toBe(before);
  presence.detached();
  expect(presence.presenceOf("s1")).toBeUndefined();
});

test("typing is on once, refreshed before acpmux's expiry, and off on send or empty text", () => {
  const { presence, sent, tick } = client();
  presence.attached("s1", { presence: { sessionId: "s1", participants: [me] } });
  presence.typed("h");
  presence.typed("he");
  expect(sent).toEqual([{ sessionId: "s1", on: true }]);
  tick(TYPING_REFRESH_MS);
  presence.typed("hel");
  expect(sent.length).toBe(2);
  presence.typed("");
  expect(sent.at(-1)).toEqual({ sessionId: "s1", on: false });
  presence.typed("again");
  presence.stopTyping();
  expect(sent.at(-1)).toEqual({ sessionId: "s1", on: false });
  presence.stopTyping();
  expect(sent.filter((s) => !s.on).length).toBe(2);
});

test("no keystroke for the idle time turns typing off", async () => {
  const { presence, sent } = client();
  presence.attached("s1", { presence: { sessionId: "s1", participants: [] } });
  presence.typed("x");
  await new Promise((resolve) => setTimeout(resolve, TYPING_IDLE_MS + 50));
  expect(sent.at(-1)).toEqual({ sessionId: "s1", on: false });
}, TYPING_IDLE_MS + 2_000);
