//! A person-readable line for a permission request whose title and input mean
//! nothing to a person: the tool's raw name and its JSON. Leo 2026-10-21 (cmux
//! #89(4)): "The approval prompt from a paired device shows raw JSON
//! ("mcp__optchat__tell wants to run: {"id":"a12","message":…}"). Render it
//! as a human card: "Chief wants to message a12 · Nightly train", with a
//! preview of the message".
//!
//! The hub stamps `toolCall._meta.acpmux.display` (`actor`, `action`,
//! `target`, `targetLabel`, `headline`, `preview`) into the request before
//! anything records or shows it, so every route that carries the request
//! (the event, `_acpmux/permission_pending`, `_acpmux/info`) carries the line,
//! and the request a person answers is the one the hub keeps. Clients show
//! `headline` and `preview` when present and the raw title otherwise; the
//! request's title and input are left as they are.
//!
//! The names come from orchestrator tags: the asker's `mux.label` (else its
//! session name) says who asks, and the target is the session whose
//! `mux.handle` is the input's id among the asker's children (`mux.parent`
//! equal to the asker's `mux.children`), named by its `mux.label`. Two Chiefs
//! on one daemon each have an a12; only the asker's own is named.

use super::*;

/// Who a session is on an approval card.
const LABEL_TAG: &str = "mux.label";
/// What a parent calls a child (the Chief's subagent id, `a12`).
const HANDLE_TAG: &str = "mux.handle";
/// On a parent: the `mux.parent` value its children carry.
const CHILDREN_TAG: &str = "mux.children";
const PARENT_TAG: &str = "mux.parent";

/// Where each parent's children are: the sessions tagged with each `(mux.parent, mux.handle)`, kept
/// as tags are written (`Hub::set_tags`) and as sessions are made (`Hub::make_session`: a load, a
/// new chat, a fork, an import), and the peer row last found for each pair. A lookup reads one
/// entry instead of every session; each entry is a hint that the lookup checks against the
/// session's (or the row's) live tags again, so a stale one names nobody.
#[derive(Default)]
pub(crate) struct ChildTags(std::sync::Mutex<ChildIndex>);

#[derive(Default)]
struct ChildIndex {
    /// Local sessions by `(mux.parent, mux.handle)`.
    local: HashMap<(String, String), std::collections::BTreeSet<String>>,
    /// Each indexed session's pair, to move it when its tags change.
    pair_of: HashMap<String, (String, String)>,
    /// The peer row (`remote_sessions` key) last found for a pair.
    remote: HashMap<(String, String), String>,
}

impl ChildTags {
    fn index(&self) -> std::sync::MutexGuard<'_, ChildIndex> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Indexes `session` under its `mux.parent` and `mux.handle` tags now (or takes it out).
    /// Expiry is not judged here: the lookup checks the live tags.
    pub(crate) fn note(&self, session: &Session) {
        let pair = {
            let meta = session.meta.lock().unwrap_or_else(|e| e.into_inner());
            let raw = |key: &str| meta.tags.get(key).map(|t| t.value.clone());
            raw(PARENT_TAG).zip(raw(HANDLE_TAG))
        };
        let mut index = self.index();
        if let Some(old) = index.pair_of.remove(&session.id)
            && let Some(ids) = index.local.get_mut(&old)
        {
            ids.remove(&session.id);
            if ids.is_empty() {
                index.local.remove(&old);
            }
        }
        if let Some(pair) = pair {
            index.local.entry(pair.clone()).or_default().insert(session.id.clone());
            index.pair_of.insert(session.id.clone(), pair);
        }
    }

    fn local(&self, pair: &(String, String)) -> Vec<String> {
        self.index().local.get(pair).map(|ids| ids.iter().cloned().collect()).unwrap_or_default()
    }
}

/// The longest preview, in characters, ellipsis included.
const PREVIEW_CHARS: usize = 280;

/// A tool whose request reads as "<actor> wants to <action> <target>": the
/// input field that names the target and the one whose text is previewed.
struct Spoken {
    tool: &'static str,
    action: &'static str,
    target: &'static str,
    text: &'static str,
}

