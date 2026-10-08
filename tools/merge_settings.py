#!/usr/bin/env python3
"""Merge the pack's settings into ~/.claude/settings.json without clobbering anything.

- Keys you already set keep your value (model, effortLevel, ...).
- Dict keys (extraKnownMarketplaces, enabledPlugins) gain the missing entries only.
- Hooks are appended unless an identical hook command is already there.

usage: merge_settings.py <pack-settings.json> <target-settings.json> [--dry-run]
"""
import json
import os
import sys


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        text = f.read().strip()
    return json.loads(text) if text else {}


def hook_commands(groups):
    return {h.get("command") for g in groups for h in g.get("hooks", [])}


def merge(base, extra, changes, path=""):
    for key, value in extra.items():
        where = f"{path}.{key}" if path else key
        if key == "hooks" and not path:
            hooks = base.setdefault("hooks", {})
            for event, groups in value.items():
                existing = hooks.setdefault(event, [])
                have = hook_commands(existing)
                for group in groups:
                    if hook_commands([group]) - have:
                        existing.append(group)
                        changes.append(f"added hook: {event} ({group.get('matcher', '*')})")
            continue
        if key not in base:
            base[key] = value
            changes.append(f"set {where}")
        elif isinstance(base[key], dict) and isinstance(value, dict):
            merge(base[key], value, changes, where)
        # anything else: keep the user's existing value


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    pack, target = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    base = load(target)
    changes = []
    merge(base, load(pack), changes)
    if not changes:
        print("settings: nothing to change")
        return
    for c in changes:
        print(f"settings: {c}")
    if not dry:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            json.dump(base, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
