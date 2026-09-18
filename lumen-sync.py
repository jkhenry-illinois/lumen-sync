#!/usr/bin/env python3
"""Sync coding-agent configs with the live NCSA Lumen model catalog.

Queries `GET <baseURL>/models` using $LUMEN_API_KEY and reconciles the
Lumen provider block in one or both supported harnesses:

  - opencode: `provider.lumen.models` in ~/.config/opencode/opencode.json
  - pi:       the `ncsa-lumen` provider (models list) in ~/.pi/agent/models.json

Adds real models (with context/cost metadata), removes phantom ones, and
leaves everything else (other providers, auth, extensions, settings) alone.

Auth is preserved as-is in each config:
  - opencode: existing `options.apiKey` is never modified (v1 behavior).
  - pi: the existing `apiKey` in the provider block is preserved. pi does not
    support env-var references, so the key lives in the file; this tool makes
    sure the file is mode 600. When creating the pi provider fresh, the key
    is written from $LUMEN_API_KEY (mode 600) because pi has no alternative.

Usage:
    python3 ~/lumen-sync.py                      # dry-run: plan for both harnesses, write nothing
    python3 ~/lumen-sync.py --apply              # write the changes (timestamped backups)
    python3 ~/lumen-sync.py --list               # just print the live Lumen catalog
    python3 ~/lumen-sync.py --target opencode    # only opencode (v1 behavior)
    python3 ~/lumen-sync.py --target pi          # only pi
    python3 ~/lumen-sync.py --apply --no-prune   # add/update but keep phantom entries
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.error
import urllib.request

DEFAULT_OPENCODE_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")
DEFAULT_PI_CONFIG = os.path.expanduser("~/.pi/agent/models.json")
DEFAULT_PI_SETTINGS = os.path.expanduser("~/.pi/agent/settings.json")
DEFAULT_BASE_URL = "https://lumen.ncsa.illinois.edu/v1"
DEFAULT_PROVIDER_KEY = "lumen"
DEFAULT_PI_PROVIDER_KEY = "ncsa-lumen"
API_KEY_ENV = "LUMEN_API_KEY"

# Preferred default model for pi if the configured default disappears from
# the catalog (checked in order against the live catalog).
PI_FALLBACK_MODELS = ["qwen3-coder-next", "glm-5.3-flash", "deepseek-v4-flash"]

QUANT_SUFFIX_RE = re.compile(r"(?i)-(FP8|BF16|FP16|FP32|INT8|INT4|AWQ|GPTQ|GGUF)$")
DATE_SUFFIX_RE = re.compile(r"-\d{4}$")


def fetch_models(base_url, api_key, timeout=20):
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Error: Lumen API returned HTTP {e.code} for {url}")
    except urllib.error.URLError as e:
        sys.exit(f"Error: could not reach Lumen at {url}: {e.reason}")
    except json.JSONDecodeError:
        sys.exit(f"Error: Lumen API at {url} did not return valid JSON")
    return data.get("data", []) if isinstance(data, dict) else data


def is_chat_model(m):
    inp = m.get("input_modalities") or []
    out = m.get("output_modalities") or []
    return "text" in inp and "text" in out


def base_name(m):
    root = m.get("root") or ""
    base = root.split("/")[-1] if root else m.get("id", "model")
    base = QUANT_SUFFIX_RE.sub("", base)
    base = DATE_SUFFIX_RE.sub("", base)
    return base


def prettify_name(m):
    return f"{base_name(m)} via Lumen"


def pi_pretty_name(m):
    base = base_name(m)
    if base and base[0].isalpha():
        base = base[0].upper() + base[1:]
    return f"{base} (Lumen)"


def build_entry(m):
    entry = {"name": prettify_name(m)}
    ctx = m.get("max_model_len")
    out = m.get("max_output_tokens")
    if isinstance(ctx, (int, float)) and isinstance(out, (int, float)):
        entry["limit"] = {"context": int(ctx), "output": int(out)}
    cin = m.get("input_cost_per_million")
    cout = m.get("output_cost_per_million")
    if isinstance(cin, (int, float)) and isinstance(cout, (int, float)):
        entry["cost"] = {"input": cin, "output": cout}
    return entry


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, config, mode=None):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    if mode is not None:
        os.chmod(path, mode)


def backup(path, quiet=False):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    b = f"{path}.lumen-sync-backup-{stamp}"
    shutil.copy2(path, b)
    if not quiet:
        print(f"  Backup: {b}")
    return b


def file_fingerprint(path):
    """SHA-256 of raw bytes, or None if the file does not exist."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def guard_fresh(path, expected_fp, label):
    """Refuse to write if the file changed since this run read it.

    Protects against clobbering concurrent edits (another terminal, another
    agent session, or the harness itself rewriting its config mid-run).
    """
    current = file_fingerprint(path)
    if current != expected_fp:
        sys.exit(
            f"Error: {path} changed since this run read it "
            f"(another session may have edited it). Nothing was written; "
            f"no backup created. Re-run lumen-sync to re-read the current state. "
            f"[{label}]"
        )


