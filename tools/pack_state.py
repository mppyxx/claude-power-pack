#!/usr/bin/env python3
"""Claude Power Pack bookkeeping: which skills and rules the pack installed, and whether the user changed them.

The manifest lives at <claude dir>/power-pack/manifest.json. It records a fingerprint of every skill
the pack put in place and of the rules section it wrote. Updates only replace things whose fingerprint
still matches, so anything the user edited (or already had before installing) is never touched.

usage:
  pack_state.py skills  --src DIR --dst DIR --manifest FILE --mode install|force|update [--group NAME] [--backup DIR] [--dry-run]
  pack_state.py rules   --src FILE --dst FILE --manifest FILE --mode install|force|update [--dry-run]
  pack_state.py stamp   --manifest FILE --version V --commit C [--auto-update on|off|keep]
  pack_state.py get     --manifest FILE KEY
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

IGNORE = {"node_modules", ".venv", "__pycache__", ".DS_Store", ".pytest_cache"}
START = "<!-- claude-power-pack:start -->"
END = "<!-- claude-power-pack:end -->"


def fingerprint(path):
    """sha256 over every file's relative path and bytes, skipping folders that tools create later."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE)
        for name in sorted(files):
            if name in IGNORE or name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            h.update(os.path.relpath(full, path).encode())
            h.update(b"\0")
            with open(full, "rb") as f:
                h.update(f.read())
            h.update(b"\0")
    return h.hexdigest()


def text_fingerprint(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def copy_skill(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*IGNORE, "*.pyc"))


def replace_skill(src, dst, backup):
    """Move the old copy to the backup folder (never delete), then copy the new one, keeping tool-made folders."""
    keep = {d: os.path.join(dst, d) for d in IGNORE if os.path.isdir(os.path.join(dst, d))}
    os.makedirs(backup, exist_ok=True)
    target = os.path.join(backup, os.path.basename(dst))
    if os.path.exists(target):
        target += "-" + str(int(time.time()))
    shutil.move(dst, target)
    copy_skill(src, dst)
    for name in keep:  # node_modules / .venv the user's tools built stay usable
        moved = os.path.join(target, name)
        if os.path.isdir(moved) and not os.path.exists(os.path.join(dst, name)):
            shutil.move(moved, os.path.join(dst, name))


def cmd_skills(a):
    m = load(a.manifest)
    owned = m.setdefault("skills", {})
    added, replaced, updated, kept_user, kept_edited, same = [], [], [], [], [], []
    if not a.dry_run:
        os.makedirs(a.dst, exist_ok=True)
    for name in sorted(os.listdir(a.src)):
        src = os.path.join(a.src, name)
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        dst = os.path.join(a.dst, name)
        new_fp = fingerprint(src)
        if not os.path.exists(dst):
            if not a.dry_run:
                copy_skill(src, dst)
                owned[name] = {"fp": new_fp, "group": a.group}
            added.append(name)
            continue
        if a.mode == "force":
            if not a.dry_run:
                replace_skill(src, dst, a.backup or os.path.join(os.path.dirname(a.manifest), "backups"))
                owned[name] = {"fp": new_fp, "group": a.group}
            replaced.append(name)
            continue
        rec = owned.get(name)
        if rec is None and fingerprint(dst) == new_fp:
            if not a.dry_run:  # identical to the pack's copy (e.g. installed by an older version): adopt it
                owned[name] = {"fp": new_fp, "group": a.group}
            same.append(name) if a.mode == "update" else kept_user.append(name)
            continue
        if a.mode == "install" or rec is None:
            kept_user.append(name)  # the user's own skill with this name: never ours to touch
            continue
        cur_fp = fingerprint(dst)
        if cur_fp != rec["fp"]:
            kept_edited.append(name)  # we installed it, but the user changed it since
            continue
        if cur_fp == new_fp:
            same.append(name)
            continue
        if not a.dry_run:
            replace_skill(src, dst, a.backup or os.path.join(os.path.dirname(a.manifest), "backups"))
            owned[name] = {"fp": new_fp, "group": a.group}
        updated.append(name)
    gone = sorted(n for n, r in owned.items() if r.get("group") == a.group and not os.path.isdir(os.path.join(a.src, n)))
    if not a.dry_run:
        save(a.manifest, m)
    if a.mode == "update":
        print(f"updated {len(updated)}, added {len(added)}, unchanged {len(same)}, kept {len(kept_edited)} you edited")
        if updated:
            print("updated: " + ", ".join(updated))
        if added:
            print("new: " + ", ".join(added))
        if kept_edited:
            print("kept your edited version of: " + ", ".join(kept_edited))
        if gone:
            print("no longer in the pack (left in place): " + ", ".join(gone))
    elif a.dry_run:
        print(f"would add {len(added)}, replace {len(replaced)}, keep your own copy of {len(kept_user)}")
    else:
        print(f"added {len(added)}, replaced {len(replaced)}, left {len(kept_user)} that were already there alone (--force replaces them with the pack copy)")