/// The tools a person approves by what they do. `mcp__optchat__tell` is the
/// Chief's message to one of its subagents (optchat-chief `tools.rs`).
const SPOKEN: &[Spoken] =
    &[Spoken { tool: "mcp__optchat__tell", action: "message", target: "id", text: "message" }];

/// What a tool call or an ask carries of the agent's own: its input, output and content, and the
/// ask's options. Whatever is inside them (a display-shaped field included) is the agent's data,
/// shown and approved as it is.
const AGENT_DATA: [&str; 4] = ["rawInput", "rawOutput", "content", "options"];

/// Drops the display line (`toolCall._meta.acpmux.display`) of every permission request in
/// `value`, wherever one sits: a pending prompt, an event record, the agent's own ask. What a peer
/// daemon sends (its pending prompts, its event records, its replies) carries no display line of
/// its own to this daemon's clients, since a peer on an older build passes on whatever its agent
/// wrote there. Only this daemon's own stamp (`Hub::stamp_permission_display`) reaches a person;
/// a peer's prompt shows its raw title, as before display lines. Nothing else changes: the
/// agent's data (`AGENT_DATA`) is never looked into.
pub(crate) fn drop_display_lines(value: &mut Value) {
    match value {
        Value::Object(map) => {
            if let Some(acpmux) = map
                .get_mut("toolCall")
                .and_then(|call| call.get_mut("_meta"))
                .and_then(|meta| meta.get_mut("acpmux"))
                .and_then(Value::as_object_mut)
            {
                acpmux.remove("display");
            }
            for (key, child) in map.iter_mut() {
                if !AGENT_DATA.contains(&key.as_str()) {
                    drop_display_lines(child);
                }
            }
        }
        Value::Array(items) => items.iter_mut().for_each(drop_display_lines),
        _ => {}
    }
}

impl Hub {
    /// A logged record as a client reads it (`events_page`): an ask acpmux recorded carries the
    /// display line worked out now, never one the log kept (an older build logged the agent's
    /// words as they came), and the agent's own ask (`in`) carries none.
    pub(in crate::hub) fn display_logged(
        &self,
        session: Option<&Session>,
        record: &mut crate::store::EventRecord,
    ) {
        match record.dir.as_str() {
            "mux" if matches!(record.kind.as_str(), "permission_request" | "permission_reissued") => {
                if let Some(request) = record.msg.get_mut("request") {
                    match session {
                        Some(session) => self.stamp_permission_display(session, request),
                        None => drop_display_lines(request),
                    }
                }
            }
            "in" if record.msg.get("method").and_then(Value::as_str)
                == Some(crate::rpc::method::SESSION_REQUEST_PERMISSION) =>
            {
                if let Some(params) = record.msg.get_mut("params") {
                    drop_display_lines(params);
                }
            }
            _ => {}
        }
    }

    /// Stamps the request's `_meta.acpmux.display` when its tool reads as a
    /// sentence (see the module docs). A display the request came with (the
    /// agent's own words) is dropped first, from every request: only acpmux
    /// writes the line a person reads.
    pub(in crate::hub) fn stamp_permission_display(&self, session: &Session, request: &mut Value) {
        if let Some(acpmux) =
            request.pointer_mut("/toolCall/_meta/acpmux").and_then(Value::as_object_mut)
        {
            acpmux.remove("display");
        }
        let Some(display) = self.permission_display(session, request) else { return };
        let Some(call) = request.get_mut("toolCall").and_then(Value::as_object_mut) else { return };
        let meta = call.entry("_meta").or_insert_with(|| json!({}));
        if !meta.is_object() {
            *meta = json!({});
        }
        let Some(meta) = meta.as_object_mut() else { return };
        let acpmux = meta.entry("acpmux").or_insert_with(|| json!({}));
        if !acpmux.is_object() {
            *acpmux = json!({});
        }
        if let Some(acpmux) = acpmux.as_object_mut() {
            acpmux.insert("display".into(), display);
        }
    }