# --------------------------------------------------------------------------
# opencode target (v1 behavior, unchanged semantics)
# --------------------------------------------------------------------------

def ensure_opencode_provider(config, provider_key, base_url):
    providers = config.setdefault("provider", {})
    if provider_key not in providers:
        providers[provider_key] = {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Lumen (NCSA)",
            "options": {"baseURL": base_url, "apiKey": "{env:" + API_KEY_ENV + "}"},
            "models": {},
        }
    provider = providers[provider_key]
    provider.setdefault("models", {})
    provider.setdefault("options", {})
    provider["options"].setdefault("baseURL", base_url)
    return provider


def reconcile_opencode(config, live_ids, provider_key, base_url, prune):
    provider = ensure_opencode_provider(config, provider_key, base_url)
    existing = provider["models"]
    added, updated, removed, unchanged = [], [], [], []

    for mid, m in live_ids.items():
        entry = build_entry(m)
        if mid not in existing:
            existing[mid] = entry
            added.append(mid)
            continue
        cur = existing[mid]
        old_limit = cur.get("limit")
        old_cost = cur.get("cost")
        name_before = cur.get("name")
        cur["limit"] = entry["limit"]
        if "cost" in entry:
            cur["cost"] = entry["cost"]
        elif "cost" in cur:
            del cur["cost"]
        if name_before:
            cur["name"] = name_before
        if old_limit != cur.get("limit") or old_cost != cur.get("cost"):
            updated.append(mid)
        else:
            unchanged.append(mid)

    if prune:
        for mid in list(existing.keys()):
            if mid not in live_ids:
                del existing[mid]
                removed.append(mid)

    return added, updated, removed, unchanged


# --------------------------------------------------------------------------
# pi target
# --------------------------------------------------------------------------

def ensure_pi_provider(config, provider_key, base_url, api_key):
    providers = config.setdefault("providers", {})
    created = False
    if provider_key not in providers:
        providers[provider_key] = {
            "name": "NCSA Lumen",
            "baseUrl": base_url,
            "api": "openai-completions",
            "apiKey": api_key or "",
            "compat": {"supportsDeveloperRole": False},
            "models": [],
        }
        created = True
    provider = providers[provider_key]
    provider.setdefault("baseUrl", base_url)
    provider.setdefault("api", "openai-completions")
    provider.setdefault("apiKey", "")
    provider.setdefault("models", [])
    if not isinstance(provider["models"], list):
        sys.exit(f"Error: pi provider '{provider_key}'.models is not a list; refusing to touch it.")
    return provider, created


def build_pi_model(m, preserve_name=None):
    inp = [x for x in ("text", "image") if x in (m.get("input_modalities") or [])] or ["text"]
    entry = {
        "id": m["id"],
        "name": preserve_name or pi_pretty_name(m),
        "reasoning": bool(m.get("supports_reasoning")),
        "input": inp,
    }
    ctx = m.get("max_model_len")
    out = m.get("max_output_tokens")
    if isinstance(ctx, (int, float)):
        entry["contextWindow"] = int(ctx)
    if isinstance(out, (int, float)):
        entry["maxTokens"] = int(out)
    cin = m.get("input_cost_per_million")
    cout = m.get("output_cost_per_million")
    if isinstance(cin, (int, float)) and isinstance(cout, (int, float)):
        entry["cost"] = {"input": cin, "output": cout, "cacheRead": 0, "cacheWrite": 0}
    return entry


def reconcile_pi(provider, live_ids, prune):
    existing = provider["models"]
    by_id = {mm.get("id"): mm for mm in existing if isinstance(mm, dict) and mm.get("id")}
    added, updated, removed, unchanged = [], [], [], []

    for mid, m in live_ids.items():
        entry = build_pi_model(m, preserve_name=by_id[mid].get("name") if mid in by_id else None)
        if mid not in by_id:
            existing.append(entry)
            added.append(mid)
            continue
        cur = by_id[mid]
        before = json.dumps(cur, sort_keys=True)
        after = json.dumps(entry, sort_keys=True)
        cur.clear()
        cur.update(entry)
        if before != after:
            updated.append(mid)
        else:
            unchanged.append(mid)

    if prune:
        for mm in list(existing):
            mid = mm.get("id") if isinstance(mm, dict) else None
            if not mid or mid not in live_ids:
                existing.remove(mm)
                if mid:
                    removed.append(mid)
                else:
                    removed.append("<malformed entry>")

    return added, updated, removed, unchanged


