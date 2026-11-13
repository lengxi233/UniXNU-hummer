#!/usr/bin/env python3
"""Compile Stage 7 components with the kernel's own ABI and compiler flags."""
import argparse
import json
import os
import runpy
from pathlib import Path
import struct
import subprocess

root = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(root / 'scripts'))
import board_sources


def hid_input_source(tree):
    """NativeHidInput.cpp, from the assembled tree or from the fetched feed.

    The source is the catalog driver ShengHidInput. Selecting that component would also
    run its own recipe (build-sheng-hid-input.py), which qualifies the object against a
    prelink candidate under build/xnu-prelink -- but the archive stage runs before that
    prelink exists, so selecting it only fails. This board compiles the same source its
    own way (frozen 207-slot vtable, staged for late registration), so the source is
    taken from wherever prepare() put it: the tree when the component is linked, and the
    feed checkout otherwise.
    """
    for candidate in (tree / 'drivers/ShengHidInput', tree / 'drivers/NativeHidInput'):
        if (candidate / 'NativeHidInput.cpp').is_file():
            return candidate / 'NativeHidInput.cpp'
    out = os.environ.get('UNIXNU_OUT')
    candidates = [Path(out) / 'feeds'] if out else []
    # <work>/tree is <out>/<board>/<profile>/<config>/tree.
    candidates.append(tree.parents[3] / 'feeds')
    device = os.environ.get('UNIXNU_DEVICE')
    if device:
        candidates.append(Path(device) / 'out' / 'feeds')
    for feeds in candidates:
        if not feeds.is_dir():
            continue
        for entry in sorted(feeds.iterdir()):
            source = entry / 'drivers/ShengHidInput/NativeHidInput.cpp'
            if source.is_file():
                return source
    raise SystemExit(
        'NativeHidInput.cpp is missing from the tree and from every feed under %s. '
        'The ShengHidInput source was not fetched; run "unixnu fetch -D <device>" first.'
        % ', '.join(str(path) for path in candidates))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('component', choices=['NativeLegacyDisplay', 'NativeKeyStoreEvents', 'Stage7Probe', 'HummerHidInput', 'NativeHidInput'])
component = parser.parse_args().component
directory = root / 'build' / ('native-keystore-events' if component == 'NativeKeyStoreEvents' else
                             'native-hid-input' if component == 'NativeHidInput' else 'native-legacy-display')
directory.mkdir(parents=True, exist_ok=True)
source = (root / 'scripts/stage7/Stage7Probe.cpp' if component == 'Stage7Probe' else
          # package/ maps to kits/hummer in the assembled tree (board.toml kit = "hummer").
          root / 'kits/hummer/HummerHidInput.cpp' if component == 'HummerHidInput' else
          # The display and key-store drivers belong to this board's package; the HID
          # input driver is a catalog component (ShengHidInput, formerly NativeHidInput).
          board_sources.driver('NativeMobileDisplay') / 'NativeLegacyDisplay.cpp' if component == 'NativeLegacyDisplay' else
          board_sources.driver('NativeKeyStoreEvents') / 'NativeKeyStoreEvents.cpp' if component == 'NativeKeyStoreEvents' else
          hid_input_source(root))
record = json.loads((root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
obj = directory / (component + '.o')
compile_command = runpy.run_path(str(root / 'scripts/build-arm-platform.py'))['compile_command']
arguments = iter(compile_command(record, source, obj))
command = []
# Keep XNU's ABI; use the reference driver's warning policy instead of -Weverything.
for value in arguments:
    if value == '-Xclang':
        option = next(arguments)
        if not option.startswith('-W'):
            command += [value, option]
    elif not value.startswith('-W'):
        command.append(value)
command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
            '-Wno-c99-designator', '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
            '-I', str(directory), '-I', str(root / '.cache/xnu-work/osfmk')]
if component == 'NativeHidInput':
    # OriginalHidDeviceABI.h (the frozen prelink ABI of the original IOHIDDevice)
    # lives in the device package.
    command += ['-I', str(root / 'kits/hummer')]
subprocess.run(command, cwd=record['directory'], check=True)
data = obj.read_bytes()
assert struct.unpack_from('<4I', data) == (0xfeedfacf, 0x100000c, 0, 1), 'Require ARM64 Mach-O object'
offset, deployment = 32, None
for _ in range(struct.unpack_from('<I', data, 16)[0]):
    cmd, size = struct.unpack_from('<II', data, offset)
    if cmd == 0x32:
        deployment = struct.unpack_from('<II', data, offset + 8)
    offset += size
assert deployment == (2, 0x110000), 'Require iOS 17 target'
print(component + ': compile and object ABI checks passed')
