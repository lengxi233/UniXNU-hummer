"""Where this board's own sources live while a recipe runs.

Until v186 the board drivers were checked out at ``drivers/<name>`` in the build
tree, because the device repository had them at that path and the resolver
linked every device module in.  Format 2 keeps only the components named in
``[components] include``, and the hummer board drivers are not in the component
catalog, so they moved into the device ``package/``.  A package appears inside a
build tree as ``kits/<board.kit>``, which is the path every recipe sees while it
runs; a standalone run (the repository itself) sees ``package/drivers/<name>``.

``driver()`` answers with whichever of the two exists, so the same recipe works
in both places and the tree stays the only explanation of the build.
"""
import os
from pathlib import Path

# The kit name of this board ([board] kit in board.toml); package/ becomes kits/<kit>.
KIT = 'hummer'


def _roots():
    """Candidate base directories, nearest first: the tree, then the device repository."""
    roots = [Path(__file__).resolve().parents[1]]
    device = os.environ.get('UNIXNU_DEVICE')
    if device:
        roots.append(Path(device).resolve())
    return roots


def driver(name):
    """Directory of a board driver, from the running tree or the device package."""
    for base in _roots():
        for candidate in (base / 'kits' / KIT / 'drivers' / name, base / 'drivers' / name, base / 'package/drivers' / name):
            if candidate.is_dir():
                return candidate
    raise SystemExit('%s: board driver is missing; it belongs in %s/package/drivers/' % (name, _roots()[0]))


def kit_path(*parts):
    """A file of the board package, such as the GPU bring-up sources."""
    for base in _roots():
        for root in (base / 'kits' / KIT, base / 'package'):
            candidate = root.joinpath(*parts)
            if candidate.exists():
                return candidate
    return _roots()[0] / 'kits' / KIT / Path(*parts)


def tree():
    """The build tree, which is where a recipe runs from."""
    return _roots()[0]


def hid_input_source():
    """NativeHidInput.cpp, from the assembled tree or from the fetched feed.

    The source is the catalog driver ShengHidInput. Selecting that component would also
    run its own recipe (build-sheng-hid-input.py), which qualifies the object against a
    prelink candidate under build/xnu-prelink -- but the archive stage runs before that
    prelink exists, so selecting it only fails. This board compiles the same source its
    own way (frozen 207-slot vtable, staged for late registration), so the file is taken
    from wherever prepare() put it: the tree when something linked it, and the feed
    checkout otherwise. The checkout is the repository root, so the file is at its top
    level; a submodule-style layout would nest it under drivers/ShengHidInput.
    """
    import os
    root = _roots()[0]
    for candidate in (root / 'drivers/ShengHidInput', root / 'drivers/NativeHidInput'):
        if (candidate / 'NativeHidInput.cpp').is_file():
            return candidate / 'NativeHidInput.cpp'
    out = os.environ.get('UNIXNU_OUT')
    candidates = [Path(out) / 'feeds'] if out else []
    # <work>/tree is <out>/<board>/<profile>/<config>/tree, so four levels up is <out>.
    # A shallower tree (a standalone run from the repository) has no such parent; skip it
    # rather than raising IndexError from the middle of a lookup.
    if len(root.parents) > 3:
        candidates.append(root.parents[3] / 'feeds')
    device = os.environ.get('UNIXNU_DEVICE')
    if device:
        candidates.append(Path(device) / 'out' / 'feeds')
    for feeds in candidates:
        if not feeds.is_dir():
            continue
        for entry in sorted(feeds.iterdir()):
            for relative in ('drivers/ShengHidInput/NativeHidInput.cpp', 'NativeHidInput.cpp'):
                source = entry / relative
                if source.is_file():
                    return source
    raise SystemExit(
        'NativeHidInput.cpp is missing from the tree and from every feed under %s. '
        'The ShengHidInput source was not fetched; run "unixnu fetch -D <device>" first.'
        % ', '.join(str(path) for path in candidates))
