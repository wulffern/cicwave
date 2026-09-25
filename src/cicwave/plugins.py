"""Plugin API: extend cicwave from separately installed packages.

cicwave itself stays generic. Anything specific to a private format,
instrument or protocol lives in its own package, which declares an entry
point in the ``cicwave.plugins`` group::

    # pyproject.toml of the plugin package
    [project.entry-points."cicwave.plugins"]
    myplugin = "myplugin:register"

The entry point names a callable taking this module's :class:`PluginAPI`.
It is called once, the first time cicwave needs its plugins, and uses the
API to register:

* **readers** -- ``register_reader(".ext", fn)``; ``fn(path)`` returns a
  :class:`pandas.DataFrame`. Matched on the longest filename suffix, before
  the built-in readers, so a plugin can also take over a built-in suffix.
* **annotators** -- ``register_annotator(fn)``; ``fn(df, path)`` runs after
  every file is read and may add attributes or annotations to *df* in
  place (see :func:`annotations`). This is how a plugin labels the parts
  of a recording (e.g. the sections of each packet) without editing files.
* **analyses** -- ``register_analysis(label, fn)``; adds *label* to a
  wave's right-click menu, and ``fn(window, wave)`` runs when chosen.
* **constellation presets** -- ``register_constellation_preset(name,
  settings)``; offered in the constellation dialog, filling its fields.

A plugin that fails to load or raises is logged and skipped, never taking
cicwave down with it. ``CICWAVE_PLUGINS=0`` in the environment disables
plugin discovery altogether (cicwave's own test suite runs that way).
"""

import logging
import os

__all__ = [
    "API_VERSION",
    "PluginAPI",
    "load_plugins",
    "annotations",
    "find_reader",
    "run_annotators",
    "analyses",
    "constellation_presets",
    "CONSTELLATION_FIELDS",
]

#: Bumped when the API changes incompatibly; a plugin can compare it.
API_VERSION = 1

ENTRY_POINT_GROUP = "cicwave.plugins"

#: Keys a constellation preset may set, mirroring the dialog's fields.
CONSTELLATION_FIELDS = (
    "symbol_rate", "offset", "freq_offset", "phase", "window_start",
    "window_stop", "conjugate", "normalize", "trajectory", "annotation",
)

_log = logging.getLogger("cicwave.plugins")

_readers = {}
_annotators = []
_analyses = []
_presets = {}
_loaded = False


class PluginAPI:
    """What a plugin's ``register`` callable receives."""

    API_VERSION = API_VERSION

    def __init__(self, name):
        self.name = name

    def register_reader(self, suffix, fn):
        suffix = suffix.lower()
        if not suffix.startswith("."):
            suffix = "." + suffix
        _readers[suffix] = (fn, self.name)

    def register_annotator(self, fn):
        _annotators.append((fn, self.name))

    def register_analysis(self, label, fn):
        _analyses.append((label, fn, self.name))

    def register_constellation_preset(self, name, settings):
        unknown = set(settings) - set(CONSTELLATION_FIELDS)
        if unknown:
            raise ValueError("unknown constellation preset field(s): %s"
                             % ", ".join(sorted(unknown)))
        _presets[name] = dict(settings)


def load_plugins(entry_points=None):
    """Discover and register plugins, once. Returns the names loaded.

    *entry_points* is for tests; by default the installed ``cicwave.plugins``
    entry points are used. Importing :mod:`importlib.metadata` and the
    plugins is deferred to here so a plain launch pays nothing until a file
    is read or a menu is built.
    """
    global _loaded
    if _loaded and entry_points is None:
        return []
    _loaded = True
    if entry_points is None:
        if os.environ.get("CICWAVE_PLUGINS") == "0":
            return []
        try:
            from importlib.metadata import entry_points as _eps
            entry_points = list(_eps(group=ENTRY_POINT_GROUP))
        except Exception as e:  # pragma: no cover - broken environment
            _log.warning("could not list cicwave plugins: %s", e)
            return []
    names = []
    for ep in entry_points:
        try:
            register = ep.load()
            register(PluginAPI(ep.name))
            names.append(ep.name)
        except Exception as e:
            _log.warning("cicwave plugin %r failed to load: %s", ep.name, e)
    return names


def find_reader(path):
    """The plugin reader for *path* (longest matching suffix), or None."""
    load_plugins()
    lower = path.lower()
    best = None
    for suffix, (fn, _name) in _readers.items():
        if lower.endswith(suffix) and (best is None or len(suffix) > len(best[0])):
            best = (suffix, fn)
    return best[1] if best else None


def run_annotators(df, path):
    """Let every registered annotator add to *df* (in place)."""
    load_plugins()
    for fn, name in _annotators:
        try:
            fn(df, path)
        except Exception as e:
            _log.warning("cicwave plugin %r annotator failed on %s: %s",
                         name, path, e)
    return df


def annotations(df):
    """The annotation list of *df*, created empty if missing.

    Annotations follow the SigMF convention -- dicts with
    ``core:sample_start``, ``core:sample_count`` and ``core:label`` -- and
    are what the constellation dialog offers as sample ranges. SigMF
    recordings start with the file's own annotations; an annotator
    appends to the same list.
    """
    return df.attrs.setdefault("cicwave_annotations", [])


def analyses():
    """``[(label, fn)]`` registered for the wave context menu."""
    load_plugins()
    return [(label, fn) for label, fn, _name in _analyses]


def constellation_presets():
    """``{name: settings}`` registered for the constellation dialog."""
    load_plugins()
    return dict(_presets)


def _reset():
    """Forget every registration (tests only)."""
    global _loaded
    _readers.clear()
    _annotators.clear()
    _analyses.clear()
    _presets.clear()
    _loaded = False
