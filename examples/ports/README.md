# Ports in use (mmilei.ports)

Omarchy bar widget for developers: a plug glyph with the number of TCP ports your own processes listen on
(loopback or all interfaces). Click it for the list: port, project folder (the process's working directory, full
path underneath), process name and PID. Per row:

- open `http://localhost:<port>` in the browser (`xdg-open`)
- stop the process with SIGTERM, after a confirmation inside the panel (defaults to Cancel)

Ports owned by other users or the system are not listed (only sockets whose owner is you are read). The stop action also compares the process start time, so a reused PID is
never signalled.

## Install

```bash
ln -s "$PWD" ~/.config/omarchy/plugins/mmilei.ports
omarchy-shell shell rescanPlugins && omarchy plugin enable mmilei.ports
```

## Settings

| key | default | meaning |
|-----|---------|---------|
| `interval` | 3 | seconds between scans while the panel is open |
| `closedInterval` | 5 | seconds between checks while the panel is closed |

## Development

`python3 -m unittest discover -s tests`; `.forge/seed` / `.forge/unseed` start and stop three throwaway
`python3 -m http.server` processes (in temp dirs) for screenshots. Requires `xdg-open`.

## Cost

One long-lived `ports.py watch` process feeds the widget (no interpreter start per refresh). Listening sockets
come from `/proc/net/tcp` and `/proc/net/tcp6`; while the panel is closed it only reads those two files every
`closedInterval` seconds and maps sockets to processes (via `/proc/<pid>/fd`) when the set of listening ports
changed. Opening the panel prints the full list immediately and rescans every `interval` seconds.
