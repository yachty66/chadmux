set -o pipefail
cd cmux-tui
cargo fmt --all -- --check 2>&1 | head -80; echo FMT-DONE
cargo clippy -p cmux-tui-core --all-targets -- -D warnings 2>&1 | grep -E "^(warning|error)" -A8 | grep -E "^(warning|error)|-->" | grep -v "crates/acpmux" | head -30; echo CLIPPY-DONE
cargo test -p cmux-tui-core agent_session 2>&1 | grep -E "^test result: ok. [1-9]|FAILED|panicked|^test .* FAILED" | head -20
cargo test -p cmux-tui-core conversation_tab 2>&1 | grep -E "^test result: ok. [1-9]|FAILED|panicked" | head
cargo test -p cmux-tui-core line_connection 2>&1 | grep -E "^test result: ok. [1-9]|FAILED|panicked" | head
mkdir -p /tmp/o && cp crates/cmux-tui-core/src/server/conversation_tabs_wire.rs crates/cmux-tui-core/src/server/line_connection.rs /tmp/o/
echo NEG-ATOMIC
python3 - <<'P'
p="crates/cmux-tui-core/src/server/conversation_tabs_wire.rs"; s=open(p).read()
a="    let _rebinding = mux.control_clients.agent_sessions.rebinding();\n"
assert a in s; open(p,"w").write(s.replace(a,""))
P
cargo test -p cmux-tui-core a_rebind_waits_for_a_teammate_write 2>&1 | grep -E "^test result|panicked" | head -3
cp /tmp/o/conversation_tabs_wire.rs crates/cmux-tui-core/src/server/
echo NEG-BLANK
python3 - <<'P'
p="crates/cmux-tui-core/src/server/line_connection.rs"; s=open(p).read()
a="            // A blank line is a line too: a proxy's bind must be the very first.\n            #[cfg(unix)]\n            if !first_line_done {"
assert a in s; open(p,"w").write(s.replace(a,"            #[cfg(unix)]\n            if false && !first_line_done {"))
P
cargo test -p cmux-tui-core a_bind_after_a_blank_line 2>&1 | grep -E "^test result|panicked" | head -3
true
