import glob
import os
import threading
from datetime import datetime, timezone

from loguru import logger as log

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from PIL import Image, ImageDraw, ImageFont

from src.backend.PluginManager.ActionBase import ActionBase

from . import config, usage

# The three metrics, in display order. Each draws an icon *inside* its dot:
# $ = cost, coin = tokens, envelope = messages (claude-monitor's Cost/Token/Message).
# (Glyph functions are defined below and bound into METRICS after them.)

# Traffic-light colours and thresholds, matching claude-monitor's progress bars:
# green < 50%, amber 50–90%, red >= 90%.
_GREEN = (46, 204, 113, 255)
_AMBER = (241, 196, 15, 255)
_RED = (231, 76, 60, 255)
_GREY = (120, 120, 120, 255)
_FG = (235, 235, 235, 255)
_CAPTION = (160, 160, 160, 255)
_SHADOW = (0, 0, 0, 255)
_INK = (26, 26, 30, 255)        # dark glyph drawn inside the coloured dots

_WARN_PCT = 50.0
_CRIT_PCT = 90.0


def _dot_color(pct):
    if pct is None:
        return _GREY
    if pct >= _CRIT_PCT:
        return _RED
    if pct >= _WARN_PCT:
        return _AMBER
    return _GREEN


_FONT_PATH = None


def _font_path() -> str | None:
    global _FONT_PATH
    if _FONT_PATH is None:
        prefs = ["/usr/share/fonts/google-crosextra-carlito/Carlito-Bold.ttf",
                 "/usr/share/fonts/cantarell/Cantarell-Bold.ttf"]
        for p in prefs:
            if os.path.exists(p):
                _FONT_PATH = p
                break
        else:
            for pat in ("/usr/share/fonts/**/*Bold.ttf", "/usr/share/fonts/**/*.ttf"):
                found = sorted(glob.glob(pat, recursive=True))
                if found:
                    _FONT_PATH = found[0]
                    break
    return _FONT_PATH


def _font(px: int):
    try:
        p = _font_path()
        return ImageFont.truetype(p, px) if p else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _draw_clock(d, cx, cy, r, col, w):
    """A clock face (circle + two hands) centred at (cx, cy)."""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=w)
    # minute hand straight up, hour hand to ~4 o'clock
    d.line([(cx, cy), (cx, cy - r * 0.72)], fill=col, width=w)
    d.line([(cx, cy), (cx + r * 0.50, cy + r * 0.42)], fill=col, width=w)


# ---- metric glyphs, drawn inside a dot of radius r centred at (cx, cy) ----

def _glyph_dollar(d, cx, cy, r, col):
    """Cost: a dollar sign."""
    _text(d, (cx, cy), "$", int(r * 1.75), col, anchor="mm", shadow=False)


def _glyph_coin(d, cx, cy, r, col):
    """Tokens: a coin (a disc seen slightly side-on, like a thin cylinder)."""
    w = max(2, int(r * 0.15))
    rx, ry = r * 0.66, r * 0.30
    top, bot = cy - r * 0.26, cy + r * 0.26
    d.ellipse([cx - rx, top - ry, cx + rx, top + ry], outline=col, width=w)  # top face
    d.line([(cx - rx, top), (cx - rx, bot)], fill=col, width=w)              # left edge
    d.line([(cx + rx, top), (cx + rx, bot)], fill=col, width=w)             # right edge
    d.arc([cx - rx, bot - ry, cx + rx, bot + ry], 0, 180, fill=col, width=w)  # front edge


def _glyph_envelope(d, cx, cy, r, col):
    """Messages: an envelope (rectangle + flap)."""
    w = max(2, int(r * 0.15))
    ew, eh = r * 1.5, r * 1.02
    x0, y0, x1, y1 = cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2
    try:
        d.rounded_rectangle([x0, y0, x1, y1], radius=r * 0.12, outline=col, width=w)
    except Exception:
        d.rectangle([x0, y0, x1, y1], outline=col, width=w)
    d.line([(x0, y0), (cx, y0 + eh * 0.55), (x1, y0)], fill=col, width=w, joint="curve")


METRICS = [("cost", _glyph_dollar), ("tokens", _glyph_coin), ("msgs", _glyph_envelope)]


def _text(d, xy, s, px, fill, anchor="mm", shadow=True):
    f = _font(px)
    if shadow:
        d.text(xy, s, font=f, fill=fill, anchor=anchor,
               stroke_width=max(1, int(px * 0.10)), stroke_fill=_SHADOW)
    else:
        d.text(xy, s, font=f, fill=fill, anchor=anchor)


def _fmt_eta(secs) -> str:
    """Reset countdown: '4h09', '47m', '<1m', or '—' when unknown."""
    if secs is None:
        return "—"
    if secs <= 0:
        return "0m"
    if secs < 60:
        return "<1m"
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{h}h{m:02d}" if h > 0 else f"{m}m"