def fix_pi_settings(settings_path, provider_key, live_ids, quiet=False):
    """If pi's default model vanished from the catalog, point it at a live one."""
    if not os.path.exists(settings_path):
        return None
    settings_fp = file_fingerprint(settings_path)
    try:
        settings = load_json(settings_path)
    except Exception as e:
        if not quiet:
            print(f"  (settings.json unreadable, skipping default-model check: {e})")
        return None
    if file_fingerprint(settings_path) != settings_fp:
        if not quiet:
            print("  (settings.json changed while being read; skipping default-model check, re-run)")
        return None
    if settings.get("defaultProvider") != provider_key:
        return None
    current = settings.get("defaultModel")
    if current in live_ids:
        return None
    fallback = next((c for c in PI_FALLBACK_MODELS if c in live_ids), None)
    if not fallback:
        fallback = sorted(live_ids.keys())[0]
    settings["defaultModel"] = fallback
    b = backup(settings_path, quiet)
    guard_fresh(settings_path, settings_fp, "pi settings")
    try:
        save_json(settings_path, settings)
        load_json(settings_path)
    except Exception as e:
        shutil.copy2(b, settings_path)
        sys.exit(f"Error: pi settings.json write failed, restored backup. ({e})")
    if not quiet:
        print(f"  pi defaultModel '{current}' no longer in catalog; set to '{fallback}'.")
    return fallback


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_list(args):
    models = fetch_models(args.base_url, args.api_key)
    chat = [m for m in models if is_chat_model(m)]
    skipped = [m for m in models if not is_chat_model(m)]
    print(f"Lumen catalog ({args.base_url}): {len(models)} total, "
          f"{len(chat)} chat-capable, {len(skipped)} skipped (non-text)")
    print()
    for m in sorted(chat, key=lambda x: x["id"]):
        cin = m.get("input_cost_per_million", "?")
        cout = m.get("output_cost_per_million", "?")
        ctx = m.get("max_model_len", "?")
        out = m.get("max_output_tokens", "?")
        reason = "reason" if m.get("supports_reasoning") else "       "
        print(f"  {m['id']:<28} ctx={ctx:<8} out={out:<8} "
              f"${cin}/{cout} per 1M  [{reason}]  {prettify_name(m)}")
    if skipped:
        print()
        print("Skipped (no text in/out):")
        for m in skipped:
            print(f"  {m['id']}  in={m.get('input_modalities')} out={m.get('output_modalities')}")
    return 0


def report_target(label, added, updated, removed, unchanged):
    if not (added or updated or removed):
        print(f"  {label}: in sync ({len(unchanged)} model(s))")
        return False
    print(f"  {label}:")
    if added:
        print(f"    + add {len(added)}: {', '.join(added)}")
    if updated:
        print(f"    ~ update {len(updated)}: {', '.join(updated)}")
    if removed:
        print(f"    - remove {len(removed)}: {', '.join(removed)}")
    if unchanged:
        print(f"    = unchanged {len(unchanged)}")
    return True


