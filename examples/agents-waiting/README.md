# Agents waiting

Omarchy bar widget for `omarchy-shell`. A robot glyph in the bar lights up in the theme accent color, with a count,
while Claude Code sessions on this machine are waiting for you. The panel lists every session: project folder,
state, and time since the state changed. Click a row to focus that session's terminal window in Hyprland.

| State | Meaning | Set by |
|-------|---------|--------|
| needs permission (urgent dot) | a tool is waiting for approval | `Notification` |
| waiting for your message (accent dot) | the turn finished | `Stop`, `Notification` (idle) |
| working | running a turn | `UserPromptSubmit` |

Permission and waiting count towards the bar number. Ended sessions (`SessionEnd`, or a dead process) disappear.

## Install

1. Link or add the plugin and enable it: `omarchy plugin enable mmilei.agents-waiting`.
2. Register the hooks. Nothing edits your Claude Code settings for you. Print the exact JSON with the right path:

   ```bash
   python3 ~/.config/omarchy/plugins/mmilei.agents-waiting/bin/agents-waiting.py hooks-config
   ```

   and merge the `hooks` object into `~/.claude/settings.json` (merge arrays if you already have those events):

   ```json
   {
     "hooks": {
       "Notification":     [{ "hooks": [{ "type": "command", "command": "python3 /ABS/PATH/bin/agents-waiting.py hook" }] }],
       "Stop":             [{ "hooks": [{ "type": "command", "command": "python3 /ABS/PATH/bin/agents-waiting.py hook" }] }],
       "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python3 /ABS/PATH/bin/agents-waiting.py hook" }] }],
       "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "python3 /ABS/PATH/bin/agents-waiting.py hook" }] }]
     }
   }
   ```

Sessions already running when you add the hooks only show up after their next event; Claude Code may need a
restart of those sessions to pick the hooks up.

## How it works

Each hook call writes `~/.local/state/mmilei.agents-waiting/sessions/<session_id>.json` (state, cwd, since, and the
PID plus start time of the `claude` process found above the hook's shell). The panel runs `snapshot`, which drops
files whose process is gone. `focus` walks up `/proc` from that PID until it reaches a PID that owns a Hyprland
window (`hyprctl -j clients`) and focuses it.

## Limits

- After you approve a permission, the row stays "needs permission" until the turn ends (`Stop`) because no
  registered event fires in between.
- A terminal inside tmux/screen has no window among its ancestors; the row is listed but clicking does nothing.
  Terminals that share one process for several windows focus the first match.
- `AGENTS_WAITING_STATE` overrides the state directory.

## Settings

`interval`: seconds between refreshes (default 3).

## Tests

`python3 -m unittest discover -s tests`
