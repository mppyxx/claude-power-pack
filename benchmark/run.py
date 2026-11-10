#!/usr/bin/env python3
"""Head-to-head benchmark: plain Claude Code vs Claude Code with Claude Power Pack.

Standard library only. Subcommands:

  python3 run.py setup       build pack/ (skills, rules, settings, MCP config) from the pack repo
  python3 run.py selftest    run every hidden checker on the starting code and on reference fixes
  python3 run.py smoke       one "reply ok" run per config; saves both init events to results/
  python3 run.py run         run the matrix (default 2 runs x 3 tasks x 2 configs, 2 at a time)
        [--runs N] [--parallel 2] [--timeout 1200] [--only plain-bugfix-1,pack-bugfix-1] [--force]
  python3 run.py score       re-score every finished run from its archived workdir and transcript
  python3 run.py report      write results.json and print the results tables (markdown)

Paths can be overridden with environment variables:
  BENCH_WORK_ROOT   where run workdirs are created (default /Users/Shared/cpb-runs on macOS,
                    /tmp/cpb-runs elsewhere). Keep it OUTSIDE your home folder: Claude Code loads
                    CLAUDE.md and .claude/CLAUDE.md from every parent directory of the workdir, so a
                    workdir under ~ would pick up ~/CLAUDE.md and ~/.claude/CLAUDE.md in BOTH configs.
  CPP_REPO          the Claude Power Pack repo checkout (default ~/claude-power-pack-repo)
  HORMOZI_DIR       hormozi-ad-factory skill folder (default ~/.claude/skills/hormozi-ad-factory)
  PACK_PLUGIN_DIRS  colon-separated plugin folders; default: auto-detected from ~/.claude/plugins
  CLAUDE_BIN        claude executable (default: "claude" on PATH)
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BENCH = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
TASKS = ["bugfix", "shipcheck", "copy"]
CONFIGS = ["plain", "pack"]
PROJECT_NAMES = {"bugfix": "lumen-checkout", "shipcheck": "team-directory", "copy": "ferrynote-launch"}
WORK_ROOT = os.environ.get("BENCH_WORK_ROOT") or ("/Users/Shared/cpb-runs" if os.path.isdir("/Users/Shared") else "/tmp/cpb-runs")
CPP_REPO = os.environ.get("CPP_REPO", os.path.join(HOME, "claude-power-pack-repo"))
HORMOZI_DIR = os.environ.get("HORMOZI_DIR", os.path.join(HOME, ".claude/skills/hormozi-ad-factory"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
PACK = os.path.join(BENCH, "pack")
RUNS = os.path.join(BENCH, "runs")
RESULTS = os.path.join(BENCH, "results")

COMMON_FLAGS = ["--model", "opus", "--setting-sources", "project", "--strict-mcp-config",
                "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence"]

MCP_CONFIG = {
    "mcpServers": {
        "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        "chrome-devtools": {"command": "npx", "args": ["-y", "chrome-devtools-mcp@latest"]},
    }
}

# plugin key in installed_plugins.json -> fallback cache glob
CACHE_PLUGINS = [
    ("superpowers@claude-plugins-official", "cache/claude-plugins-official/superpowers/*"),
    ("andrej-karpathy-skills@karpathy-skills", "cache/karpathy-skills/andrej-karpathy-skills/*"),
    ("figma@claude-plugins-official", "cache/claude-plugins-official/figma/*"),
    ("firebase@firebase", "cache/firebase/firebase/*"),
    ("brag@brag", "cache/brag/brag/*"),
]
SYNCED_PLUGINS = ["design~g2", "marketing~g2", "brand-voice", "legal~g2"]


# ----------------------------------------------------------------------------- helpers

def clean_env():
    """Run claude with a minimal environment so nothing from a parent session (effort level,
    entrypoint, host auth) leaks into either config. Login comes from the keychain as usual."""
    keep = ["HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG"]
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("LANG", "en_US.UTF-8")
    env["TERM"] = "dumb"
    return env


def plugin_dirs():
    if os.environ.get("PACK_PLUGIN_DIRS"):
        return [p for p in os.environ["PACK_PLUGIN_DIRS"].split(":") if p]
    base = os.path.join(HOME, ".claude/plugins")
    installed = {}
    try:
        installed = json.load(open(os.path.join(base, "installed_plugins.json"))).get("plugins", {})
    except Exception:
        pass
    out = []
    for key, pattern in CACHE_PLUGINS:
        path = None
        entries = installed.get(key) or []
        user = [e for e in entries if e.get("scope") == "user"] or entries
        if user and os.path.isdir(user[0].get("installPath", "")):
            path = user[0]["installPath"]
        else:
            cands = sorted(glob.glob(os.path.join(base, pattern)))
            path = cands[-1] if cands else None
        if not path:
            raise SystemExit(f"plugin not found: {key}")
        out.append(path)
    for name in SYNCED_PLUGINS:
        cands = sorted(glob.glob(os.path.join(base, "synced", "*", name)))
        if not cands:
            raise SystemExit(f"synced plugin not found: {name}")
        out.append(cands[0])
    return out


def claude_cmd(config, prompt):
    cmd = [CLAUDE_BIN, "-p", prompt] + COMMON_FLAGS
    if config == "pack":
        cmd += ["--settings", os.path.join(PACK, "settings.json"), "--mcp-config", os.path.join(PACK, "mcp.json")]
        for d in plugin_dirs():
            cmd += ["--plugin-dir", d]
    return cmd


def prepare_workdir(config, task, workdir):
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(os.path.dirname(workdir), exist_ok=True)
    if task is None:
        os.makedirs(workdir)
    else:
        shutil.copytree(os.path.join(BENCH, "tasks", task, "start"), workdir)
    if config == "pack":
        os.makedirs(os.path.join(workdir, ".claude"), exist_ok=True)
        shutil.copytree(os.path.join(PACK, "skills"), os.path.join(workdir, ".claude", "skills"))
        shutil.copy(os.path.join(PACK, "CLAUDE.md"), os.path.join(workdir, "CLAUDE.md"))


def run_claude(cmd, cwd, transcript, stderr_path, timeout):
    t0 = time.time()
    with open(transcript, "w") as out, open(stderr_path, "w") as err:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                             env=clean_env(), start_new_session=True)
        timed_out = False
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    p.wait(timeout=15)
                    break
                except subprocess.TimeoutExpired:
                    continue
            rc = p.returncode
        # MCP servers (npx) are children of claude; make sure none outlive the run
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    leftovers = kill_strays(cwd)
    return {"exit_code": rc, "timed_out": timed_out, "wall_s": round(time.time() - t0, 1),
            "stray_processes_killed": leftovers}


def kill_strays(workdir):
    """Claude Code's Bash tool starts commands in their own process groups, so a test server the
    agent backgrounds (python3 app.py &) can outlive the run and answer a later run's requests.
    Kill every process whose current directory is inside the run folder. Processes started from
    elsewhere (e.g. /tmp) are not caught; see README limitations."""
    killed = []
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn", "-u", str(os.getuid())],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return killed
    pid = None
    real = os.path.realpath(workdir)
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid and pid != os.getpid():
            path = os.path.realpath(line[1:])
            if path == real or path.startswith(real + os.sep):
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except (ProcessLookupError, PermissionError):
                    pass
    return killed


def load_events(path):
    ev = []
    if not os.path.exists(path):
        return ev
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            ev.append(json.loads(line))
        except Exception:
            pass
    return ev


# ----------------------------------------------------------------------------- transcript analysis

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
RUN_RE = re.compile(r"\bpython(?:3(?:\.\d+)?)?\b|\bpytest\b|\bunittest\b")
PYFILE = r"[\w./-]+\.py(?![\w.])"
BASH_WRITE_RE = re.compile(r"sed\s+-i|perl\s+-\w*i|>\s*" + PYFILE + r"|\btee\s+(?:-a\s+)?" + PYFILE + r"|\bpatch\b|git\s+apply|"
                           r"\b(?:cp|mv)\s+\S+\s+" + PYFILE + r"|open\([^)]*\.py['\"]\s*,\s*['\"][wa]|\.write_text\(")


def tool_calls(events):
    calls = []
    for i, e in enumerate(events):
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"i": i, "name": block.get("name"), "input": block.get("input") or {},
                              "sub": e.get("parent_tool_use_id") is not None})
    return calls


def process_metric(events):
    """Did the agent run python/pytest/unittest AFTER its last edit to a .py file, before its final answer?"""
    calls = tool_calls(events)
    last_edit = None
    for c in calls:
        if c["name"] in EDIT_TOOLS:
            fp = c["input"].get("file_path") or c["input"].get("notebook_path") or ""
            if fp.endswith(".py"):
                last_edit = c
        elif c["name"] == "Bash":
            cmd = c["input"].get("command", "")
            if BASH_WRITE_RE.search(cmd):
                last_edit = c
    if last_edit is None:
        return {"verified_after_last_edit": None, "note": "no .py edits"}
    after = [c for c in calls if c["i"] >= last_edit["i"] and c is not last_edit and c["name"] == "Bash"
             and RUN_RE.search(c["input"].get("command", ""))]
    # a Bash call that both edits and then runs python in the same command also counts
    if last_edit["name"] == "Bash":
        cmd = last_edit["input"].get("command", "")
        m = BASH_WRITE_RE.search(cmd)
        if m and RUN_RE.search(cmd[m.end():]):
            after.insert(0, {"input": {"command": "(same command, after the write) " + cmd[m.end():][-280:]}})
    le = last_edit["input"].get("file_path") or last_edit["input"].get("command", "")[:200]
    return {
        "verified_after_last_edit": bool(after),
        "last_edit": f"{last_edit['name']}: {le}",
        "run_after": after[0]["input"].get("command", "")[:300] if after else None,
    }


def usage_summary(events):
    calls = tool_calls(events)
    by_name = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1
    skills = [c["input"].get("skill") or c["input"].get("command") for c in calls if c["name"] == "Skill"]
    subagents = [c["input"].get("subagent_type") or "general" for c in calls if c["name"] in ("Task", "Agent")]
    mcp = sorted({c["name"] for c in calls if (c["name"] or "").startswith("mcp__")})
    return {"tool_calls": len(calls), "by_tool": by_name, "skills_invoked": skills, "subagents": subagents,
            "mcp_tools_used": mcp, "subagent_tool_calls": sum(1 for c in calls if c["sub"])}


def result_event(events):
    for e in reversed(events):
        if e.get("type") == "result":
            return e
    return None


def init_event(events):
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            return e
    return None


# ----------------------------------------------------------------------------- scoring

def check_task(task, final_dir):
    script = {"bugfix": "hidden_tests.py", "shipcheck": "hidden_checks.py", "copy": "score_copy.py"}[task]
    path = os.path.join(BENCH, "tasks", task, "check", script)
    p = subprocess.run([sys.executable, "-I", path, final_dir], capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"checker_error": (p.stderr or p.stdout)[-1500:]}


def score_run(run_id):
    rdir = os.path.join(RUNS, run_id)
    meta = json.load(open(os.path.join(rdir, "meta.json")))
    task, config = meta["task"], meta["config"]
    events = load_events(os.path.join(rdir, "transcript.jsonl"))
    results = [e for e in events if e.get("type") == "result"]
    res = results[-1] if results else {}
    final = os.path.join(rdir, "final")
    check = check_task(task, final) if os.path.isdir(final) else {"checker_error": "no archived workdir"}
    failed = meta.get("timed_out") or not res
    s = {
        "id": run_id, "config": config, "task": task, "n": meta["n"],
        "status": "timeout" if meta.get("timed_out") else ("no_result" if not res else ("error" if res.get("is_error") else "ok")),
        "exit_code": meta.get("exit_code"), "wall_s": meta.get("wall_s"),
        # A session can end in more than one result event (e.g. the agent waits for a background
        # subagent, answers, then answers again when it finishes). duration_ms and num_turns are per
        # result event, total_cost_usd is cumulative. We keep the final event's values as reported,
        # plus sums across all result events, plus wall-clock time from launch to exit.
        "result_events": len(results),
        "duration_ms_final_event": res.get("duration_ms"),
        "duration_ms": sum(r.get("duration_ms") or 0 for r in results) or None,
        "num_turns_final_event": res.get("num_turns"),
        "num_turns": sum(r.get("num_turns") or 0 for r in results) or None,
        "total_cost_usd": max((r.get("total_cost_usd") or 0 for r in results), default=None),
        "result_subtype": res.get("subtype"),
        "is_error": any(r.get("is_error") for r in results) if results else None,
        "check": check,
        "usage": usage_summary(events),
        "final_message": (res.get("result") or "")[:4000],
    }
    if task == "bugfix":
        s["score"] = 0 if failed else check.get("score", 0)
        s["score_max"] = 10
    elif task == "shipcheck":
        s["score"] = 0 if failed else check.get("score", 0)
        s["score_max"] = 6
        s["func_score"] = 0 if failed else check.get("func_score", 0)
        s["func_max"] = 7
    else:
        if failed or not check.get("post_exists"):
            s.update({"post_exists": bool(check.get("post_exists")) and not failed, "em_dashes": None,
                      "filler_total": None, "words": None})
        else:
            s.update({"post_exists": True, "em_dashes": check["em_dashes"], "filler_total": check["filler_total"],
                      "words": check["words"], "en_dashes": check["en_dashes"], "filler_hits": check["filler_hits"]})
    if task in ("bugfix", "shipcheck"):
        pm = process_metric(events)
        if failed:
            pm["verified_after_last_edit"] = False
            pm["note"] = "run failed or timed out"
        s["process"] = pm
    json.dump(s, open(os.path.join(rdir, "score.json"), "w"), indent=2)
    return s


# ----------------------------------------------------------------------------- commands

def cmd_setup(_):
    if os.path.exists(PACK):
        shutil.rmtree(PACK)
    os.makedirs(PACK)
    shutil.copytree(os.path.join(CPP_REPO, "skills"), os.path.join(PACK, "skills"))
    shutil.copytree(HORMOZI_DIR, os.path.join(PACK, "skills", "hormozi-ad-factory"))
    shutil.copy(os.path.join(CPP_REPO, "rules", "CLAUDE.md"), os.path.join(PACK, "CLAUDE.md"))
    shutil.copy(os.path.join(CPP_REPO, "settings", "settings.json"), os.path.join(PACK, "settings.json"))
    json.dump(MCP_CONFIG, open(os.path.join(PACK, "mcp.json"), "w"), indent=2)
    n = len([d for d in os.listdir(os.path.join(PACK, "skills")) if os.path.isfile(os.path.join(PACK, "skills", d, "SKILL.md"))])
    print(f"pack/ built: {n} skills, rules, settings, mcp.json")
    print("plugins:", *plugin_dirs(), sep="\n  ")


def cmd_selftest(_):
    cases = [("bugfix", "start", 2), ("bugfix", "reference_partial", 4), ("bugfix", "reference", 10),
             ("shipcheck", "start", 0), ("shipcheck", "reference_fallback", 5), ("shipcheck", "reference", 6)]
    ok = True
    for task, d, want in cases:
        src = os.path.join(BENCH, "tasks", task, d)
        tmp = os.path.join(WORK_ROOT, "_selftest", task, d)
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        shutil.copytree(os.path.join(BENCH, "tasks", task, "start"), tmp)
        for f in os.listdir(src):
            sp = os.path.join(src, f)
            if os.path.isfile(sp):
                shutil.copy(sp, os.path.join(tmp, f))
        r = check_task(task, tmp)
        got = r.get("score")
        extra = f" func {r.get('func_score')}/{r.get('func_max')}" if task == "shipcheck" else ""
        print(f"{task:10s} {d:20s} score {got}/{r.get('max')}{extra}  (expected {want})")
        ok &= got == want and (task != "shipcheck" or r.get("func_score") == 7)
    for name, text, want in [("clean", "Ferrynote is live today. Try it.", 0),
                             ("sloppy", "We leverage a seamless, robust platform — unlock it.", 4)]:
        sys.path.insert(0, os.path.join(BENCH, "tasks", "copy", "check"))
        from score_copy import score_text
        r = score_text(text)
        print(f"copy       {name:20s} filler {r['filler_total']} em_dashes {r['em_dashes']}  (expected filler {want})")
        ok &= r["filler_total"] == want
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def summarize_init(init, plain_skills=None):
    skills = init.get("skills", [])
    return {
        "model": init.get("model"),
        "cwd": init.get("cwd"),
        "n_skills": len(skills),
        "extra_skills_vs_plain": sorted(set(skills) - set(plain_skills)) if plain_skills is not None else None,
        "plugins": [f"{p.get('name')} {p.get('version', '')}".strip() for p in init.get("plugins", [])],
        "mcp_servers": init.get("mcp_servers"),
        "memory_paths": init.get("memory_paths"),
        "agents": init.get("agents"),
        "n_tools": len(init.get("tools", [])),
        "claude_code_version": init.get("claude_code_version"),
    }


def cmd_smoke(_):
    os.makedirs(RESULTS, exist_ok=True)
    inits = {}
    for config in CONFIGS:
        wd = os.path.join(WORK_ROOT, "smoke-" + config, "project")
        prepare_workdir(config, None, wd)
        tr = os.path.join(RESULTS, f"smoke-{config}.jsonl")
        info = run_claude(claude_cmd(config, "reply ok"), wd, tr, tr + ".stderr", 300)
        ev = load_events(tr)
        init, res = init_event(ev), result_event(ev)
        if not init:
            print(config, "NO INIT EVENT", info)
            return 1
        json.dump(init, open(os.path.join(RESULTS, f"smoke-init-{config}.json"), "w"), indent=2)
        inits[config] = init
        print(config, info, "result:", (res or {}).get("result"), "cost:", (res or {}).get("total_cost_usd"))
    plain_skills = inits["plain"].get("skills", [])
    summary = {"plain": summarize_init(inits["plain"]), "pack": summarize_init(inits["pack"], plain_skills)}
    pack_dir_skills = set(os.listdir(os.path.join(PACK, "skills")))
    extra = summary["pack"]["extra_skills_vs_plain"]
    summary["pack"]["extra_project_skills"] = sorted(s for s in extra if s in pack_dir_skills)
    summary["pack"]["extra_plugin_skills"] = sorted(s for s in extra if ":" in s)
    summary["pack"]["extra_other"] = sorted(s for s in extra if s not in pack_dir_skills and ":" not in s)
    summary["pack"]["pack_skill_folders_not_loaded"] = sorted(pack_dir_skills - set(inits["pack"].get("skills", [])))
    json.dump(summary, open(os.path.join(RESULTS, "smoke-summary.json"), "w"), indent=2)
    p, k = summary["plain"], summary["pack"]
    print(f"PLAIN: {p['n_skills']} skills, plugins {p['plugins']}, mcp {p['mcp_servers']}")
    print(f"PACK:  {k['n_skills']} skills (+{len(extra)}: {len(k['extra_project_skills'])} project skills, "
          f"{len(k['extra_plugin_skills'])} plugin skills, {len(k['extra_other'])} other), plugins {len(k['plugins'])}: {k['plugins']}")
    print(f"       mcp {[m['name'] + ':' + m['status'] for m in k['mcp_servers']]}")
    print(f"       pack skill folders that did not load: {k['pack_skill_folders_not_loaded']}")
    return 0


def run_one(run_id, timeout):
    config, task, n = run_id.split("-")
    rdir = os.path.join(RUNS, run_id)
    if os.path.exists(rdir):
        shutil.rmtree(rdir)
    os.makedirs(rdir)
    token = "%08x" % random.getrandbits(32)
    workdir = os.path.join(WORK_ROOT, "r" + token, PROJECT_NAMES[task])
    prepare_workdir(config, task, workdir)
    prompt = open(os.path.join(BENCH, "tasks", task, "prompt.txt")).read().strip()
    cmd = claude_cmd(config, prompt)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{started}] start {run_id} in {workdir}", flush=True)
    info = run_claude(cmd, workdir, os.path.join(rdir, "transcript.jsonl"), os.path.join(rdir, "stderr.log"), timeout)
    final = os.path.join(rdir, "final")
    shutil.copytree(workdir, final, symlinks=True,
                    ignore=lambda d, names: [n for n in names if (n == "skills" and d.endswith(os.sep + ".claude")) or n == "__pycache__"])
    meta = {"id": run_id, "config": config, "task": task, "n": int(n), "workdir": workdir, "started": started,
            "cmd": cmd, "prompt": prompt, **info}
    json.dump(meta, open(os.path.join(rdir, "meta.json"), "w"), indent=2)
    s = score_run(run_id)
    brief = {k: s.get(k) for k in ("status", "score", "func_score", "em_dashes", "filler_total", "words",
                                    "duration_ms", "num_turns", "total_cost_usd") if s.get(k) is not None}
    if "process" in s:
        brief["verified"] = s["process"].get("verified_after_last_edit")
    print(f"[{time.strftime('%H:%M:%S')}] done  {run_id}: {brief}", flush=True)
    return s


def cmd_run(a):
    ids = []
    for n in range(1, a.runs + 1):
        for task in TASKS:
            for config in CONFIGS:
                ids.append(f"{config}-{task}-{n}")
    if a.only:
        want = set(a.only.split(","))
        ids = [i for i in ids if i in want]
    if not a.force:
        ids = [i for i in ids if not os.path.exists(os.path.join(RUNS, i, "score.json"))]
    print("runs:", ids, flush=True)
    # Run in groups (default: the PLAIN and PACK run of the same task and repeat together), and wait
    # for the whole group before starting the next, so both configs always share the same API conditions.
    groups = [ids[i:i + a.parallel] for i in range(0, len(ids), a.parallel)]
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        for g in groups:
            futs = [ex.submit(run_one, i, a.timeout) for i in g]
            for f in futs:
                try:
                    f.result()
                except Exception as e:
                    print("run crashed:", repr(e), flush=True)


def cmd_score(_):
    for rid in sorted(os.listdir(RUNS)):
        if os.path.exists(os.path.join(RUNS, rid, "meta.json")):
            s = score_run(rid)
            print(rid, s.get("status"), s.get("score"), s.get("func_score"), s.get("em_dashes"), s.get("filler_total"))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def cmd_report(_):
    runs = []
    for rid in sorted(os.listdir(RUNS)):
        p = os.path.join(RUNS, rid, "score.json")
        if os.path.exists(p):
            runs.append(json.load(open(p)))
    agg = {}
    for config in CONFIGS:
        r = [x for x in runs if x["config"] == config]
        by = lambda t: [x for x in r if x["task"] == t]
        proc = [x["process"]["verified_after_last_edit"] for x in r if "process" in x]
        agg[config] = {
            "runs": len(r),
            "bugfix_tests_passed_of_10": mean([x["score"] for x in by("bugfix")]),
            "shipcheck_issues_fixed_of_6": mean([x["score"] for x in by("shipcheck")]),
            "shipcheck_functionality_of_7": mean([x["func_score"] for x in by("shipcheck")]),
            "copy_em_dashes": mean([x.get("em_dashes") for x in by("copy")]),
            "copy_filler_terms": mean([x.get("filler_total") for x in by("copy")]),
            "copy_words": mean([x.get("words") for x in by("copy")]),
            "verified_before_done": f"{sum(1 for v in proc if v)}/{len(proc)}",
            "verified_before_done_rate": round(sum(1 for v in proc if v) / len(proc), 3) if proc else None,
            "wall_s_mean": mean([x.get("wall_s") for x in r]),
            "api_turn_time_s_mean": mean([(x["duration_ms"] or 0) / 1000 for x in r if x.get("duration_ms")]),
            "turns_mean": mean([x.get("num_turns") for x in r]),
            "cost_usd_mean": mean([x.get("total_cost_usd") for x in r]),
            "cost_usd_total": round(sum(x.get("total_cost_usd") or 0 for x in r), 4),
            "failures": [x["id"] for x in r if x["status"] != "ok"],
            "per_task": {t: {"wall_s_mean": mean([x.get("wall_s") for x in by(t)]),
                             "turns_mean": mean([x.get("num_turns") for x in by(t)]),
                             "cost_usd_mean": mean([x.get("total_cost_usd") for x in by(t)])} for t in TASKS},
        }
    smoke = {}
    sp = os.path.join(RESULTS, "smoke-summary.json")
    if os.path.exists(sp):
        smoke = json.load(open(sp))
    notes = []
    np_ = os.path.join(BENCH, "notes.json")
    if os.path.exists(np_):
        notes = json.load(open(np_))
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "notes": notes,
        "claude_flags_common": COMMON_FLAGS,
        "work_root": WORK_ROOT,
        "smoke": smoke,
        "averages": agg,
        "runs": [{k: v for k, v in x.items() if k != "final_message"} for x in runs],
    }
    json.dump(out, open(os.path.join(BENCH, "results.json"), "w"), indent=2)

    def f(v, d=1):
        return "n/a" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))
    P, K = agg["plain"], agg["pack"]
    rows = [
        ("Bugfix: hidden tests passed (of 10)", f(P["bugfix_tests_passed_of_10"]), f(K["bugfix_tests_passed_of_10"])),
        ("Shipcheck: seeded issues fixed (of 6)", f(P["shipcheck_issues_fixed_of_6"]), f(K["shipcheck_issues_fixed_of_6"])),
        ("Shipcheck: normal behaviour still working (of 7)", f(P["shipcheck_functionality_of_7"]), f(K["shipcheck_functionality_of_7"])),
        ("Copy: em dashes in post", f(P["copy_em_dashes"]), f(K["copy_em_dashes"])),
        ("Copy: AI-filler terms in post", f(P["copy_filler_terms"]), f(K["copy_filler_terms"])),
        ("Copy: words in post", f(P["copy_words"], 0), f(K["copy_words"], 0)),
        ("Ran code/tests after last edit (bugfix + shipcheck runs)", P["verified_before_done"], K["verified_before_done"]),
        ("Wall-clock time per run, seconds (mean)", f(P["wall_s_mean"], 0), f(K["wall_s_mean"], 0)),
        ("Turns per run (mean)", f(P["turns_mean"]), f(K["turns_mean"])),
        ("Cost per run, USD (mean, Claude Code's estimate)", f(P["cost_usd_mean"], 2), f(K["cost_usd_mean"], 2)),
        ("Failed or timed-out runs", str(len(P["failures"])), str(len(K["failures"]))),
    ]
    print("| Metric | Plain | Pack |\n|---|---|---|")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]} |")
    print("\n| Task | Wall-clock s, Plain | Wall-clock s, Pack | Turns, Plain | Turns, Pack | Cost USD, Plain | Cost USD, Pack |")
    print("|---|---|---|---|---|---|---|")
    for t in TASKS:
        a, b = P["per_task"][t], K["per_task"][t]
        print(f"| {t} | {f(a['wall_s_mean'], 0)} | {f(b['wall_s_mean'], 0)} | {f(a['turns_mean'])} | {f(b['turns_mean'])} | "
              f"{f(a['cost_usd_mean'], 2)} | {f(b['cost_usd_mean'], 2)} |")
    print(f"| all 6 runs, total cost | | | | | {P['cost_usd_total']:.2f} | {K['cost_usd_total']:.2f} |")
    print("\n| Run | Status | Score | Extra | Ran tests after last edit | Time (s) | Turns | Cost (USD) | Skills used |")
    print("|---|---|---|---|---|---|---|---|---|")
    for x in runs:
        if x["task"] == "bugfix":
            sc, ex = f"{x['score']}/10", ""
        elif x["task"] == "shipcheck":
            sc, ex = f"{x['score']}/6 issues", f"{x['func_score']}/7 still working"
        else:
            sc = "no post" if not x.get("post_exists") else f"{x['em_dashes']} em dashes, {x['filler_total']} filler"
            ex = f"{x.get('words')} words" if x.get("post_exists") else ""
        v = x.get("process", {}).get("verified_after_last_edit")
        v = "n/a" if "process" not in x else ("yes" if v else ("no" if v is False else "n/a (no .py edits)"))
        sk = ", ".join(x["usage"]["skills_invoked"]) or "none"
        dur = f(x.get("wall_s"), 0) if x.get("wall_s") is not None else "n/a"
        print(f"| {x['id']} | {x['status']} | {sc} | {ex} | {v} | {dur} | {x.get('num_turns')} | "
              f"{f(x.get('total_cost_usd'), 2)} | {sk} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("selftest")
    sub.add_parser("smoke")
    r = sub.add_parser("run")
    r.add_argument("--runs", type=int, default=2)
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--timeout", type=int, default=1200)
    r.add_argument("--only", default="")
    r.add_argument("--force", action="store_true")
    sub.add_parser("score")
    sub.add_parser("report")
    a = ap.parse_args()
    return {"setup": cmd_setup, "selftest": cmd_selftest, "smoke": cmd_smoke, "run": cmd_run,
            "score": cmd_score, "report": cmd_report}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