def cmd_sync(args):
    live = fetch_models(args.base_url, args.api_key)
    live_ids = {m["id"]: m for m in live if is_chat_model(m)}
    if not live_ids:
        sys.exit("Error: Lumen catalog returned no chat-capable models; refusing to sync.")

    do_opencode = args.target in ("opencode", "both")
    do_pi = args.target in ("pi", "both")

    # ---- Load everything up front; any load error aborts before writes ----
    # Fingerprint before AND after each load so a mid-read external write is
    # detected instead of silently planned-over.
    oc_config = oc_fp = None
    if do_opencode:
        if os.path.exists(args.config):
            oc_fp = file_fingerprint(args.config)
            oc_config = load_json(args.config)
            if file_fingerprint(args.config) != oc_fp:
                sys.exit(f"Error: {args.config} changed while being read; re-run lumen-sync.")
        else:
            oc_config = {}
    pi_config = pi_fp = None
    if do_pi:
        if os.path.exists(args.pi_config):
            pi_fp = file_fingerprint(args.pi_config)
            pi_config = load_json(args.pi_config)
            if file_fingerprint(args.pi_config) != pi_fp:
                sys.exit(f"Error: {args.pi_config} changed while being read; re-run lumen-sync.")
        else:
            pi_config = {}

    oc_plan = pi_plan = None
    pi_provider_created = False
    if do_opencode:
        before = len(oc_config.get("provider", {}).get(args.provider_key, {}).get("models", {}))
        oc_plan = reconcile_opencode(oc_config, live_ids, args.provider_key, args.base_url, args.prune)
        if not args.quiet:
            print(f"Plan for {args.config} (provider.{args.provider_key}.models):")
            report_target("opencode", *oc_plan)
            if before or oc_plan[0] or oc_plan[1]:
                print(f"  (existing before: {before} | live chat models: {len(live_ids)})")
    if do_pi:
        if pi_config is None:
            pi_config = {}
            if not args.quiet:
                print(f"Plan for {args.pi_config}: (file does not exist yet; will be created)")
        provider, pi_provider_created = ensure_pi_provider(pi_config, args.pi_provider, args.base_url, args.api_key)
        pi_plan = reconcile_pi(provider, live_ids, args.prune)
        if not args.quiet:
            print(f"Plan for {args.pi_config} (providers.{args.pi_provider}.models):")
            report_target("pi", *pi_plan)
            if pi_provider_created:
                print("    (pi provider created fresh; apiKey written from $LUMEN_API_KEY)")

    changed = any(plan and (plan[0] or plan[1] or plan[2]) for plan in (oc_plan, pi_plan))

    if not changed:
        if not args.quiet:
            print("In sync. No changes needed.")
        return 0

    if not args.apply:
        if not args.quiet:
            print("\nDry run only. Re-run with --apply to write changes.")
        return 0

    # ---- Apply ----
    if do_opencode and oc_plan and (oc_plan[0] or oc_plan[1] or oc_plan[2]):
        guard_fresh(args.config, oc_fp, "opencode")
        b = backup(args.config, args.quiet)
        try:
            save_json(args.config, oc_config)
            load_json(args.config)
        except Exception as e:
            shutil.copy2(b, args.config)
            sys.exit(f"Error: opencode config write failed, restored backup. ({e})")
        if not args.quiet:
            print(f"opencode config written: {args.config}")

    if do_pi and pi_plan and (pi_plan[0] or pi_plan[1] or pi_plan[2]):
        guard_fresh(args.pi_config, pi_fp, "pi")
        b = backup(args.pi_config, args.quiet)
        try:
            save_json(args.pi_config, pi_config, mode=stat.S_IRUSR | stat.S_IWUSR)
            load_json(args.pi_config)
        except Exception as e:
            shutil.copy2(b, args.pi_config)
            os.chmod(args.pi_config, stat.S_IRUSR | stat.S_IWUSR)
            sys.exit(f"Error: pi models.json write failed, restored backup. ({e})")
        if not args.quiet:
            print(f"pi models.json written (mode 600): {args.pi_config}")
        fix_pi_settings(args.pi_settings, args.pi_provider, live_ids, args.quiet)

    if not args.quiet:
        print("Done.")
        print("Restart opencode and pi sessions for changes to take effect.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Sync opencode and/or pi configs with the live NCSA Lumen catalog."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to the configs (default is dry-run).")
    parser.add_argument("--no-prune", dest="prune", action="store_false",
                        help="Keep configured models not in the Lumen catalog (don't remove phantoms).")
    parser.add_argument("--list", action="store_true", help="Print the live Lumen catalog and exit.")
    parser.add_argument("--target", choices=("both", "opencode", "pi"), default="both",
                        help="Which harness config to sync (default: both).")
    parser.add_argument("--config", default=DEFAULT_OPENCODE_CONFIG,
                        help=f"opencode.json path (default: {DEFAULT_OPENCODE_CONFIG}).")
    parser.add_argument("--provider-key", default=DEFAULT_PROVIDER_KEY,
                        help=f"Provider key in opencode.json (default: {DEFAULT_PROVIDER_KEY}).")
    parser.add_argument("--pi-config", default=DEFAULT_PI_CONFIG,
                        help=f"pi models.json path (default: {DEFAULT_PI_CONFIG}).")
    parser.add_argument("--pi-provider", default=DEFAULT_PI_PROVIDER_KEY,
                        help=f"Provider key in pi models.json (default: {DEFAULT_PI_PROVIDER_KEY}).")
    parser.add_argument("--pi-settings", default=DEFAULT_PI_SETTINGS,
                        help=f"pi settings.json path (default: {DEFAULT_PI_SETTINGS}).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"Lumen API base URL (default: {DEFAULT_BASE_URL}).")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-error output.")
    parser.set_defaults(prune=True)
    args = parser.parse_args()

    args.api_key = os.environ.get(API_KEY_ENV)
    if not args.api_key:
        sys.exit(f"Error: ${API_KEY_ENV} is not set. Export your Lumen API key before running.")

    if args.list:
        return cmd_list(args)
    return cmd_sync(args)


if __name__ == "__main__":
    sys.exit(main())
