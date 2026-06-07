# Claude Usage

A [StreamController](https://github.com/StreamController/StreamController) plugin that shows your
**Claude Code** usage on a Stream Deck key as three colour-coded traffic lights — **Cost**, **Token**
and **Message** usage — plus a live **countdown to the usage reset**.

It mirrors the three "usage" gauges in
[Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor)
(`claude-monitor`), reusing that tool's own code so the figures and colours match its TUI exactly.

## The key face

```
   C    T    M        <- Cost / Token / Message
  ●    ●    ●         <- traffic-light dots
  6%   6%  17%        <- optional per-metric percentage
     4h44             <- time left in the current 5-hour window
   to reset
```

- **Dots** follow the same thresholds as the monitor: 🟢 green `< 50%`, 🟡 amber `50–90%`, 🔴 red `≥ 90%`.
- **Idle** (no active session) shows grey dots and `idle`; an error reading usage shows `err`.

## How it gets the numbers

Claude Code writes usage to `~/.claude/projects/**/*.jsonl`. Parsing that correctly (pricing, 5-hour
session blocks, P90 custom limits) is exactly what `claude-monitor` already does, so this plugin runs a
tiny snapshot script through **claude-monitor's own venv Python on the host** (via `flatpak-spawn --host`,
which the StreamController flatpak permits) and reads back one line of JSON. The snapshot takes a fraction
of a second.

This means **`claude-monitor` must be installed** (e.g. `uv tool install claude-monitor` or
`pipx install claude-monitor`). The plugin auto-discovers its interpreter from the `claude-monitor`
launcher; if yours lives somewhere unusual, set the path explicitly in the plugin settings.

## Configuration

### Plugin-wide (Settings → Plugins → Claude Usage)

| Setting | Meaning |
|---|---|
| **Plan** | Which limits the dots compare against: **Custom (auto / P90)** — the monitor's default, limits learned from your own history — or **Pro** / **Max 5×** / **Max 20×** fixed limits. |
| **Refresh interval (s)** | How often usage is re-read (default 30). The countdown still ticks live between refreshes. |
| **History window (h)** | How much history to analyse (default 96). The custom-plan P90 limits need a few days of context. |
| **claude-monitor Python** | Optional override for the interpreter path. Blank = auto-discover. |
| **Terminal command** | Command run when a key is pressed; `{cmd}` is replaced with the resolved `claude-monitor` path. Default `gnome-terminal -- {cmd}`. Swap in your own terminal, e.g. `konsole -e {cmd}` or `kitty {cmd}`. |

## Pressing a key

Pressing a Claude Usage key opens the live **`claude-monitor`** TUI in a new terminal (on the host, via
`flatpak-spawn --host`). The full launcher path is resolved up front so it works even when the terminal
doesn't inherit your `uv`/`pipx` PATH.

### Per key (the key's action settings)

| Setting | Meaning |
|---|---|
| **Show percentages** | Print each metric's % under its dot. |
| **Show reset countdown** | Show the time left in the current 5-hour usage window. |

## Install (development)

Symlink the plugin into StreamController's Flatpak plugin directory, then restart the app:

```sh
ln -sfn "$PWD" ~/.var/app/com.core447.StreamController/data/plugins/com_amsterisk_ClaudeUsage
flatpak kill com.core447.StreamController; flatpak run com.core447.StreamController
```

Then add the **Claude Usage** action to a key.

## License

MIT — see [LICENSE](LICENSE).
