"""Loading ``emucraft.toml`` / ``.json`` configuration files.

Example (all sections optional)::

    units = "inch"            # units of the lengths in this file

    [machine]
    rapid_rate = 1000         # units/min, for cycle time estimates
    rapid_mode = "dogleg"     # non-interpolated rapids (each axis at full speed)
    table_z = -0.5            # the tip must stay above this

    [stock]
    min = [-2.8, -0.35, 0.0]
    max = [1.2, 4.65, 1.75]

    [check]
    resolution = 0.002
    link_feed = 700           # feed moves this fast must not touch material

    [tools.20]
    shape = "flat"
    diameter = 0.375
    flute_length = 1.0
    stickout = 1.4
    holder_diameter = 1.3

    [default_tool]            # fills anything the program does not say
    holder_diameter = 1.5

    [[comment_rules]]         # extra CAM comment patterns
    pattern = 'HOLDER DIA\\s*=\\s*([-+]?[\\d.]+)'
    field = "tool.holder_diameter"
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .model import CheckSpec, Config, MachineSpec, StockSpec

try:  # Python 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover - older Pythons
    try:
        import tomli as _toml  # type: ignore
    except ImportError:
        _toml = None


class ConfigError(ValueError):
    pass


def _check_keys(section: str, data: Dict[str, Any], allowed) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    unknown = set(data) - set(allowed)
    if unknown:
        raise ConfigError(f"[{section}] unknown key(s): {', '.join(sorted(unknown))}")


def _dataclass_from(cls, section: str, data: Dict[str, Any]):
    names = {f.name for f in fields(cls)}
    _check_keys(section, data, names)
    return cls(**data)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional(rule):
    what, test = rule
    return what, lambda v: v is None or test(v)


# (what the value must be, test)
_POSITIVE = ("a positive number", lambda v: _number(v) and v > 0)
_NON_NEGATIVE = ("a number >= 0", lambda v: _number(v) and v >= 0)
_POSITIVE_INT = ("a positive integer", lambda v: _integer(v) and v > 0)
_UNITS = ('"inch" or "mm"', lambda v: v in ("inch", "mm"))

_MACHINE_RULES = {
    "rapid_rate": _optional(_POSITIVE),
    "rapid_mode": ('"linear" or "dogleg"', lambda v: v in ("linear", "dogleg")),
    "home_z": _optional(("a number", _number)),
    "table_z": _optional(("a number", _number)),
    "tool_change_time": _NON_NEGATIVE,
}
_CHECK_RULES = {
    "resolution": _optional(_POSITIVE),
    "max_cells": _POSITIVE_INT,
    "tolerance": _optional(_NON_NEGATIVE),
    "chord_tolerance": _optional(_POSITIVE),
    "arc_radius_tolerance": _optional(_POSITIVE),
    "simplify": _optional(_NON_NEGATIVE),
    "link_feed": _optional(_POSITIVE),
    "link_severity": ('"error" or "warning"', lambda v: v in ("error", "warning")),
    "block_delete": ("true or false", lambda v: isinstance(v, bool)),
    "tool_point": _optional(('"tip" or "center"', lambda v: v in ("tip", "center"))),
    "units": _optional(_UNITS),
    "default_units": _UNITS,
    "threads": _optional(_POSITIVE_INT),
}
_TOOL_RULES = {
    "diameter": _POSITIVE,
    "corner_radius": _NON_NEGATIVE,
    "tip_angle": ("an angle between 0 and 180", lambda v: _number(v) and 0 < v < 180),
    "flute_length": _NON_NEGATIVE,
    "shank_diameter": _NON_NEGATIVE,
    "stickout": _NON_NEGATIVE,
    "holder_diameter": _NON_NEGATIVE,
    "holder_length": _NON_NEGATIVE,
    "flutes": _POSITIVE_INT,
    "shape": ('one of "flat", "ball", "bull", "cone"', lambda v: v in ("flat", "ball", "bull", "cone")),
    "kind": ("text", lambda v: isinstance(v, str)),
    "name": ("text", lambda v: isinstance(v, str)),
}
# where comment / macro-variable rules may store what they find
_RULE_TARGETS = (
    {f"tool.{name}" for name in _TOOL_RULES if name != "shape"}
    | {"tool.number"}
    | {f"stock.{axis}{end}" for axis in "xyz" for end in ("min", "max")}
    | {"program.units", "program.tool_point"}
)


def _validate(section: str, obj, rules) -> None:
    for name, (what, test) in rules.items():
        if not test(getattr(obj, name)):
            raise ConfigError(f"[{section}] {name} must be {what}")


def _tool_values(section: str, values) -> Dict[str, Any]:
    _check_keys(section, values, _TOOL_RULES)
    for name, value in values.items():
        what, test = _TOOL_RULES[name]
        if not test(value):
            raise ConfigError(f"[{section}] {name} must be {what}")
    return dict(values)


def _rule_target(section: str, target) -> str:
    if target not in _RULE_TARGETS:
        raise ConfigError(f"[{section}] cannot store into '{target}' "
                          f"(use one of: {', '.join(sorted(_RULE_TARGETS))})")
    return target


def config_from_dict(data: Dict[str, Any]) -> Config:
    top = {"units", "machine", "check", "stock", "tools", "default_tool", "optimize",
           "comment_rules", "variable_rules"}
    _check_keys("top level", data, top)
    cfg = Config()
    units = data.get("units")
    if units is not None and units not in ("inch", "mm"):
        raise ConfigError("units must be 'inch' or 'mm'")
    cfg.units = units
    try:
        if "machine" in data:
            cfg.machine = _dataclass_from(MachineSpec, "machine", data["machine"])
            _validate("machine", cfg.machine, _MACHINE_RULES)
            limits = cfg.machine.limits
            if not isinstance(limits, dict):
                raise ConfigError("[machine] limits must be a table like { x = [min, max] }")
            for axis, rng in limits.items():
                if str(axis).lower() not in ("x", "y", "z"):
                    raise ConfigError(f"[machine] limits: unknown axis '{axis}' (use x, y or z)")
                if not (isinstance(rng, list) and len(rng) == 2 and all(_number(v) for v in rng)
                        and rng[0] < rng[1]):
                    raise ConfigError(f"[machine] limits.{axis} must be [min, max] with min < max")
        if "check" in data:
            cfg.check = _dataclass_from(CheckSpec, "check", data["check"])
            _validate("check", cfg.check, _CHECK_RULES)
        if "stock" in data:
            st = data["stock"]
            _check_keys("stock", st, {"min", "max"})
            lo, hi = st.get("min"), st.get("max")
            if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) == 3 and len(hi) == 3
                    and all(_number(v) for v in lo + hi)):
                raise ConfigError("[stock] needs min = [x, y, z] and max = [x, y, z]")
            cfg.stock = StockSpec(*map(float, lo), *map(float, hi), source="config")
            if not cfg.stock.valid():
                raise ConfigError("[stock] max must be larger than min on every axis")
        tools = data.get("tools") or {}
        if not isinstance(tools, dict):
            raise ConfigError("[tools] must be a table of tool numbers")
        for key, values in tools.items():
            try:
                number = int(str(key).lstrip("Tt"))
            except ValueError:
                raise ConfigError(f"[tools.{key}] is not a tool number (use [tools.12] or [tools.T12])") from None
            cfg.tools[number] = _tool_values(f"tools.{key}", values)
        if "default_tool" in data:
            cfg.default_tool = _tool_values("default_tool", data["default_tool"])
        optimize = data.get("optimize") or {}
        if not isinstance(optimize, dict):
            raise ConfigError("[optimize] must be a table")
        cfg.optimize = dict(optimize)
        for rule in data.get("comment_rules") or []:
            _check_keys("comment_rules", rule, {"pattern", "field"})
            if not isinstance(rule.get("pattern"), str) or "field" not in rule:
                raise ConfigError("[[comment_rules]] needs a pattern and a field")
            try:
                compiled = re.compile(rule["pattern"])
            except re.error as exc:
                raise ConfigError(f"[[comment_rules]] bad pattern {rule['pattern']!r}: {exc}") from None
            if compiled.groups < 1:
                raise ConfigError(f"[[comment_rules]] pattern {rule['pattern']!r} needs a (group) for the value")
            cfg.comment_rules.append((rule["pattern"], _rule_target("comment_rules", rule["field"])))
        variables = data.get("variable_rules") or {}
        if not isinstance(variables, dict):
            raise ConfigError("[variable_rules] must be a table like { 127 = \"tool.holder_length\" }")
        for key, target in variables.items():
            try:
                number = int(str(key).lstrip("#"))
            except ValueError:
                raise ConfigError(f"[variable_rules] '{key}' is not a macro variable number") from None
            cfg.variable_rules[number] = _rule_target("variable_rules", target)
    except TypeError as exc:
        raise ConfigError(str(exc)) from None
    return cfg


def load_config(path: Optional[Union[str, Path]]) -> Config:
    if path is None:
        return Config()
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        if _toml is None:
            raise ConfigError("TOML configuration needs Python 3.11+ (or the 'tomli' package)")
        try:
            data = _toml.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError
            raise ConfigError(f"{path}: {exc}") from None
    return config_from_dict(data)


def find_config(start: Union[str, Path]) -> Optional[Path]:
    """Look for emucraft.toml / emucraft.json next to a program and upwards."""
    here = Path(start).resolve()
    if here.is_file():
        here = here.parent
    for folder in [here, *here.parents]:
        for name in ("emucraft.toml", "emucraft.json"):
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None
