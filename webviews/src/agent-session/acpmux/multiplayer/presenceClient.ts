// The pane's side of a shared chat's presence (hq plans/cmux-next/chat-multiplayer/DESIGN.md
// section 5; hb-acpmux's `_acpmux/presence` and `_acpmux/typing`). It holds the open chat's
// presence from the attach reply and each `_acpmux/presence`, and tells acpmux when this pane types:
// on at the first keystroke, again before acpmux's 8 s expiry while typing goes on, off on send,
// clear or 5 s without a keystroke. Only on/off crosses, never the draft. An acpmux that answered
// no `presence` on attach has neither method, so this pane then sends nothing.
//
// One timer, the idle one: armed on a keystroke, cleared on off, on a chat switch and on close.
import { chatPresence, type ChatPresence } from "./participants";

/// acpmux drops an `on` after 8 s; a pane still typing says so again before that.
export const TYPING_REFRESH_MS = 5_000;
/// No keystroke for this long and the pane says it stopped.
export const TYPING_IDLE_MS = 5_000;

type Send = (method: "_acpmux/typing", params: { sessionId: string; on: boolean }) => void;

export class PresenceClient {
  private sessionId: string | undefined;
  /// This pane's own id in the open chat: the attach reply's `you.id` (a teammate the host daemon
  /// admitted), else the one the pane was made with.
  private self: string;
  private supported = false;
  private current: ChatPresence = { viewing: [], typing: [] };
  private typingOn = false;
  private sentAt = 0;
  private idle: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly send: Send,
    private readonly changed: () => void,
    /// This pane's author id: a local pane sends none, which acpmux names `owner`.
    private readonly me: () => string = () => "owner",
    private readonly now: () => number = Date.now,
  ) {
    this.self = me();
  }

  /// The open chat's presence, or undefined when nobody else is here.
  get presence(): ChatPresence | undefined {
    return this.current.viewing.length > 0 || this.current.typing.length > 0 ? this.current : undefined;
  }

  /// The presence to draw for `sessionId` (the shown chat), so a switch never shows the last chat's.
  presenceOf(sessionId: string | undefined): ChatPresence | undefined {
    return sessionId !== undefined && sessionId === this.sessionId ? this.presence : undefined;
  }

  /// A chat opened (its attach reply): presence when acpmux sends it, else none.
  attached(sessionId: string, reply: unknown): void {
    this.stopTyping();
    this.sessionId = sessionId;
    const { presence, you } = (reply ?? {}) as { presence?: unknown; you?: { id?: unknown } };
    this.self = typeof you?.id === "string" && you.id ? you.id : this.me();
    this.supported = presence !== undefined;
    this.set(this.supported ? chatPresence(presence, this.self) : { viewing: [], typing: [] });
  }

  /// `_acpmux/presence` for any chat; only the open chat's counts.
  notification(params: unknown): void {
    const sessionId = (params as { sessionId?: unknown } | undefined)?.sessionId;
    if (sessionId !== this.sessionId) return;
    this.supported = true;
    this.set(chatPresence(params, this.self));
  }

  /// The composer's text changed: typing while it holds text, stopped when it is empty.
  typed(text: string): void {
    if (!this.supported || !this.sessionId) return;
    if (!text.trim()) return this.stopTyping();
    const now = this.now();
    if (!this.typingOn || now - this.sentAt >= TYPING_REFRESH_MS) {
      this.typingOn = true;
      this.sentAt = now;
      this.send("_acpmux/typing", { sessionId: this.sessionId, on: true });
    }
    if (this.idle) clearTimeout(this.idle);
    this.idle = setTimeout(() => this.stopTyping(), TYPING_IDLE_MS);
  }

  /// A prompt went, or the chat closes: this pane is no longer typing.
  stopTyping(): void {
    if (this.idle) clearTimeout(this.idle);
    this.idle = undefined;
    if (!this.typingOn) return;
    this.typingOn = false;
    if (this.sessionId) this.send("_acpmux/typing", { sessionId: this.sessionId, on: false });
  }

  /// The pane leaves the chat (a switch or close).
  detached(): void {
    this.stopTyping();
    this.sessionId = undefined;
    this.supported = false;
    this.set({ viewing: [], typing: [] });
  }

  private set(next: ChatPresence): void {
    const same = (a: ChatPresence, b: ChatPresence) =>
      a.viewing.map((p) => `${p.id}:${p.name}`).join() === b.viewing.map((p) => `${p.id}:${p.name}`).join() &&
      a.typing.map((p) => p.id).join() === b.typing.map((p) => p.id).join();
    if (same(this.current, next)) return;
    this.current = next;
    this.changed();
  }
}
