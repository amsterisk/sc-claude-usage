"""Plugin-global configuration for Claude Usage.

These settings are account-wide (the usage figures don't vary per key), so they
live on the plugin via PluginBase.get_settings()/set_settings() and are shared by
every Usage key. Per-key settings (which dots/countdown to show) live on each
action instead. Shape:

    {
      "plan":       "custom",   # custom | pro | max5 | max20 — picks the limits
      "poll":       30,         # seconds between usage refreshes
      "hours_back": 96,         # how much history to analyse (P90 needs context)
      "python":     "",         # override path to the claude-monitor venv python
      "term_cmd":   "gnome-terminal -- {cmd}"  # pressing a key opens this; {cmd} = claude-monitor
    }

Pressing a Usage key runs `term_cmd` on the host (via flatpak-spawn --host), with
{cmd} filled in with the resolved `claude-monitor` launcher path — i.e. it opens
the live monitor TUI in a new terminal. The template is split into argv with
shlex, so values needn't be quoted.

"custom" mirrors claude-monitor's own default: limits are auto-detected from your
own history (P90), so the traffic lights match what the monitor TUI shows.
"""

PLANS = [
    ("custom", "Custom (auto / P90)"),
    ("pro", "Pro"),
    ("max5", "Max 5×"),
    ("max20", "Max 20×"),
]
PLAN_IDS = [p[0] for p in PLANS]
PLAN_LABELS = [p[1] for p in PLANS]

DEFAULTS = {
    "plan": "custom",
    "poll": 30,
    "hours_back": 96,
    "python": "",
    "term_cmd": "gnome-terminal -- {cmd}",
}


def load(plugin_base) -> dict:
    s = plugin_base.get_settings() or {}
    cfg = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in s and s[k] not in (None, ""):
            cfg[k] = s[k]
    return cfg


def save(plugin_base, cfg: dict) -> None:
    s = plugin_base.get_settings() or {}
    for k in DEFAULTS:
        if k in cfg:
            s[k] = cfg[k]
    plugin_base.set_settings(s)
