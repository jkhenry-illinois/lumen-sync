#!/usr/bin/env python3
"""Sync the opencode `lumen` provider with the live NCSA Lumen catalog.

Queries `GET <baseURL>/models` using $LUMEN_API_KEY and reconciles the
`provider.lumen.models` block in opencode.json so it always reflects what
Lumen actually hosts -- adding real models (with context/cost metadata),
removing phantom ones, and leaving your other providers untouched.

The API key is read from the environment for this script's own request and is
never written into the config by this tool. Existing `options.apiKey` values in
the config are preserved as-is.

Usage:
    python3 ~/lumen-sync.py                # dry-run: show the plan, write nothing
    python3 ~/lumen-sync.py --apply        # write the changes (timestamped backup)
    python3 ~/lumen-sync.py --list         # just print the live Lumen catalog
    python3 ~/lumen-sync.py --apply --no-prune   # add/update but keep phantom entries
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request

DEFAULT_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")
DEFAULT_BASE_URL = "https://lumen.ncsa.illinois.edu/v1"
DEFAULT_PROVIDER_KEY = "lumen"
API_KEY_ENV = "LUMEN_API_KEY"

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


def prettify_name(m):
    root = m.get("root") or ""
    base = root.split("/")[-1] if root else m.get("id", "model")
    base = QUANT_SUFFIX_RE.sub("", base)
    base = DATE_SUFFIX_RE.sub("", base)
    return f"{base} via Lumen"


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


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(path, config):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def ensure_provider(config, provider_key, base_url):
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


def reconcile(config, live_models, provider_key, base_url, prune):
    provider = ensure_provider(config, provider_key, base_url)
    existing = provider["models"]
    live_ids = {m["id"]: m for m in live_models if is_chat_model(m)}

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


def cmd_sync(args):
    config = load_config(args.config)
    existing = config.get("provider", {}).get(args.provider_key, {}).get("models", {})
    before_count = len(existing)
    live = fetch_models(args.base_url, args.api_key)

    added, updated, removed, unchanged = reconcile(
        config, live, args.provider_key, args.base_url, args.prune
    )

    if not (added or updated or removed):
        if not args.quiet:
            print(f"In sync. {len(unchanged)} model(s) up to date. No changes needed.")
        return 0

    if not args.quiet:
        print(f"Plan for {args.config} (provider.{args.provider_key}.models):")
        if added:
            print(f"  + add {len(added)}: {', '.join(added)}")
        if updated:
            print(f"  ~ update {len(updated)}: {', '.join(updated)}")
        if removed:
            print(f"  - remove {len(removed)}: {', '.join(removed)}")
        if unchanged:
            print(f"  = unchanged {len(unchanged)}")
        print(f"  (existing before: {before_count} | live chat models: "
              f"{len([m for m in live if is_chat_model(m)])})")

    if not args.apply:
        if not args.quiet:
            print("\nDry run only. Re-run with --apply to write changes.")
        return 0

    backup = args.config + ".lumen-sync-backup-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(args.config, backup)
    if not args.quiet:
        print(f"Backup: {backup}")

    try:
        save_config(args.config, config)
        load_config(args.config)
    except Exception as e:
        shutil.copy2(backup, args.config)
        sys.exit(f"Error: write/validate failed, restored backup. ({e})")

    if not args.quiet:
        print(f"Done. Config written to {args.config}.")
        print("Restart opencode for changes to take effect.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Sync the opencode lumen provider with the live NCSA Lumen catalog."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to the config (default is dry-run).")
    parser.add_argument("--no-prune", dest="prune", action="store_false",
                        help="Keep config models not in the Lumen catalog (don't remove phantoms).")
    parser.add_argument("--list", action="store_true", help="Print the live Lumen catalog and exit.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"opencode.json path (default: {DEFAULT_CONFIG}).")
    parser.add_argument("--provider-key", default=DEFAULT_PROVIDER_KEY,
                        help=f"Provider key in opencode.json (default: {DEFAULT_PROVIDER_KEY}).")
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
