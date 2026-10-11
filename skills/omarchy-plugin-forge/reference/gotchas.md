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
program inside it: walk up the process tree from a PID to find the window.

## Opening things

`xdg-open <url>` for URLs, `omarchy launch` helpers when they exist (`omarchy launch --help`), and the user's
terminal through `xdg-terminal-exec` (it honors the Omarchy default terminal), e.g.
`xdg-terminal-exec --dir=<path>`.

## Rendering in a VM

Under software rendering (`llvmpipe`, typical in a VM) text and simple bars are fine, but per-frame animation in
the shell's JS is slow: a ~600-node force layout cost ~100 ms per frame. Keep panels static or animate with
QML `Behavior`/`NumberAnimation`, not JS loops.
