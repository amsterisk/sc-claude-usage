"""Plugin-global settings UI: plan, poll cadence, history window, interpreter.

Returned from PluginBase.get_settings_area() as a single Adw.PreferencesGroup.
These apply to every Usage key (the figures are account-wide).
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from . import config


class PluginSettings:
    def __init__(self, plugin_base):
        self.plugin_base = plugin_base

    def get_settings_area(self) -> Adw.PreferencesGroup:
        cfg = config.load(self.plugin_base)
        group = Adw.PreferencesGroup(title="Claude Usage")

        plan_model = Gtk.StringList()
        for lbl in config.PLAN_LABELS:
            plan_model.append(lbl)
        self.plan_row = Adw.ComboRow(title="Plan", model=plan_model)
        self.plan_row.set_subtitle("Sets the cost/token/message limits the dots compare against")
        cur = cfg.get("plan", "custom")
        self.plan_row.set_selected(
            config.PLAN_IDS.index(cur) if cur in config.PLAN_IDS else 0)
        self.plan_row.connect("notify::selected", self._on_plan)
        group.add(self.plan_row)

        poll = Adw.SpinRow.new_with_range(5, 600, 5)
        poll.set_title("Refresh interval (s)")
        poll.set_subtitle("How often usage is re-read")
        poll.set_value(int(cfg.get("poll", 30) or 30))
        poll.connect("notify::value", self._on_int, "poll")
        group.add(poll)

        hours = Adw.SpinRow.new_with_range(24, 336, 24)
        hours.set_title("History window (h)")
        hours.set_subtitle("How much history to analyse (custom-plan P90 limits need context)")
        hours.set_value(int(cfg.get("hours_back", 96) or 96))
        hours.connect("notify::value", self._on_int, "hours_back")
        group.add(hours)

        self.py_row = Adw.EntryRow(title="claude-monitor Python (optional)")
        self.py_row.set_text(cfg.get("python", ""))
        self.py_row.connect("notify::text", self._on_python)
        group.add(self.py_row)

        return group

    def _on_plan(self, combo, _pspec):
        cfg = config.load(self.plugin_base)
        cfg["plan"] = config.PLAN_IDS[combo.get_selected()]
        config.save(self.plugin_base, cfg)

    def _on_int(self, row, _pspec, key):
        cfg = config.load(self.plugin_base)
        cfg[key] = int(row.get_value())
        config.save(self.plugin_base, cfg)

    def _on_python(self, entry, _pspec):
        cfg = config.load(self.plugin_base)
        cfg["python"] = entry.get_text()
        config.save(self.plugin_base, cfg)
