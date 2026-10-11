# System resources

Omarchy bar widget for `omarchy-shell`. The bar shows CPU and RAM usage as one short label (`CPU 12%  RAM 45%`
with glyphs); it turns the theme's urgent color when either is at 90% or more. Click it for the panel:

- CPU total plus one meter per core
- RAM and swap (used / total)
- load average (1, 5, 15 min; urgent color when the 1-minute load exceeds the core count)
- CPU temperature, only when the machine exposes a sensor (hwmon `coretemp`/`k10temp`/`zenpower`/`acpitz`, or a
  matching thermal zone). Many VMs have none, and then the field is simply not shown.
- the top 5 processes by CPU. The `x` button on a row asks "Terminate NAME (pid N)?" and sends SIGTERM only after
  you confirm. The dialog opens on Cancel, so a stray Enter does nothing.

Only your own processes get the button. A process's CPU is 100 per busy core, so a multi-threaded one can pass 100.

## Install

```bash
omarchy plugin add <repo-url> --enable --yes      # or link the folder into ~/.config/omarchy/plugins/mmilei.resources
```

Then place the widget with `omarchy bar move` (default section: right).

## Settings

| Key | Default | Meaning |
|-----|---------|---------|
| `interval` | 2 | seconds between samples while the panel is open |
| `closedInterval` | 10 | seconds between bar updates while the panel is closed |
| `processCount` | 5 | processes listed in the panel |

## How it works

The widget runs one long-lived `bin/resources.py watch` and reads a JSON line per tick. While the panel is
closed it computes only total CPU % and RAM % (every `closedInterval` s, from two consecutive reads of `/proc/stat`
and `/proc/meminfo`); when the panel opens the QML writes `open` to its stdin and it adds per-core CPU, swap, load,
temperature and the top processes every `interval` s, sampling a fresh 1 s window first (a CPU tick is 10 ms, so a
shorter window makes per-process numbers jump in steps of several %). `snapshot` is the one-shot form (two samples
1 s apart) used by tests. `kill PID START` re-reads `/proc/PID/stat` and refuses when the start
time no longer matches the one the panel saw (the PID was reused), when the process belongs to another user, or for
PID 1. QML only draws; all logic is in the script and covered by `python3 -m unittest discover -s tests`.
Python stdlib only.
