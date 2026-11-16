#!/usr/bin/env bash
#
# Repair the UniXNU checkout before `unixnu build ...` runs.
#
# `unixnu` runs `git submodule update --init --recursive` itself (scripts/unixnu/
# feeds.py, update_submodules) and treats a failure as fatal, so the build never
# starts if one gitlink is unusable.  UniXNU/UniXNU's `main` currently records
# three gitlinks whose commits the submodule remotes no longer serve (upstream
# force-pushed):
#
#   drivers/DarwinBoot      d099354d0eab34df5088ca1ba89c9c88bc3260bb
#   drivers/NativeAdreno    5cf11357a1b35ca8424baa2721e4578df1890bde
#   drivers/NativeRamBlock  d4d22d9819b0162eda49f6402235dddc2279410d
#
# Rather than hard-coding that list (it will rot), walk every submodule in
# .gitmodules, update it on its own, and when that fails re-point the gitlink at
# the remote's default-branch head -- which by definition is being served -- and
# try again.  The re-point is index-only: nothing is committed, and the engine
# still gets a fully populated tree.
#
# After the checkout is healthy, applies the hummer SMMU stream-table patch; see
# the block at the bottom.
#
# Usage:  cd UniXNU && bash ../ci/fix-unixnu-submodules.sh
#
set -uo pipefail

export GIT_TERMINAL_PROMPT=0

if [ ! -f .gitmodules ]; then
	echo "fix-unixnu-submodules: no .gitmodules here; run from the UniXNU checkout" >&2
	exit 1
fi

if ! git config -f .gitmodules --get-regexp '^submodule\..*\.path$' > submodules.txt; then
	echo "fix-unixnu-submodules: .gitmodules has no submodule entries"
	rm -f submodules.txt
else
	fixed=0
	failed=0

	# No pipe: the loop body has to be able to set the counters.
	while read -r key path; do
		[ -n "${path:-}" ] || continue
		name=${key#submodule.}
		name=${name%.path}

		if git submodule update --init --recursive -- "$path" < /dev/null; then
			continue
		fi

		url=$(git config -f .gitmodules --get "submodule.$name.url")
		case "$url" in
			./*|../*) url="https://github.com/UniXNU/$(basename "$path").git" ;;
		esac

		sha=$(git ls-remote "$url" HEAD 2>/dev/null | awk '$2 == "HEAD" { print $1; exit }')
		if [ -z "$sha" ]; then
			echo "::error::$path is pinned to a commit upstream no longer serves, and $url advertises no HEAD"
			failed=$((failed + 1))
			continue
		fi

		old=$(git ls-tree HEAD -- "$path" | awk '{ print $3 }')
		echo "::warning::$path is pinned to ${old:-<none>}, which $url no longer serves; repointing to $sha"

		git update-index --cacheinfo "160000,$sha,$path" || {
			echo "::error::$path: could not rewrite the gitlink"
			failed=$((failed + 1))
			continue
		}
		rm -rf "$path"
		if git submodule update --init --recursive -- "$path" < /dev/null; then
			fixed=$((fixed + 1))
		else
			echo "::error::$path still fails after being repointed to $sha"
			failed=$((failed + 1))
		fi
	done < submodules.txt

	rm -f submodules.txt
	echo "fix-unixnu-submodules: $fixed repointed, $failed failed"

	[ "$failed" -eq 0 ] || exit 1
fi

# ---------------------------------------------------------------------------
# hummer SMMU stream-table patch
#
# The UFS planner refuses to boot on this board.  Measured on the device with a
# UEFI-side MmioRead32 dump (build 700025/700026), not inferred from a photo:
#
#     idr0=4C017E84  idr1=60000053   -> 83 context banks
#     slot=1  smr=80000060  sid=0060  mask=0000  s2cr=00000000   <- UFS stream
#     valid=5  matches=1  masked=0
#
# So there is exactly one SMR match with no mask, and S2CR points at context
# bank 0.  The planner still rejects it:
#
#     p.bank = banks - 1 - bankFromTop      -> 82, 81, ... 75
#     bool ourBank = ((s2cr >> 16) & 3) == 0 && (s2cr & 255) == p.bank;
#     if (matches > 1 || mask || !ourBank)  -> "conflicting UFS stream mapping"
#
# "(0 & 255) == 82" is never true, so every attempt fails.  The entry is firmware
# leftovers pointing at a bank this driver never configures.
#
# Treat "S2CR points at bank 0" as a stale mapping we may take over.  matches > 1
# and mask stay fatal -- those really are conflicts.
#
echo "== applying hummer SMMU patch =="
SMMU_FILE="drivers/NativeUfs/SmmuTakeover.h"

if [ ! -f "$SMMU_FILE" ]; then
	echo "::error::hummer SMMU patch: $SMMU_FILE not found; tree is not as expected"
	ls -la drivers/ 2>/dev/null || true
	exit 1
fi

if grep -q "UNiXNU-HUMMER-SMMU" "$SMMU_FILE"; then
	echo "hummer SMMU patch: already applied"
else
	python3 - "$SMMU_FILE" <<'PY'
import pathlib, sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

anchor = "bool ourBank = ((s2cr >> 16) & 3) == 0 && (s2cr & 255) == p.bank;"
if anchor not in text:
    print("::error::hummer SMMU patch: ourBank anchor not found", file=sys.stderr)
    for line in text.splitlines():
        if "ourBank" in line or "usable" in line:
            print("  saw: " + line.strip(), file=sys.stderr)
    sys.exit(1)

note = anchor + """
    //
    // UNiXNU-HUMMER-SMMU
    //
    // Qualcomm firmware leaves the UFS stream on context bank 0 (S2CR = 0)
    // before an OS claims it.  The walk below only tries banks from the top
    // down, so on this board it never matches that entry and refuses to boot.
    // Bank 0 is never configured by this driver, so taking it over reclaims a
    // stale mapping rather than stealing a live one.
    //
    bool stale = ((s2cr >> 16) & 3) == 0 && (s2cr & 255) == 0;"""

text = text.replace(anchor, note, 1)

applied = False
for old, new in (
    ("if (matches > 1 || mask || !ourBank) {",
     "if (matches > 1 || mask || (!ourBank && !stale)) {"),
    ("if (matches > 1 || mask || !usable) {",
     "if (matches > 1 || mask || (!usable && !stale)) {"),
):
    if old in text:
        text = text.replace(old, new, 1)
        print("hummer SMMU patch: " + old + "  ->  " + new)
        applied = True
        break

if not applied:
    print("::error::hummer SMMU patch: rejection condition not found", file=sys.stderr)
    sys.exit(1)

path.write_text(text, encoding="utf-8", newline="\n")
print("hummer SMMU patch: written to " + str(path))
PY
fi

echo "== hummer SMMU patch: context =="
grep -n -A9 "bool ourBank" "$SMMU_FILE" | head -20
echo "== hummer SMMU patch: condition =="
grep -n "matches > 1 || mask" "$SMMU_FILE"
echo "== hummer SMMU patch: done =="
