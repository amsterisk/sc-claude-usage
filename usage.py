"""Read a live Claude Code usage snapshot.

The usage data lives in ~/.claude/projects/**/*.jsonl, which the StreamController
flatpak sandbox can't see, and parsing it correctly (pricing tables, 5-hour
session blocks, P90 custom limits) is exactly what the `claude-monitor` tool
already does. So rather than re-implement any of it, we run a tiny snapshot
script through claude-monitor's *own* venv Python on the host (via
`flatpak-spawn --host`) and read back one line of JSON. The numbers therefore
match the monitor's TUI exactly.

The snapshot mirrors claude_monitor's display logic: find the active session
block, pick the cost/token/message limits for the chosen plan (P90-derived for
the "custom" plan, like the TUI default), and report used/limit/percent for each
plus the block's reset time.
"""

import json
import os
import shlex
import subprocess
import threading
import time

from loguru import logger as log

# Fallback interpreter path if discovery fails (uv tool install location).
_DEFAULT_PY = os.path.expanduser(
    "~/.local/share/uv/tools/claude-monitor/bin/python"
)

# The snapshot program, fed to `python -c`. argv: <plan> <hours_back>.
# Prints a single JSON line to stdout; never raises out (errors -> JSON "error").
_SNAPSHOT_SRC = r"""
import json, sys
try:
    from datetime import datetime, timezone
    from claude_monitor.data.analysis import analyze_usage
    from claude_monitor.core.plans import Plans, get_cost_limit, get_token_limit
    from claude_monitor.utils.time_utils import percentage

    plan = sys.argv[1] if len(sys.argv) > 1 else "custom"
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 96

    data = analyze_usage(hours_back=hours, quick_start=False, use_cache=False)
    blocks = data["blocks"]
    active = next((b for b in blocks if isinstance(b, dict) and b.get("isActive")), None)

    if not active:
        print(json.dumps({"active": False, "plan": plan}))
        sys.exit(0)

    if plan == "custom":
        from claude_monitor.ui.components import AdvancedCustomLimitDisplay
        tmp = AdvancedCustomLimitDisplay(None)
        sd = tmp._collect_session_data(blocks)
        pct = tmp._calculate_session_percentiles(sd["limit_sessions"])
        cost_limit = pct["costs"]["p90"]
        msg_limit = pct["messages"]["p90"]
        token_limit = get_token_limit("custom", blocks)
    else:
        cost_limit = get_cost_limit(plan)
        msg_limit = Plans.get_message_limit(plan)
        token_limit = get_token_limit(plan)

    tokens = active.get("totalTokens", 0)
    cost = active.get("costUSD", 0.0)
    msgs = active.get("sentMessagesCount", 0)

    # "Will run out before reset": project tokens at the current burn rate to the
    # end of the 5-hour window (claude-monitor's own projection) and see if that
    # crosses the token limit -- i.e. predicted exhaustion before the reset time.
    proj = active.get("projection") or {}
    proj_tokens = proj.get("totalTokens")
    exhaust = bool(proj_tokens is not None and token_limit and proj_tokens > token_limit)

    print(json.dumps({
        "active": True,
        "plan": plan,
        "exhaust": exhaust,
        "tokens": {"used": tokens, "limit": token_limit,
                   "pct": percentage(tokens, token_limit) if token_limit else 0},
        "cost":   {"used": cost, "limit": cost_limit,
                   "pct": percentage(cost, cost_limit) if cost_limit else 0},
        "msgs":   {"used": msgs, "limit": msg_limit,
                   "pct": percentage(msgs, msg_limit) if msg_limit else 0},
        "reset_iso": active.get("endTime"),
        "now_iso": datetime.now(timezone.utc).isoformat(),
    }))
except Exception as e:
    print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
"""


def _in_flatpak() -> bool:
    return os.path.isfile("/.flatpak-info")


def _host_cwd() -> str:
    return os.path.expanduser("~")


_PY = None
_PY_LOCK = threading.Lock()


