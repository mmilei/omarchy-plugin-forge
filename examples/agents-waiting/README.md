# Agents waiting

Omarchy bar widget for `omarchy-shell`: an inbox of coding agents waiting for you. A robot glyph with a count lights
up in the theme accent color while any agent is **blocked** or **waiting**; the whole widget disappears from the bar
when no agent is running at all. The panel lists every agent once, with a title, state, agent name, project and the
time since the state changed. Click a row to jump to it.

| State | Meaning | Counts |
|-------|---------|--------|
| blocked (urgent dot) | needs you: a permission or a question. Only from an explicit signal, never inferred from silence | yes |
| waiting (accent dot) | the turn finished, your move | yes |
| done (accent until clicked) | finished while you were away; stays highlighted until you click the row | no |
| working | running | no |

## Sources

No configuration is needed for the first two.

1. **Claude Code, natively.** Claude Code writes `~/.claude/sessions/<pid>.json` for every live session, interactive
   and background. The plugin reads those, drops a session whose `/proc/<pid>` start time no longer matches
   (PID reuse), and takes the row title from the last `ai-title` in the session transcript (only the tail of the
   file is read), then the session `name`, then the folder name. That format is internal and undocumented: a file
   that does not parse is skipped, and the plugin falls back to `claude agents --json` (documented), at most once
   every 30 s because each call costs about 0.18 s of CPU.
2. **herdr**, when its server runs: `herdr api snapshot`. Panes with a detected agent (Claude, Hermes, Codex, ...)
   become rows. If herdr is not installed or not running this source is silently empty.
3. **Reported state**, for anything else (Hermes Agent outside herdr, your own scripts). See below.

## One agent, one row

The same agent can be seen by several sources. Rows are joined, never duplicated:

1. **Session id first.** herdr stores the agent session id of each pane (`agent_session.value`). If it equals a
   Claude `sessionId` or the `--session-id` of a report, they are the same agent.
2. **Otherwise the herdr pane.** `HERDR_PANE_ID` read from `/proc/<pid>/environ` of the Claude process, or stored by
   `report` when it ran inside a pane.
3. A joined row takes its state from the more precise source (Claude native or reported beat herdr's screen
   detection, as in herdr itself), its title from Claude's `ai-title` when there is one, and focuses through herdr.
   Panes with no agent are not rows. Two Claude sessions in one pane stay two rows.

## Reporting from scripts and Hermes

```bash
python3 ~/.config/omarchy/plugins/mmilei.agents-waiting/bin/agents-waiting.py report \
  --source my-script --agent hermes --state idle --seq 12 --message "done, your move" --cwd "$PWD"
python3 .../agents-waiting.py release --source my-script --seq 13      # remove the row
```

`--state` is `idle|working|blocked|done`. `--seq` must be higher than the last accepted one for that source or the
call is ignored (borrowed from herdr's protocol); it defaults to the current time in ms. `--session-id` is
optional and enables the exact join with herdr. `report` records the agent process (found by walking up from the
caller, skipping hook shells) and its start time, so the row disappears when that process dies, plus
`HERDR_PANE_ID` when set. Both commands exit 0 immediately and print nothing, whatever happens, so hooks can call
them. Files live in `~/.local/state/mmilei.agents-waiting/reported/`. Nothing in your global config is edited:
wire the one-liner into whichever hook you like.

### Hermes Agent

`agents-waiting.py hermes-hook` takes a Hermes shell-hook payload on stdin and maps it: `pre_llm_call`,
`post_approval_response`, `on_human_input_resolved` → working; `pre_approval_request`, `on_human_input_request` →
blocked; `agent_loop_stopped` → waiting; `on_session_end` → release. The Hermes session id is passed along, so a
Hermes session inside herdr still shows once.

### Setup skill

[`skill/agents-waiting-setup/`](skill/agents-waiting-setup/SKILL.md) is a short agent skill (Claude Code, Hermes)
that checks which agents you have, shows the exact `~/.hermes/config.yaml` hooks block and an optional `aw`
shell helper, and writes them only after you say yes.

## Click

| Row | Action |
|-----|--------|
| in a herdr pane (any source) | `herdr agent focus <pane>` |
| Claude interactive, or reported with a PID | focus its terminal window: walk `/proc` parents to a Hyprland client (this also covers the Hermes desktop app, window class `Hermes`) |
| Claude background session (no window) | open a terminal running `claude agents` |

Clicking a done row also acknowledges it (stored in `~/.local/state/mmilei.agents-waiting/acks.json`).

## Requirements

`python3`, `hyprctl` (Hyprland), `xdg-terminal-exec`. Optional: `herdr`, `claude`.

## Install

`omarchy plugin enable mmilei.agents-waiting` after linking or adding the plugin folder.

## Limits

- Claude's `status: waiting` is treated as blocked and `idle` as waiting (assumed from the names; the format is
  undocumented).
- herdr rows get their age from when this plugin first saw the state.
- A terminal inside tmux/screen has no window among its ancestors: clicking does nothing.
- Overrides: `AGENTS_WAITING_STATE`, `AGENTS_WAITING_CLAUDE_DIR`, `AGENTS_WAITING_HERDR_SNAPSHOT`.

## Settings

`interval`: seconds between refreshes while the panel is open (default 3).
`closedInterval`: seconds between cheap change checks while it is closed (default 2).

## Cost

One long-lived `watch` process, not a Python start per refresh. Open, it re-reads everything every `interval`.
Closed, each tick only stats `~/.claude/sessions`, the reported dir and the known agent PIDs, and re-parses when
one moved (plus once a minute for expiry); `herdr api snapshot` is asked at most every 10 s. A session that becomes
blocked or waiting shows up within `closedInterval` seconds, a herdr-only pane within 10 s. `snapshot` stays as the
one-shot form.

## Tests

`python3 -m unittest discover -s tests`

## Credits

Ideas from [ccmanager](https://github.com/h0i5/ccmanager) (reading `~/.claude/sessions`, the `procStart` liveness
check, `ai-title` row titles) and
[herdr](https://herdr.dev) (the report/release protocol, sequence numbers, state vocabulary).
