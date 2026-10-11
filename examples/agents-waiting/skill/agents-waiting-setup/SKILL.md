---
name: agents-waiting-setup
description: Connect agents to the Omarchy "Agents waiting" bar widget (mmilei.agents-waiting). Claude Code and anything inside herdr need nothing; this wires Hermes Agent's shell hooks and a terminal helper, showing every config change and applying it only after the user says yes. Use when asked to set up, connect or troubleshoot agents-waiting.
---

# agents-waiting-setup

`P=~/.config/omarchy/plugins/mmilei.agents-waiting/bin/agents-waiting.py`

## 1. Check what is already covered

| Agent | Needs setup? |
|---|---|
| Claude Code | No: read from `~/.claude/sessions/` |
| Anything running inside herdr (Claude, Hermes, Codex, …) | No: read from `herdr api snapshot` |
| Hermes Agent outside herdr (desktop app, plain terminal) | Yes: step 2 |
| Your own scripts | Optional: step 3 |

`command -v hermes herdr claude` tells you which exist. Skip what is not installed. Run `python3 $P snapshot`
and show the user the rows it already finds.

## 2. Hermes Agent

Add this block to `~/.hermes/config.yaml`, merging into an existing `hooks:` mapping if there is one (keep every
entry already there). Show the user the final `hooks:` section and wait for an explicit yes before writing:

```yaml
hooks:
  pre_llm_call:           [{command: "python3 <P> hermes-hook", timeout: 5}]
  pre_approval_request:   [{command: "python3 <P> hermes-hook", timeout: 5}]
  post_approval_response: [{command: "python3 <P> hermes-hook", timeout: 5}]
  on_human_input_request: [{command: "python3 <P> hermes-hook", timeout: 5}]
  on_human_input_resolved: [{command: "python3 <P> hermes-hook", timeout: 5}]
  agent_loop_stopped:     [{command: "python3 <P> hermes-hook", timeout: 5}]
  on_session_end:         [{command: "python3 <P> hermes-hook", timeout: 5}]
```

Replace `<P>` with the absolute path (Hermes splits the command with `shlex`, no `~` expansion). Then:

- `hermes hooks doctor` must list the seven entries without errors.
- Hermes asks for consent the first time each hook runs; tell the user to accept.
- Verify: `hermes hooks test agent_loop_stopped`, then `python3 $P snapshot` shows a `hermes` row.

## 3. Terminal helper (optional)

For a long command that should land in the inbox when it finishes, offer this shell function for the user's rc
file (show it, write only on yes):

```bash
aw() {  # aw <command...>: run it, then report done to the Agents waiting widget
  local src="term-$$-$RANDOM"
  python3 <P> report --source "$src" --agent term --state working --message "$*" --cwd "$PWD"
  "$@"; local rc=$?
  python3 <P> report --source "$src" --agent term --state done --message "$* (exit $rc)" --cwd "$PWD"
  return $rc
}
```

## Rules

- Never edit a config file without showing the exact change and getting a yes in the same turn.
- Never touch `~/.claude/settings.json`: Claude Code needs no hooks.
- To undo: remove the added entries; `python3 $P release --source <id>` clears a stuck row.
