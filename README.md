# lumen-sync

Sync the [opencode](https://opencode.ai) `lumen` provider with the live
[NCSA Lumen](https://lumen.ncsa.illinois.edu) model catalog.

Queries the Lumen API (`GET /v1/models`) with your `LUMEN_API_KEY` and
reconciles the `provider.lumen.models` block in `opencode.json` so it always
reflects what Lumen actually hosts. It adds real models (with context and
cost metadata), removes phantom entries that no longer exist, and leaves
your other providers untouched.

No more hand-editing model IDs. No more stale entries after Lumen rotates
its catalog.

## Requirements

- Python 3 (standard library only, no pip installs)
- A Lumen API key exported as `LUMEN_API_KEY`
- opencode with a config at `~/.config/opencode/opencode.json`
  (or pass a different path with `--config`)

## Install

### Option A: installer script (Linux/macOS)

```bash
git clone https://github.com/jkhenry-illinois/lumen-sync.git
cd lumen-sync
./install.sh            # user-local install to ~/.local (no sudo)
sudo ./install.sh --system   # or system-wide to /usr/local
```

On Ubuntu you can also double-click `install.desktop` in a file manager to
run the same installer.

### Option B: run it straight from the clone

```bash
python3 lumen-sync.py --list
```

## Usage

```bash
lumen-sync                  # dry-run: show the plan, write nothing
lumen-sync --apply          # write the changes (timestamped backup first)
lumen-sync --list           # print the live Lumen catalog and exit
lumen-sync --apply --no-prune   # add/update but keep entries Lumen no longer lists
```

Useful options:

| Option | What it does |
|--------|--------------|
| `--config PATH` | Target a different `opencode.json` |
| `--provider-key KEY` | Sync a different provider block (default: `lumen`) |
| `--base-url URL` | Different Lumen endpoint (default: `https://lumen.ncsa.illinois.edu/v1`) |
| `-q, --quiet` | Suppress non-error output (good for cron) |

Full documentation: `man lumen-sync` (installed by `install.sh`).

## What gets written

The tool only touches `provider.lumen.models`. Each entry follows this shape
(values are filled in from the live catalog):

```json
{
  "<model-id>": {
    "name": "<model-id> via Lumen",
    "limit": { "context": 131072, "output": 8192 },
    "cost":  { "input": 0.5, "output": 1.5 }
  }
}
```

`limit` is included when the catalog reports token limits, `cost` when it
reports per-million pricing. Existing `name` values you have customized are
preserved, and `options.apiKey` in the provider block is never modified.

## Safety

- **Dry-run by default.** Nothing is written unless you pass `--apply`.
- **Timestamped backup** (`opencode.json.lumen-sync-backup-YYYYMMDD-HHMMSS`)
  is created before every write.
- **Auto-restore.** If the write fails validation, the backup is restored
  automatically.
- **Key handling.** The API key is read from the environment for API calls
  and is never written into the config by this tool.

## License

MIT. See [LICENSE](LICENSE). This is an unofficial community tool and is not
affiliated with NCSA or the University of Illinois.
