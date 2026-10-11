#!/usr/bin/env bash
# forge-check.sh <plugin-dir> [--static]
#
# The definition of "done" for an omarchy-shell bar-widget plugin:
#   1. manifest validates           (omarchy plugin validate)
#   2. script tests pass            (python3 -m unittest, when tests/ exists)
#   3. no hardcoded colors in QML   (color-scan.py)
#   4. installs and loads clean     (link, enable, restart shell, journal has no
#                                    error/warning naming the plugin)
#   5. screenshots under two themes (panel open; user's theme restored after)
#
# --static runs 1-3 only (no shell restart, no theme switch).
# FORGE_THEMES="Theme A,Theme B" overrides the two themes used in step 5.
# Exit 0 only when every step that ran passed.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
dir=${1:-}
mode=${2:-}
[[ -n $dir && -f $dir/manifest.json ]] || { echo "usage: forge-check.sh <plugin-dir> [--static]" >&2; exit 2; }
dir=$(cd "$dir" && pwd)
id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$dir/manifest.json") ||
  { echo "FAIL manifest: unreadable or missing id" >&2; exit 1; }

failed=0
step() { printf '\n== %s\n' "$1"; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; failed=1; }

step "1 manifest"
if out=$(omarchy plugin validate "$dir" 2>&1); then pass "validate"; else echo "$out"; fail "validate"; fi

step "2 tests"
if [[ -d $dir/tests ]]; then
  if out=$(cd "$dir" && python3 -m unittest discover -s tests 2>&1); then
    echo "$out" | tail -2; pass "unittest"
  else
    echo "$out" | tail -25; fail "unittest"
  fi
else
  fail "no tests/ directory (the script needs tests)"
fi

step "3 theme colors"
if python3 "$here/color-scan.py" "$dir"; then pass "no hardcoded colors"; else fail "hardcoded colors (see above)"; fi

if [[ $mode == --static ]]; then
  echo; ((failed)) && { echo "RESULT: FAIL"; exit 1; } || { echo "RESULT: PASS (static)"; exit 0; }
fi

step "4 install and load"
link="$HOME/.config/omarchy/plugins/$id"
if [[ -L $link ]]; then
  [[ $(readlink -f "$link") == "$dir" ]] || { fail "$link points elsewhere; not touching it"; echo "RESULT: FAIL"; exit 1; }
elif [[ -e $link ]]; then
  fail "$link exists and is not a link; not touching it"; echo "RESULT: FAIL"; exit 1
else
  mkdir -p "$(dirname "$link")" && ln -s "$dir" "$link"
fi
omarchy-shell shell rescanPlugins >/dev/null 2>&1
omarchy plugin enable "$id" >/dev/null 2>&1
since=$(date '+%Y-%m-%d %H:%M:%S')
omarchy-restart-shell >/dev/null 2>&1
for _ in $(seq 1 30); do omarchy-shell -q shell ping >/dev/null 2>&1 && omarchy-shell shell ping >/dev/null 2>&1 && break; sleep 0.5; done
sleep 3
enabled=$(omarchy plugin list --json 2>/dev/null | python3 -c 'import json,sys; i=sys.argv[1]; print(next((p["enabled"] for p in json.load(sys.stdin) if p["id"]==i), "missing"))' "$id")
[[ $enabled == True ]] && pass "enabled" || fail "plugin state: $enabled"

journal() { journalctl --user --since "$since" --no-pager -o cat 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -F -e "$id" -e "$dir" | grep -iE 'error|warn|unable|undefined|cannot|failed|TypeError|ReferenceError'; }

step "5 panel under two themes"
original=$(omarchy-theme-current 2>/dev/null)
IFS=, read -r theme_a theme_b <<<"${FORGE_THEMES:-Catppuccin Latte,Tokyo Night}"
[[ $original == "$theme_a" ]] && theme_a="Gruvbox"
[[ $original == "$theme_b" ]] && theme_b="Gruvbox"
shots="${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-plugin-forge/$id"
mkdir -p "$shots"
# Optional demo data so the shots show a populated panel: <plugin>/.forge/seed
# runs before the screenshots and <plugin>/.forge/unseed after.
[[ -x $dir/.forge/seed ]] && { "$dir/.forge/seed" && echo "seeded demo data" || fail ".forge/seed"; }

# Changed pixels between two shots; the panel opening must change the screen.
changed() { magick compare -metric AE -fuzz 2% "$1" "$2" null: 2>&1 | cut -d' ' -f1 | cut -d. -f1; }

for theme in "$theme_a" "$theme_b"; do
  omarchy-theme-set "$theme" >/dev/null 2>&1 || { fail "theme-set $theme"; continue; }
  sleep 3
  file="$shots/${theme// /-}.png"
  opened=0
  for _ in 1 2; do
    omarchy-shell -q shell hide "$id" >/dev/null 2>&1; sleep 0.8
    # Wait for the theme transition to finish: the reference shot must be still,
    # or the change it causes would pass for the panel opening.
    grim "$shots/.closed.png"
    for _ in 1 2 3 4 5; do
      sleep 1; grim "$shots/.closed2.png"
      (( $(changed "$shots/.closed.png" "$shots/.closed2.png") < 500 )) && break
      mv "$shots/.closed2.png" "$shots/.closed.png"
    done
    omarchy-shell shell summon "$id" '{}' >/dev/null 2>&1
    sleep 2
    grim "$file"
    (( $(changed "$shots/.closed.png" "$file") > 2000 )) && { opened=1; break; }
  done
  omarchy-shell -q shell hide "$id" >/dev/null 2>&1
  ((opened)) && echo "shot $file" || fail "panel did not open under $theme (screen unchanged after summon)"
done
rm -f "$shots/.closed.png" "$shots/.closed2.png"
[[ -x $dir/.forge/unseed ]] && "$dir/.forge/unseed"
[[ -n $original ]] && omarchy-theme-set "$original" >/dev/null 2>&1 && echo "theme restored: $original"

step "journal since restart"
if hits=$(journal); then echo "$hits" | tail -20; fail "journal errors or warnings naming $id"; else pass "journal clean"; fi

echo
((failed)) && { echo "RESULT: FAIL"; exit 1; }
echo "RESULT: PASS (now look at the screenshots in $shots)"
