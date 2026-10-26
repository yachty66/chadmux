// The shown chat's presence, live, outside the pane's snapshot: a teammate opening the chat or
// typing redraws only the header faces and the typing line, never the transcript (the snapshot
// listener rebuilds the whole pane). The direct client sets it (direct.ts `presence`); a snapshot's
// own `presence` (a gallery fixture) draws while no client has set one.
import { useSyncExternalStore } from "react";
import { PresenceStack, TypingLine } from "./Faces";
import type { ChatPresence } from "./participants";

let current: ChatPresence | undefined;
const listeners = new Set<() => void>();

export const shownPresence = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  get: (): ChatPresence | undefined => current,
  set(next: ChatPresence | undefined): void {
    if (next === current) return;
    current = next;
    for (const listener of listeners) listener();
  },
};

const useShown = (fallback: ChatPresence | undefined) =>
  useSyncExternalStore(shownPresence.subscribe, shownPresence.get, shownPresence.get) ?? fallback;

/// Who else has the chat open, in the header.
export function LivePresenceStack({ fallback }: { fallback?: ChatPresence }) {
  const presence = useShown(fallback);
  return presence ? <PresenceStack viewing={presence.viewing} /> : null;
}

/// Who else types, over the composer.
export function LiveTypingLine({ fallback }: { fallback?: ChatPresence }) {
  const presence = useShown(fallback);
  return presence ? <TypingLine typing={presence.typing} /> : null;
}