def _interpreter(override: str = "") -> str | None:
    """Path to the claude-monitor venv Python on the host.

    An explicit override wins. Otherwise discover it from the launcher's shebang
    (`head -1 $(command -v claude-monitor)`), cached for the process. Falls back
    to the conventional uv install path.
    """
    if override:
        return override
    global _PY
    with _PY_LOCK:
        if _PY:
            return _PY
    shell = ('p=$(command -v claude-monitor 2>/dev/null); '
             '[ -n "$p" ] && head -1 "$p" | sed "s/^#!//"')
    cmd = ["sh", "-lc", shell]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    found = None
    try:
        r = subprocess.run(cmd, cwd=_host_cwd(), capture_output=True, text=True,
                           timeout=8)
        cand = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
        if cand:
            found = cand
    except Exception as e:
        log.debug(f"ClaudeUsage: interpreter discovery failed: {e}")
    if not found:
        found = _DEFAULT_PY
    with _PY_LOCK:
        _PY = found
    return _PY


def _run_snapshot(plan: str, hours_back: int, override: str) -> dict:
    py = _interpreter(override)
    if not py:
        return {"error": "claude-monitor Python not found"}
    argv = [py, "-c", _SNAPSHOT_SRC, plan, str(hours_back)]
    cmd = ["flatpak-spawn", "--host", *argv] if _in_flatpak() else argv
    try:
        r = subprocess.run(cmd, cwd=_host_cwd(), capture_output=True, text=True,
                           timeout=45)
    except subprocess.TimeoutExpired:
        return {"error": "snapshot timed out"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out = (r.stdout or "").strip()
    if not out:
        return {"error": (r.stderr or "no output").strip()[:200]}
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"error": f"unparseable: {out[:200]}"}


_LAUNCHER = None
_LAUNCHER_LOCK = threading.Lock()


def _launcher() -> str:
    """Absolute path to the `claude-monitor` launcher on the host.

    Found via `command -v claude-monitor` and cached. A new terminal won't
    necessarily inherit the user's PATH (uv/pipx shims often aren't on the
    default login PATH a terminal app spawns with), so we resolve the full path
    here and hand the terminal an absolute command. Falls back to the bare name.
    """
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER:
            return _LAUNCHER
    cmd = ["sh", "-lc", "command -v claude-monitor 2>/dev/null"]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host", *cmd]
    found = ""
    try:
        r = subprocess.run(cmd, cwd=_host_cwd(), capture_output=True, text=True,
                           timeout=8)
        found = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
    except Exception as e:
        log.debug(f"ClaudeUsage: claude-monitor discovery failed: {e}")
    if not found:
        found = "claude-monitor"  # let the terminal's own PATH have a go
    with _LAUNCHER_LOCK:
        _LAUNCHER = found
    return _LAUNCHER


def open_monitor(term_cmd: str) -> bool:
    """Open the claude-monitor TUI in a new terminal on the host, detached.

    `term_cmd` is a template whose {cmd} placeholder is replaced with the resolved
    claude-monitor launcher path, e.g. "gnome-terminal -- {cmd}". Returns False if
    the command can't start.
    """
    monitor = _launcher()
    argv = []
    for tok in shlex.split(term_cmd or ""):
        argv.append(monitor if tok == "{cmd}" else tok.replace("{cmd}", monitor))
    if not argv:
        log.error("ClaudeUsage: empty terminal command template")
        return False
    cmd = ["flatpak-spawn", "--host", *argv] if _in_flatpak() else argv
    try:
        # start_new_session so the terminal outlives StreamController.
        subprocess.Popen(cmd, cwd=_host_cwd(), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info(f"ClaudeUsage: opened monitor via {argv!r}")
        return True
    except Exception as e:
        log.error(f"ClaudeUsage: failed to open terminal {cmd}: "
                  f"{type(e).__name__}: {e}")
        return False


# Short-lived cache so several keys (or back-to-back ticks) coalesce onto one run
# instead of each launching the analyser. Callers pass their poll interval as the
# TTL: a snapshot newer than that is reused.
_CACHE = {"key": None, "ts": 0.0, "data": None}
_CACHE_LOCK = threading.Lock()


def get_snapshot(plan: str, hours_back: int, override: str = "",
                 ttl: float = 10.0) -> dict:
    """Return a usage snapshot dict, reusing a recent one within `ttl` seconds."""
    key = (plan, hours_back, override)
    now = time.time()
    with _CACHE_LOCK:
        if (_CACHE["key"] == key and _CACHE["data"] is not None
                and now - _CACHE["ts"] < ttl):
            return _CACHE["data"]
    data = _run_snapshot(plan, hours_back, override)
    with _CACHE_LOCK:
        _CACHE.update(key=key, ts=time.time(), data=data)
    return data