def cmd_rules(a):
    m = load(a.manifest)
    body = open(a.src).read().rstrip()
    block = f"{START}\n{body}\n{END}\n"
    old = open(a.dst).read() if os.path.exists(a.dst) else ""
    has = START in old and END in old
    current = old[old.index(START) + len(START):old.index(END)] if has else ""
    msg = None
    if has and a.mode == "install":
        if not m.get("rules_fp") and not a.dry_run and text_fingerprint(current) == text_fingerprint(body):
            m["rules_fp"] = text_fingerprint(body)  # unedited pack rules from an older install: adopt them
            save(a.manifest, m)
        print(f"{a.dst}: the pack's rules are already there, left as you have them (--force resets them to the pack's version)")
        return
    if has and a.mode == "update":
        if text_fingerprint(current) != m.get("rules_fp"):
            print(f"{a.dst}: you edited the pack's rules, so they were kept. The new version is in the pack's rules/CLAUDE.md")
            return
        if text_fingerprint(current) == text_fingerprint(body):
            print(f"{a.dst}: rules already up to date")
            return
        new = old[:old.index(START)] + block + old[old.index(END) + len(END):].lstrip("\n")
        msg = "updated the pack's rules to the new version"
    elif has:  # force
        new = old[:old.index(START)] + block + old[old.index(END) + len(END):].lstrip("\n")
        msg = "reset the pack's section to the original (your old file is in the backup)"
    elif old.strip():
        new = old.rstrip() + "\n\n" + block
        msg = "added the pack's rules below your existing ones"
    else:
        new = block
        msg = "created it"
    if a.dry_run:
        print(f"{a.dst}: would have {msg}")
        return
    os.makedirs(os.path.dirname(a.dst) or ".", exist_ok=True)
    with open(a.dst, "w") as f:
        f.write(new)
    m["rules_fp"] = text_fingerprint(body)
    save(a.manifest, m)
    print(f"{a.dst}: {msg}")


def cmd_stamp(a):
    m = load(a.manifest)
    previous = m.get("version")
    m["version"] = a.version
    m["commit"] = a.commit
    m["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    m.setdefault("installed_at", m["updated_at"])
    if a.auto_update in ("on", "off"):
        m["auto_update"] = a.auto_update == "on"
    m.setdefault("auto_update", True)
    m["repo"] = "https://github.com/mppyxx/claude-power-pack"
    save(a.manifest, m)
    print(previous or "")


def cmd_get(a):
    v = load(a.manifest).get(a.key)
    if isinstance(v, bool):
        v = "on" if v else "off"
    print("" if v is None else v)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("skills")
    s.add_argument("--src", required=True); s.add_argument("--dst", required=True)
    s.add_argument("--manifest", required=True); s.add_argument("--mode", required=True, choices=["install", "force", "update"])
    s.add_argument("--group", default="skills"); s.add_argument("--backup"); s.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("rules")
    r.add_argument("--src", required=True); r.add_argument("--dst", required=True)
    r.add_argument("--manifest", required=True); r.add_argument("--mode", required=True, choices=["install", "force", "update"])
    r.add_argument("--dry-run", action="store_true")
    t = sub.add_parser("stamp")
    t.add_argument("--manifest", required=True); t.add_argument("--version", required=True); t.add_argument("--commit", required=True)
    t.add_argument("--auto-update", default="keep", choices=["on", "off", "keep"])
    g = sub.add_parser("get")
    g.add_argument("--manifest", required=True); g.add_argument("key")
    a = p.parse_args()
    {"skills": cmd_skills, "rules": cmd_rules, "stamp": cmd_stamp, "get": cmd_get}[a.cmd](a)


if __name__ == "__main__":
    main()
