# lumen-sync

Sync coding-agent configs with the live
[NCSA Lumen](https://lumen.ncsa.illinois.edu) model catalog. Works with
[opencode](https://opencode.ai) and [pi](https://github.com/earendil-works/pi-coding-agent)
(the `@earendil-works/pi-coding-agent` CLI).

Queries the Lumen API (`GET /v1/models`) with your `LUMEN_API_KEY` and
reconciles the Lumen provider block in one or both harnesses:

- **opencode**: `provider.lumen.models` in `~/.config/opencode/opencode.json`
- **pi**: `providers.ncsa-lumen.models` in `~/.pi/agent/models.json`

It adds real models (with context and cost metadata), removes phantom
entries that no longer exist, and leaves your other providers, auth, and
settings untouched. No more hand-editing model IDs. No more stale entries
after Lumen rotates its catalog (for example when GLM 5.3 flash replaced
GLM 5.2).

## Requirements

- Python 3 (standard library only, no pip installs)
- A Lumen API key exported as `LUMEN_API_KEY`
- The harness you want to sync:
  - opencode with a config at `~/.config/opencode/opencode.json`
  - pi (v0.85+ tested) with a config at `~/.pi/agent/models.json`
- Custom paths are available for everything (`--config`, `--pi-config`, ...)

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
lumen-sync                       # dry-run: plan for BOTH harnesses, write nothing
lumen-sync --apply               # write the changes (timestamped backups first)
lumen-sync --list                # print the live Lumen catalog and exit
lumen-sync --target pi --apply   # only pi
lumen-sync --target opencode     # only opencode (v1.x behavior)
lumen-sync --apply --no-prune    # add/update but keep entries Lumen no longer lists
```

Useful options:

| Option | What it does |
|--------|--------------|
| `--target both\|opencode\|pi` | Which harness to sync (default: `both`) |
| `--config PATH` | Target a different `opencode.json` |
| `--provider-key KEY` | Sync a different provider block (default: `lumen`) |
| `--pi-config PATH` | Target a different pi `models.json` |
| `--pi-provider KEY` | Sync a different pi provider (default: `ncsa-lumen`) |
| `--base-url URL` | Different Lumen endpoint (default: `https://lumen.ncsa.illinois.edu/v1`) |
| `-q, --quiet` | Suppress non-error output (good for cron) |

Full documentation: `man lumen-sync` (installed by `install.sh`).

## What gets written

**opencode** entries (only `provider.lumen.models` is touched):

```json
{
  "<model-id>": {
    "name": "<model-id> via Lumen",
    "limit": { "context": 131072, "output": 8192 },
    "cost":  { "input": 0.5, "output": 1.5 }
  }
}
```

**pi** entries (only `providers.ncsa-lumen.models` is touched):

```json
{
  "id": "<model-id>",
  "name": "<Model Name> (Lumen)",
  "reasoning": true,
  "input": ["text"],
  "contextWindow": 1048576,
  "maxTokens": 131072,
  "cost": { "input": 0.14, "output": 0.45, "cacheRead": 0, "cacheWrite": 0 }
}
```

Existing display names you customized are preserved on both targets. If pi's
configured default model disappears from the catalog, lumen-sync points
`settings.json` at a live fallback and says so.

## Safety

- **Dry-run by default.** Nothing is written unless you pass `--apply`.
- **Timestamped backups** (`*.lumen-sync-backup-YYYYMMDD-HHMMSS`) before
  every write; automatic restore if a written file fails to re-parse.
- **Idempotent.** Safe to re-run as verification; a second run reports
  "in sync" and writes nothing.
- **Concurrency guard.** Config files are fingerprinted when read.
  `--apply` refuses to write if a file changed underneath it (another
  terminal, another agent session, or the harness rewriting its own config)
  and exits without writing or backing up. Re-run to pick up the new state.
  v2.0.1.
- **Key handling.** opencode's `options.apiKey` is never modified. pi does
  not support env-var references, so its literal key is preserved as-is and
  the file is always written mode 600. Fresh pi provider creation writes the
  key from `$LUMEN_API_KEY` (mode 600) because pi has no alternative.

## License

MIT. See [LICENSE](LICENSE). This is an unofficial community tool and is not
affiliated with NCSA or the University of Illinois.