    fn permission_display(&self, session: &Session, request: &Value) -> Option<Value> {
        let call = request.get("toolCall")?;
        let tool = call
            .pointer("/_meta/claude/tool")
            .or_else(|| call.pointer("/_meta/claudeCode/toolName"))
            .or_else(|| call.get("title"))
            .and_then(Value::as_str)?;
        let spoken = SPOKEN.iter().find(|s| s.tool == tool)?;
        let input = call.get("rawInput")?;
        let target = text(input.get(spoken.target)?)?;
        let (actor, children) = {
            let meta = session.meta.lock().unwrap_or_else(|e| e.into_inner());
            let actor = live_tag(&meta, LABEL_TAG).unwrap_or_else(|| meta.name.clone());
            (actor, live_tag(&meta, CHILDREN_TAG))
        };
        let target_label = children.and_then(|parent| self.child_label(&parent, &target));
        let mut headline = format!("{actor} wants to {} {target}", spoken.action);
        let mut display = json!({"actor": actor, "action": spoken.action, "target": target});
        if let Some(label) = target_label {
            headline.push_str(" · ");
            headline.push_str(&label);
            display["targetLabel"] = Value::String(label);
        }
        display["headline"] = Value::String(headline);
        if let Some(preview) = input.get(spoken.text).and_then(Value::as_str).map(one_line) {
            display["preview"] = Value::String(preview);
        }
        Some(display)
    }

    /// The `mux.label` of the child of `parent` whose handle is `handle`: a
    /// session here, else one a peer lists (a cloud subagent), whose row
    /// carries its live tags.
    /// Local sessions come from the index (`ChildTags`), never a walk over every session; a peer's
    /// rows are walked only when the pair's last row no longer matches.
    fn child_label(&self, parent: &str, handle: &str) -> Option<String> {
        let pair = (parent.to_owned(), handle.to_owned());
        let local = self.child_tags.local(&pair).into_iter().find_map(|id| {
            let s = self.sessions.lock().unwrap_or_else(|e| e.into_inner()).get(&id).cloned()?;
            let meta = s.meta.lock().unwrap_or_else(|e| e.into_inner());
            let mine = live_tag(&meta, PARENT_TAG).as_deref() == Some(parent)
                && live_tag(&meta, HANDLE_TAG).as_deref() == Some(handle);
            if mine { live_tag(&meta, LABEL_TAG) } else { None }
        });
        local.or_else(|| self.peer_child_label(&pair))
    }

    /// `child_label` among the rows peers list (a cloud subagent), whose rows carry their live tags.
    fn peer_child_label(&self, pair: &(String, String)) -> Option<String> {
        let label = |r: &RemoteSession| {
            let tags = r.summary.get("tags")?;
            let tag = |key: &str| tags.get(key).and_then(text);
            let mine = tag(PARENT_TAG).as_deref() == Some(pair.0.as_str())
                && tag(HANDLE_TAG).as_deref() == Some(pair.1.as_str());
            if mine { tag(LABEL_TAG) } else { None }
        };
        let last = self.child_tags.index().remote.get(pair).cloned();
        let rows = self.remote_sessions.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(found) = last.as_ref().and_then(|key| rows.get(key)).and_then(label) {
            return Some(found);
        }
        let (key, found) = rows.iter().find_map(|(key, r)| Some((key.clone(), label(r)?)))?;
        drop(rows);
        self.child_tags.index().remote.insert(pair.clone(), key);
        Some(found)
    }
}

/// `key`'s value on `meta`, unless it expired or is blank.
fn live_tag(meta: &SessionMeta, key: &str) -> Option<String> {
    let tag = meta.tags.get(key)?;
    if tag.expires_at.is_some_and(|e| e <= now_ms()) {
        return None;
    }
    text(&Value::String(tag.value.clone()))
}

fn text(value: &Value) -> Option<String> {
    value.as_str().map(str::trim).filter(|s| !s.is_empty()).map(str::to_owned)
}