def _eta_seconds(reset_iso) -> float | None:
    if not reset_iso:
        return None
    try:
        reset = datetime.fromisoformat(reset_iso)
        if reset.tzinfo is None:
            reset = reset.replace(tzinfo=timezone.utc)
        return (reset - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None


def _key_image(snap: dict, show_pct: bool, show_countdown: bool,
               size: int = 144) -> Image.Image:
    """Render the key face: three traffic-light dots (C/T/M) and a reset countdown."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    active = bool(snap.get("active"))
    err = snap.get("error")

    centers = [size * 0.20, size * 0.50, size * 0.80]
    dot_cy = size * 0.30 if (show_pct or show_countdown) else size * 0.45
    dot_r = size * 0.125
    pct_y = size * 0.55

    for (key, glyph), cx in zip(METRICS, centers):
        m = snap.get(key) if active else None
        pct = m.get("pct") if isinstance(m, dict) else None
        col = _dot_color(pct)
        d.ellipse([cx - dot_r, dot_cy - dot_r, cx + dot_r, dot_cy + dot_r], fill=col)
        glyph(d, cx, dot_cy, dot_r, _INK)
        if show_pct:
            txt = f"{pct:.0f}%" if pct is not None else "—"
            _text(d, (cx, pct_y), txt, int(size * 0.11), _FG)

    if show_countdown:
        cy = size * 0.82
        if err:
            _text(d, (size / 2, cy), "err", int(size * 0.16), _RED)
        elif not active:
            _text(d, (size / 2, cy), "idle", int(size * 0.15), _CAPTION)
        else:
            # Clock icon + countdown, centred as a group. The clock is green
            # normally and red when usage is projected to run out before reset.
            eta = _fmt_eta(_eta_seconds(snap.get("reset_iso")))
            fpx = int(size * 0.22)
            f = _font(fpx)
            try:
                tb = d.textbbox((0, 0), eta, font=f)
                tw = tb[2] - tb[0]
            except Exception:
                tw = fpx * len(eta) * 0.6
            r = size * 0.082
            gap = size * 0.05
            total = 2 * r + gap + tw
            x0 = (size - total) / 2.0
            clock_col = _RED if snap.get("exhaust") else _GREEN
            _draw_clock(d, x0 + r, cy, r, clock_col, max(2, int(size * 0.022)))
            _text(d, (x0 + 2 * r + gap, cy), eta, fpx, _FG, anchor="lm")
    return img


class Usage(ActionBase):
    """Show Claude Code usage as three traffic lights plus a reset countdown."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_configuration = True
        self._snap = {}            # last snapshot dict
        self._refreshing = False
        self._poll_ticks = 0
        self._shown_eta = None     # last countdown string drawn (for cheap re-render)

    # ---------- per-key display settings ----------
    def _show_pct(self) -> bool:
        return bool(self.get_settings().get("show_pct", True))

    def _show_countdown(self) -> bool:
        return bool(self.get_settings().get("show_countdown", True))

    # ---------- lifecycle ----------
    def on_ready(self):
        self._render()
        threading.Thread(target=self._refresh, name="CURefresh", daemon=True).start()

    def on_tick(self):
        if not self.get_is_present():
            return
        cfg = config.load(self.plugin_base)
        poll = int(cfg.get("poll", 30) or 30)
        # Refresh the data on the poll cadence.
        if not self._refreshing:
            self._poll_ticks += 1
            if self._poll_ticks >= max(1, poll):
                self._poll_ticks = 0
                threading.Thread(target=self._refresh, name="CURefresh",
                                 daemon=True).start()
        # Re-render between refreshes only when the live countdown minute changes.
        if self._show_countdown() and self._snap.get("active"):
            eta = _fmt_eta(_eta_seconds(self._snap.get("reset_iso")))
            if eta != self._shown_eta:
                self._render()

    def _refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            cfg = config.load(self.plugin_base)
            poll = int(cfg.get("poll", 30) or 30)
            snap = usage.get_snapshot(
                plan=cfg.get("plan", "custom"),
                hours_back=int(cfg.get("hours_back", 96) or 96),
                override=cfg.get("python", "") or "",
                ttl=max(2, poll - 1),
            )
            self._snap = snap or {}
            if self._snap.get("error"):
                log.warning(f"ClaudeUsage: snapshot error: {self._snap['error']}")
            self._render()
        finally:
            self._refreshing = False

    # ---------- rendering ----------
    def _render(self):
        GLib.idle_add(self._render_now)

    def _render_now(self):
        try:
            self.set_center_label("", update=False)
        except Exception:
            pass
        try:
            self._shown_eta = _fmt_eta(_eta_seconds(self._snap.get("reset_iso"))) \
                if self._snap.get("active") else None
            self.set_media(image=_key_image(self._snap, self._show_pct(),
                                            self._show_countdown()),
                           update=True)
        except Exception as e:
            log.error(f"ClaudeUsage: render failed: {e}")
        return False

    # ---------- per-key config UI ----------
    def get_config_rows(self):
        s = self.get_settings()

        self.pct_row = Adw.SwitchRow(title="Show percentages")
        self.pct_row.set_subtitle("Print each metric's % under its dot")
        self.pct_row.set_active(bool(s.get("show_pct", True)))
        self.pct_row.connect("notify::active", self._on_switch, "show_pct")

        self.eta_row = Adw.SwitchRow(title="Show reset countdown")
        self.eta_row.set_subtitle("Time left in the current 5-hour usage window")
        self.eta_row.set_active(bool(s.get("show_countdown", True)))
        self.eta_row.connect("notify::active", self._on_switch, "show_countdown")

        return [self.pct_row, self.eta_row]

    def _on_switch(self, row, _pspec, key):
        s = self.get_settings()
        s[key] = bool(row.get_active())
        self.set_settings(s)
        self._render()
