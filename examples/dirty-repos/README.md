# Dirty repos (mmilei.dirty-repos)

Omarchy bar widget: a git glyph with the number of repositories under a root folder that have uncommitted
changes or commits not pushed. At zero the count disappears and only the glyph stays. Click it for the list:
each row shows the repo name, its branch and what is pending (`N changed files`, `N ahead`, `N behind`,
`no upstream`) and the path. Clicking a row opens a terminal in that repo (`xdg-terminal-exec --dir=<repo>`).

- **Changed files** counts modified, staged, deleted and untracked files (`git status`, ignored files excluded).
- **Ahead/behind** are relative to the upstream as of the *last fetch*. The widget never fetches.
- **No upstream** is shown for a branch that was never pushed, only when the repo has at least one remote
  (a local-only repo has nowhere to push). Detached HEADs and repos without commits are not flagged for it.
- **Scan**: depth 1 = direct children of the root. It does not descend into a repo it found, hidden folders,
  symlinks, `node_modules`, `venv`, `site-packages`, `target`, `build`, `dist`, `vendor`, `Pods`,
  `bower_components`, `__pycache__`. Submodules are skipped (the parent reports them). A linked `git worktree`
  is listed as its own row (own working tree, own branch), so no commit is counted twice.
- **Read-only**: the script only runs `git --no-optional-locks status` and `git remote` (the first does not
  even refresh the index), plus `rev-parse` before opening a terminal.

## How it refreshes

One long-lived `watch` process (`bin/dirty-repos.py watch`), told `open` / `closed` over stdin; it exits on EOF.
Open: a full scan every `interval` seconds and right when the panel opens. Closed: every `closedInterval`
seconds it compares a fingerprint of each repo (mtimes of `.git/index`, `HEAD`, `packed-refs`, `refs`, and the
work-tree root) and runs `git status` only where it moved; a full rescan every 5 minutes catches edits that
touch none of those, and repos are re-discovered every 10 minutes. It prints only when the result changed.
`snapshot` stays as a one-shot.

## Install

```bash
ln -s "$PWD" ~/.config/omarchy/plugins/mmilei.dirty-repos
omarchy-shell shell rescanPlugins && omarchy plugin enable mmilei.dirty-repos
```

## Settings

| key | default | meaning |
|-----|---------|---------|
| `root` | `~/git` | folder to scan (`~` is expanded) |
| `depth` | 2 | levels below the root to search (1 to 6) |
| `interval` | 60 | seconds between full scans while the panel is open |
| `closedInterval` | 15 | seconds between change checks while the panel is closed |

## Development

`python3 -m unittest discover -s tests`. `.forge/seed` builds throwaway repos (with local bare remotes) in
`$XDG_RUNTIME_DIR/mmilei.dirty-repos-forge` and writes `.forge/demo-root`; while that file exists the script
scans that folder instead of the `root` setting, so screenshots never touch your real settings or repos.
`.forge/unseed` removes both. Requires `git` and `xdg-terminal-exec`.