/// `text` on one line (runs of whitespace, line breaks included, become one
/// space), at most [`PREVIEW_CHARS`] characters with an ellipsis when cut.
fn one_line(text: &str) -> String {
    let line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() <= PREVIEW_CHARS {
        return line;
    }
    let cut: String = line.chars().take(PREVIEW_CHARS - 1).collect();
    format!("{}…", cut.trim_end())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::peer::PeerNotice;

    /// A hub with peer `name` registered (its link never runs) that lists `sessions`.
    fn hub_with_peer(name: &str, sessions: Vec<Value>) -> (Arc<Hub>, cmux_unix_socket::TestDir) {
        let dir = cmux_unix_socket::short_test_dir("acpmux-pd");
        let mut cfg = crate::config::Config::default();
        cfg.store.mode = crate::config::StoreMode::Local;
        let store = crate::store::open(&cfg.store, dir.path()).unwrap();
        let hub = Hub::new(cfg, store);
        let (tx, _rx) = tokio::sync::mpsc::channel(8);
        let peer = crate::peer::Peer::new(name, "ws://127.0.0.1:9", None, None, tx);
        hub.peers.lock().unwrap().insert(name.to_owned(), peer.clone());
        hub.apply_peer_notice(name, peer.generation, PeerNotice::Sessions(sessions));
        (hub, dir)
    }

    fn row(id: &str, tags: Value) -> Value {
        json!({"sessionId": id, "name": id, "status": "idle", "turnCount": 1, "updatedAt": 5, "tags": tags})
    }

    /// A cloud subagent (optchat-chief `OPTCHAT_SUBAGENT_PEER`) runs on a
    /// peer: its tags come with the peer's session list.
    #[test]
    fn a_child_on_a_peer_is_named_too() {
        let (hub, _dir) = hub_with_peer(
            "cloud-vm",
            vec![
                row(
                    "m1",
                    json!({"mux.parent": "optchat-chief:h0me", "mux.handle": "a12", "mux.label": "Nightly train"}),
                ),
                row(
                    "m2",
                    json!({"mux.parent": "optchat-chief:0ther", "mux.handle": "a13", "mux.label": "Not ours"}),
                ),
            ],
        );
        assert_eq!(hub.child_label("optchat-chief:h0me", "a12").as_deref(), Some("Nightly train"));
        assert_eq!(hub.child_label("optchat-chief:h0me", "a13"), None, "another Chief's child");
        // Found again from the row it was found in, and again after that row moves away.
        assert_eq!(hub.child_label("optchat-chief:h0me", "a12").as_deref(), Some("Nightly train"));
        let generation = hub.peers.lock().unwrap()["cloud-vm"].generation;
        hub.apply_peer_notice(
            "cloud-vm",
            generation,
            PeerNotice::Sessions(vec![row(
                "m3",
                json!({"mux.parent": "optchat-chief:h0me", "mux.handle": "a12", "mux.label": "Moved"}),
            )]),
        );
        assert_eq!(hub.child_label("optchat-chief:h0me", "a12").as_deref(), Some("Moved"));
    }

    /// #24337 review: a child is found through the index, which follows its tags (a session made
    /// from a saved record, as a load makes it, then a tag change) and never names a session gone.
    #[test]
    fn the_child_index_follows_tags() {
        let (hub, _dir) = hub_with_peer("cloud-vm", vec![]);
        // A saved record with its tags, as a load reads it.
        let meta = serde_json::from_value(json!({
            "schema": crate::store::META_SCHEMA, "id": "kid", "name": "kid", "harness": "claude",
            "cwd": "/tmp", "status": "idle", "createdAt": 0, "updatedAt": 0,
            "tags": {"mux.parent": {"value": "optchat-chief:h0me"}, "mux.handle": {"value": "a12"},
                     "mux.label": {"value": "Nightly train"}},
        }))
        .unwrap();
        let kid = hub.make_session(meta);
        hub.sessions.lock().unwrap().insert(kid.id.clone(), kid.clone());
        assert_eq!(hub.child_label("optchat-chief:h0me", "a12").as_deref(), Some("Nightly train"));

        let set: serde_json::Map<String, Value> = [("mux.handle".to_owned(), json!("a14"))].into_iter().collect();
        hub.set_tags(&kid, Some(&set), &[], None);
        assert_eq!(hub.child_label("optchat-chief:h0me", "a12"), None, "its old handle");
        assert_eq!(hub.child_label("optchat-chief:h0me", "a14").as_deref(), Some("Nightly train"));

        hub.sessions.lock().unwrap().remove(&kid.id);
        assert_eq!(hub.child_label("optchat-chief:h0me", "a14"), None, "a session gone");
    }
}
