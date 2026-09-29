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

``set_description(text)`` gives the plugin a one-line description for
Help > Plugins; without it the package's own summary is shown.

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
    "plugin_info",
    "describe_plugins",
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
_info = {}
_loaded = False


class PluginAPI:
    """What a plugin's ``register`` callable receives."""

    API_VERSION = API_VERSION

    def __init__(self, name):
        self.name = name
        self._info = _info.setdefault(name, _new_info(name))

    def set_description(self, text):
        """One line saying what the plugin is for, shown in Help > Plugins."""
        self._info["description"] = str(text).strip()

    def register_reader(self, suffix, fn):
        suffix = suffix.lower()
        if not suffix.startswith("."):
            suffix = "." + suffix
        _readers[suffix] = (fn, self.name)
        self._info["readers"].append(suffix)

    def register_annotator(self, fn):
        _annotators.append((fn, self.name))
        self._info["annotators"].append(getattr(fn, "__name__", repr(fn)))

    def register_analysis(self, label, fn):
        _analyses.append((label, fn, self.name))
        self._info["analyses"].append(label)

    def register_constellation_preset(self, name, settings):
        unknown = set(settings) - set(CONSTELLATION_FIELDS)
        if unknown:
            raise ValueError("unknown constellation preset field(s): %s"
                             % ", ".join(sorted(unknown)))
        _presets[name] = dict(settings)
        self._info["presets"].append(name)


def _new_info(name):
    return {"name": name, "description": "", "version": "", "error": None,
            "readers": [], "annotators": [], "analyses": [], "presets": []}


def _dist_metadata(ep):
    """``(summary, version)`` of the package providing *ep*, if known."""
    dist = getattr(ep, "dist", None)
    if dist is None:
        return "", ""
    try:
        return (dist.metadata.get("Summary") or "", dist.version or "")
    except Exception:  # pragma: no cover - odd metadata
        return "", ""


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
        info = _info.setdefault(ep.name, _new_info(ep.name))
        info["description"], info["version"] = _dist_metadata(ep)
        try:
            register = ep.load()
            register(PluginAPI(ep.name))
            names.append(ep.name)
        except Exception as e:
            info["error"] = str(e) or type(e).__name__
            _log.warning("cicwave plugin %r failed to load: %s", ep.name, e)
    return names


def plugin_info():
    """What each discovered plugin is and registered, for display.

    A list of dicts with ``name``, ``description``, ``version``, ``error``
    (the load failure, or None) and the lists ``readers``, ``annotators``,
    ``analyses`` and ``presets``.
    """
    load_plugins()
    return [dict(i) for i in _info.values()]


def describe_plugins():
    """Plain-text summary of :func:`plugin_info`, as Help > Plugins shows."""
    infos = plugin_info()
    if not infos and os.environ.get("CICWAVE_PLUGINS") == "0":
        return "Plugin discovery is off (CICWAVE_PLUGINS=0)."
    if not infos:
        return ("No plugins installed.\n\nA plugin is a package declaring a "
                "'%s' entry point." % ENTRY_POINT_GROUP)
    out = []
    for i in infos:
        head = i["name"] + (" " + i["version"] if i["version"] else "")
        out.append(head)
        if i["description"]:
            out.append("  " + i["description"])
        if i["error"]:
            out.append("  FAILED TO LOAD: " + i["error"])
        for key, title in (("readers", "Readers"),
                           ("annotators", "Annotators"),
                           ("analyses", "Analyses"),
                           ("presets", "Constellation presets")):
            if i[key]:
                out.append("  %-22s %s" % (title + ":", ", ".join(i[key])))
        out.append("")
    return "\n".join(out).rstrip()


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
    _info.clear()
    _loaded = False
