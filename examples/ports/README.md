# Ports in use (mmilei.ports)

Omarchy bar widget for developers: a plug glyph with the number of TCP ports your own processes listen on
(loopback or all interfaces). Click it for the list: port, project folder (the process's working directory, full
path underneath), process name and PID. Per row:

- open `http://localhost:<port>` in the browser (`xdg-open`)
- stop the process with SIGTERM, after a confirmation inside the panel (defaults to Cancel)

Ports owned by other users or the system are not listed (`ss` only reveals the process of your own sockets, and
the script double-checks the owner). The stop action also compares the process start time, so a reused PID is
never signalled.

## Install

```bash
ln -s "$PWD" ~/.config/omarchy/plugins/mmilei.ports
omarchy-shell shell rescanPlugins && omarchy plugin enable mmilei.ports
```

## Settings

| key | default | meaning |
|-----|---------|---------|
| `interval` | 3 | seconds between scans |

## Development

`python3 -m unittest discover -s tests`; `.forge/seed` / `.forge/unseed` start and stop three throwaway
`python3 -m http.server` processes (in temp dirs) for screenshots. Requires `ss` (iproute2) and `xdg-open`.
