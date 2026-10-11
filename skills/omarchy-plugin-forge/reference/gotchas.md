# Gotchas

Each of these cost real debugging time. Check them before assuming the bug is in your logic.

## Unqualified `Color` is `undefined` in a third-party plugin

Symptom: the whole panel renders black, and the journal shows `Unable to assign [undefined] to QColor`.
Fix: `import qs.Commons as Commons` and write `Commons.Color.background`. Built-in plugins get away with the bare
name; yours does not.

## Hot reload lies

The journal says `Local plugin changed, reloading: <id>`, but:

- A `Panel.qml` loaded through a `Loader` stays cached. If a warning cites line numbers that do not match your
  file, you are looking at the old version.
- `keepLoaded: true` keeps the component mounted across reloads on purpose.

Neither `omarchy plugin disable/enable` fixes it. Run `omarchy-restart-shell` (the bar blinks for a second).
`forge-check.sh` always restarts for this reason. Do not set `keepLoaded` unless the panel must keep state
between openings.

## Reading errors

```bash
journalctl --user --since "-2min" --no-pager | grep <plugin-id>
```

QML errors that do not name your id usually come from your file anyway when they start right after the
restart; check the file path in the message.

## `Process` paths

`Qt.resolvedUrl("bin/x.py")` is a `file://` URL and may be percent-encoded. Strip the scheme and
`decodeURIComponent` it before passing it as an argument. Use `python3`, not `python`.

## Hyprland calls

The shell runs under Hyprland; `hyprctl -j clients` lists windows with `pid`, `address` and `workspace`.
Focus one with `hyprctl dispatch focuswindow address:<address>`. The terminal emulator owns the window, not the
program inside it: walk up the process tree (`/proc/<pid>/stat`, field 4 is the parent) from a PID until one
matches a client. Inside tmux or screen there is no window among the ancestors; say so instead of failing
silently.

## PIDs from hooks and launchers

Hook commands (Claude Code, Hermes) run under an intermediate `sh`, so the hook's `getppid()` is that shell, not
the agent, and it is gone a moment later. Walk up `/proc` to the process you mean, matching on `argv[0]` (or
`argv[1]` for interpreters like `python`/`node`) and skipping shells. Do not match the whole `cmdline`: the hook's
own `sh -c '... --agent hermes'` contains the agent's name. Store the start time too (`/proc/<pid>/stat` field
22) and compare it later: PIDs get reused, and a bare PID check will call a new, unrelated process alive.

## Several sources for the same thing

When two sources can see the same object (an agent seen by its own state files and by a terminal multiplexer),
join before rendering: by a shared id first (a session id), then by context (a variable inherited through the
environment, readable at `/proc/<pid>/environ` for the user's own processes), and keep the more precise source's
state. Unit-test every combination with fixtures; duplicates are the first thing a user notices.

## Demo data the shell can see

The panel's `Process` runs under the shell's environment, not forge-check's, so environment variables set by
`.forge/seed` never reach it. To relocate a data source for screenshots, have the script read a plugin-local
pointer file (`.forge/demo-root`) that seed writes and unseed deletes, and gitignore it.

## Opening things

`xdg-open <url>` for URLs, `omarchy launch` helpers when they exist (`omarchy launch --help`), and the user's
terminal through `xdg-terminal-exec` (it honors the Omarchy default terminal), e.g.
`xdg-terminal-exec --dir=<path>`.

## Rendering in a VM

Under software rendering (`llvmpipe`, typical in a VM) text and simple bars are fine, but per-frame animation in
the shell's JS is slow: a ~600-node force layout cost ~100 ms per frame. Keep panels static or animate with
QML `Behavior`/`NumberAnimation`, not JS loops.

## Reading the kernel instead of spawning tools

Many "run a tool and parse it" sources have a cheaper `/proc` equivalent. Listening TCP sockets: `/proc/net/tcp`
and `/proc/net/tcp6` (state `0A` is LISTEN; the `uid` and `inode` columns tell whose socket it is), mapped to
PIDs through the `socket:[inode]` links in `/proc/<pid>/fd`. The mapping is the costly part: do it only when the
`(port, inode)` set changed or the panel is open. CPU and memory: `/proc/stat`, `/proc/meminfo`. Check the result
against the tool once (`ss -ltnpH`) before trusting it.
