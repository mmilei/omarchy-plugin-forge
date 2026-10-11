# omarchy-plugin-forge

An agent skill that builds [Omarchy](https://omarchy.org) bar plugins from a one-line idea, and a check script
that decides when they are done.

Omarchy 4 ships `omarchy-shell`, a Quickshell/QML bar that loads third-party plugins from a folder. Writing one
is quick once you know the shell, and slow until then: the API is young, a few traps cost an afternoon each, and a
model working from memory will hardcode colors and miss the hot-reload cache. This skill gives the agent the
shape that works, makes it read the shell that is actually installed, and loops it against `forge-check.sh`
until the plugin loads clean and follows the theme.

Scope: **bar widgets with an anchored panel**: an icon or short label in the bar that opens a popup on click.

![The four example plugins](docs/examples.png)

## What `forge-check.sh` checks

```
skills/omarchy-plugin-forge/scripts/forge-check.sh <plugin-dir> [--static]
```

1. **Manifest** validates (`omarchy plugin validate`).
2. **Tests** of the plugin's script pass (`python3 -m unittest`). Every plugin keeps its logic in a stdlib
   Python script that prints JSON; the QML only draws.
3. **Theme colors only.** Any hex literal, named color or constant `Qt.rgba` in the QML fails the check. Colors
   come from `Commons.Color`, `Commons.Style` or the theme's `colors.toml`.
4. **Loads clean.** Links and enables the plugin, restarts the shell, and fails on any journal error or warning
   that names it.
5. **Screenshots under two themes** with the panel open. Fails if the panel did not open, restores your theme
   afterwards. Optional `.forge/seed` and `.forge/unseed` put demo data on screen for the shots.

`--static` runs 1–3 only. The full run restarts the shell (the bar blinks for a second) and switches the theme
twice, so run one at a time.

## Install

**Claude Code**

```bash
claude plugin marketplace add mmilei/omarchy-plugin-forge
claude plugin install omarchy-plugin-forge@omarchy-plugin-forge
```

**Hermes Agent** (or any agent that reads `SKILL.md` folders)

```bash
git clone https://github.com/mmilei/omarchy-plugin-forge
ln -s "$PWD/omarchy-plugin-forge/skills/omarchy-plugin-forge" ~/.hermes/skills/omarchy-plugin-forge
```

Then ask for a plugin:

> Use the omarchy-plugin-forge skill to build a bar plugin that shows the ports my dev servers are listening on.

The agent reads the installed shell, writes the plugin, runs `forge-check.sh` until it passes, looks at the
screenshots and reports anything in the skill that no longer matches your Omarchy version.

## Examples

Each one was built cold: an agent got only the skill and a one-paragraph idea, with no access to this
conversation or to the other examples' history.

| Plugin | What it does | Built by |
|---|---|---|
| [agents-waiting](examples/agents-waiting/) | Inbox of coding agents waiting for you (Claude Code, herdr, reported); click a row to focus it | Claude Code subagent (Sonnet) |
| [ports](examples/ports/) | Ports your dev servers listen on; open in the browser or stop the process | Claude Code subagent (Sonnet) |
| [dirty-repos](examples/dirty-repos/) | Git repos with uncommitted or unpushed work; open a terminal there | Claude Code subagent (Sonnet) |
| [resources](examples/resources/) | CPU, RAM, swap, load and top processes | Hermes Agent (Sonnet) |

After each build, what the agent reported as wrong or missing went back into the skill before the next one.

Install an example by hand:

```bash
ln -s "$PWD/examples/ports" ~/.config/omarchy/plugins/mmilei.ports
omarchy-shell shell rescanPlugins
omarchy plugin enable mmilei.ports
```

## Where it came from

The first plugin was a personal GTD panel: the day's tasks in the bar, a popup with counts, closed-per-day bars
and a one-click done, all read from a folder of markdown notes. Building it by hand surfaced most of the traps
now in `reference/gotchas.md`, and turning those notes into a skill was the obvious next step.

![The GTD panel that started it, with demo data](docs/gtd-panel.png)

## Layout

```
skills/omarchy-plugin-forge/
  SKILL.md               the workflow the agent follows
  reference/patterns.md  manifest, bar widget, panel, script contract, confirm dialogs, theme hues
  reference/gotchas.md   traps found the hard way
  scripts/forge-check.sh the definition of done
  scripts/color-scan.py  the hardcoded-color check
examples/                four plugins built with the skill
.claude-plugin/          Claude Code plugin and marketplace manifests
```

## Tested on

Omarchy 4.0.4 (channel "quattro"), Hyprland 0.56, in a [Try Omarchy for Windows](https://github.com/omacom/try-omarchy-windows)
VM with software rendering. The shell API is still moving; the skill tells the agent to trust the installed
shell over its references, and to report the differences.

## License

MIT
