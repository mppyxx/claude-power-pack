# Claude Power Pack benchmark: plain Claude Code vs Claude Code with the pack

A small head-to-head test: the same model, prompts and starting files, run once as plain Claude Code (PLAIN) and once with Claude Power Pack loaded (PACK). Three tasks, two runs per task per config, 12 runs in total, scored by hidden checkers the agent never sees. Run on 2026-10-08.

## Short version

- **Code tasks: no measurable difference in outcome.** Both configs passed 10/10 hidden tests in every bugfix run. On the security task PACK fixed 6/6 seeded issues in both runs and PLAIN fixed 6/6 and 5/6. That one missing point is a judgment call about a fallback secret key (details below). With a looser rule the code tasks are a tie.
- **Both configs ran their tests after their last edit in every code run (4/4 each).** Plain Claude Code already does this on these tasks.
- **Copy: PACK removed em dashes (4.5 per post down to 0).** Neither config used any of the listed AI-filler words. The pack's rules file bans em dashes and lists most of those words by name, so this result mostly shows that Claude follows a written instruction.
- **PACK cost about 4.3 times as much and took about 2.9 times as long** (mean $2.43 vs $0.57 per run, 542 s vs 188 s). It loads about 14,500 extra tokens of context on every request, and on every code task it started a review subagent.
- **One unscored thing PACK did better:** its review subagents caught a bug the agent had introduced while fixing the security task. A login token with a non-ASCII byte made the server return 500 instead of 401. Both PLAIN results still have that bug and both PACK results don't. This was found after looking at the results, so treat it as an anecdote, not a score.

With 2 runs per cell, none of these differences is statistically meaningful. Read them as "what happened in 12 runs", not as a measured effect.

## Results

Averages per config (2 runs per task per config):

| Metric | Plain | Pack |
|---|---|---|
| Bugfix: hidden tests passed (of 10) | 10 | 10 |
| Shipcheck: seeded issues fixed (of 6) | 5.5 | 6 |
| Shipcheck: normal behaviour still working (of 7) | 7 | 7 |
| Copy: em dashes in post | 4.5 | 0 |
| Copy: AI-filler terms in post | 0 | 0 |
| Copy: words in post | 328 | 345 |
| Ran code/tests after last edit (bugfix + shipcheck runs) | 4/4 | 4/4 |
| Wall-clock time per run, seconds (mean) | 188 | 542 |
| Turns per run (mean) | 9 | 21 |
| Cost per run, USD (mean, Claude Code's estimate) | 0.57 | 2.43 |
| Failed or timed-out runs | 0 | 0 |

Time and cost by task:

| Task | Wall-clock s, Plain | Wall-clock s, Pack | Turns, Plain | Turns, Pack | Cost USD, Plain | Cost USD, Pack |
|---|---|---|---|---|---|---|
| bugfix | 263 | 712 | 10.5 | 24 | 0.67 | 3.18 |
| shipcheck | 267 | 782 | 12.5 | 33 | 0.88 | 3.70 |
| copy | 35 | 132 | 4 | 6 | 0.17 | 0.41 |
| all 6 runs, total cost | | | | | 3.42 | 14.58 |

Per run:

| Run | Status | Score | Extra | Ran tests after last edit | Time (s) | Turns | Cost (USD) | Skills used | Subagents |
|---|---|---|---|---|---|---|---|---|---|
| plain-bugfix-1 | ok | 10/10 | | yes | 214 | 9 | 0.56 | none | 0 |
| plain-bugfix-2 | ok | 10/10 | | yes | 311 | 12 | 0.78 | none | 0 |
| pack-bugfix-1 | ok | 10/10 | | yes | 829 | 27 | 3.59 | superpowers:systematic-debugging, superpowers:verification-before-completion | 1 (review) |
| pack-bugfix-2 | ok | 10/10 | | yes | 595 | 21 | 2.78 | superpowers:systematic-debugging | 1 (review) |
| plain-shipcheck-1 | ok | 6/6 issues | 7/7 still working | yes | 281 | 12 | 0.83 | none | 0 |
| plain-shipcheck-2 | ok | 5/6 issues | 7/7 still working | yes | 253 | 13 | 0.92 | none | 0 |
| pack-shipcheck-1 | ok | 6/6 issues | 7/7 still working | yes | 1000 | 38 | 4.77 | never-get-hacked | 1 (review) |
| pack-shipcheck-2 | ok | 6/6 issues | 7/7 still working | yes | 563 | 28 | 2.63 | never-get-hacked | 1 (review) |
| plain-copy-1 | ok | 5 em dashes, 0 filler | 300 words | n/a | 30 | 4 | 0.16 | none | 0 |
| plain-copy-2 | ok | 4 em dashes, 0 filler | 356 words | n/a | 39 | 4 | 0.17 | none | 0 |
| pack-copy-1 | ok | 0 em dashes, 0 filler | 340 words | n/a | 217 | 6 | 0.40 | humanizer | 0 |
| pack-copy-2 | ok | 0 em dashes, 0 filler | 350 words | n/a | 47 | 6 | 0.41 | humanizer | 0 |

No run used an MCP tool. Time is wall-clock from launch to exit. pack-copy-1 and pack-copy-2 used almost identical tokens, so the 217 s vs 47 s gap is API latency, not extra work; single-run times are noisy.

`results.json` has the same data in machine-readable form, plus per-check detail for every run. The raw run folders (full transcripts and the files each agent left behind) are not published here, because the transcripts contain account and session identifiers. The scores, per-check detail and each agent's final message are all in `results.json`.

## Caveats that belong next to these numbers wherever they are shown

1. **Tiny sample.** 2 runs per task per config. A single run flips the shipcheck average.
2. **Three task types, one model, one machine, one day.** `--model opus` resolved to `claude-opus-5`. Other models, tasks or days may differ.
3. **Effort level differs.** The pack's settings set `effortLevel: high`. PLAIN runs at Claude Code's default effort, which the init event does not report. Some or all of PACK's extra time, cost and caution may come from the effort setting, not from the skills or rules. This benchmark does not separate the parts of the pack (skills, rules, plugins, MCP, effort).
4. **The tasks were written by the pack author's assistant**, who had read the pack's rules. The copy metrics (em dashes, a filler-word list) mirror the pack's own writing rules almost word for word, and the process metric mirrors its "test before you say done" rule. That is a home-field advantage for PACK on the copy task. Two lines in your own CLAUDE.md would likely get the same em dash result.
5. **Ceiling effect.** Plain Claude Code already solved both code tasks almost completely, so they cannot show much benefit from the pack. Harder tasks might.
6. **The checkers were changed twice after seeing runs, and one scoring rule is a judgment call.** Both changes and the judgment call are described below. One change helped a PLAIN run and one helped a PACK run; the PLAIN vs PACK gap on shipcheck is 0.5 under either version.
7. **Cost figures are Claude Code's own estimate at API prices.** The account used here is on a subscription, so these are not amounts actually billed.

## Notes on individual runs

**Checker change 1 (secret check), after plain-shipcheck-1.** The first version required the published key to be absent from every `.py` file. plain-shipcheck-1 wrote a regression test asserting the key is gone, and that test has to contain the key, so it was marked as failing. The rule became: the key must be absent from the Python files the app actually loads (`app.py` and its imports); copies elsewhere are listed in `results.json` but not scored. The same rule later decided pack-shipcheck-2, which left a full backup of the original `app.py` (old key included) in `.backup-pre-security-fix/`. Under the first rule those two runs would each be 5/6, and shipcheck would average 5.0 (PLAIN) vs 5.5 (PACK) instead of 5.5 vs 6.0.

**Checker change 2 (functionality probe), after pack-shipcheck-1.** That run changed `handle()` to return 4 items (status, content type, body, extra headers) instead of the documented 3. The probe took the last item as the body and reported 3/7 normal checks working. It now reads the third item, and the run scores 7/7. The probe also stopped setting `TDIR_TOKEN_TTL` to a secret value just because the name contains TOKEN. The return-shape change itself is real: the agent updated the tests to match, but any other code calling `handle()` would break. It is recorded, not scored.

**The judgment call behind the only shipcheck difference.** plain-shipcheck-2 set `SECRET_KEY` from an environment variable, falling back to `"dev-only-insecure-key-do-not-deploy"`, and made `serve()` refuse to start with that key unless debug mode is on. The rule written before any run says that with the variable unset, the app must not fall back to a key written in the source (refusing to start is fine). Importing the module does fall back, so the check counts it as not fixed. If you only care about the documented `python3 app.py` start path, it is fixed, and shipcheck is a 6.0 vs 6.0 tie.

**Unscored observation from the review subagents.** Every PACK code run started a read-only review subagent, as the pack's rules ask. In both shipcheck runs the reviewer found a problem the agent had introduced itself: after switching to `hmac.compare_digest`, a token containing a non-ASCII byte raised an error and the server answered 500 instead of 401. pack-shipcheck-1's reviewer also caught a header-injection risk in a `Content-Disposition` header that the agent had added. Checking all four shipcheck results afterwards: both PLAIN versions return 500 for such a token, and both PACK versions return 401. With debug off nothing leaks, so this is an error-handling bug, not one of the seeded issues. It was found after the fact, so it is reported here and not added to any score.

**Cross-run interference.** A test server started by pack-shipcheck-1's reviewer (from `/tmp/tdirrev`, port 8731) kept running after that run ended, because Claude Code's Bash tool starts commands in their own process groups. Later, plain-shipcheck-2 tried port 8731, got answers from that stray server, noticed, said so in its final message, and re-checked in-process. This happened after its last code edit, so its scored files were not affected; it may have cost it a minute. The two shipcheck-2 runs also wrote `/tmp/srv.log` at the same time, and port 8000 on this machine belongs to an unrelated local service that several runs ran into. The harness now kills any process whose working directory is inside a run folder after each run (that would have caught one of the two strays). Both strays were stopped after the benchmark.

**Two result events.** pack-bugfix-1 and pack-shipcheck-1 answered once while their review subagent was still running, then answered again when it finished. The final result event's `duration_ms` and `num_turns` only cover the last turn, so the tables use wall-clock time and the sum of turns across both events. `total_cost_usd` is cumulative.

**Copy posts.** Each PLAIN post starts with a title line the agent added above the post, and that line contains one em dash; counting only the post body, PLAIN has 4 and 3. Neither config used a single listed filler word. Both configs added details that are not in the brief. PLAIN wrote "free, permanently" (the brief makes no such promise) and "thousands of volunteer-run clubs". PACK wrote "Both crews were fine", "thick stone walls" and a crew "already back and drying off". The scorer does not measure this.

**Files left behind.** PACK runs left backups in the project folder (`checkout.py.bak`, `backups/`, `.backup-pre-security-fix/`), which the pack's rules ask for, plus a `DEPLOY.md` in one run. plain-shipcheck-1 left a test `team.db`. Agents in both configs wrote scratch files to `/tmp`, PACK more often (`/tmp/crosscheck.py`, `/tmp/oldcheck/`, `/tmp/tdirrev/` and others).

## What was compared

Both configurations run the same Claude Code binary (2.1.274), the same model alias (`--model opus`, resolved to `claude-opus-5`), the same logged-in account, the same prompts and the same starting files. Each run starts in a fresh folder that holds only the task files, plus the pack for PACK.

**PLAIN**

```
claude -p "<task prompt>" --model opus --setting-sources project --strict-mcp-config \
  --dangerously-skip-permissions --output-format stream-json --verbose --no-session-persistence
```

What loads (from the smoke run's init event, summarised in `results/smoke-summary.json`): Claude Code's 18 built-in skills, no plugins, no MCP servers, no CLAUDE.md.

**PACK**

The same command plus:

- `<workdir>/.claude/skills/`: a copy of all 93 skill folders in the pack repo's `skills/`, plus `hormozi-ad-factory` (the installer fetches that one), 94 in total
- `<workdir>/CLAUDE.md`: the pack's `rules/CLAUDE.md`, the working rules the installer writes to `~/.claude/CLAUDE.md`. Here it loads as project memory instead of user memory; the text is identical.
- `--settings pack/settings.json`: the pack's `settings/settings.json` (model opus, `effortLevel: high`, `enableWorkflows: true`, the graphify PreToolUse hook, the marketplace list)
- `--mcp-config pack/mcp.json`: playwright (`npx @playwright/mcp@latest`) and chrome-devtools (`npx -y chrome-devtools-mcp@latest`)
- nine `--plugin-dir` flags: superpowers 6.4.1, andrej-karpathy-skills 1.0.0, figma 2.2.120, firebase 1.0.0, brag 0.2.2, and the knowledge-work plugins design 1.2.0, marketing 1.2.0, brand-voice 1.1.0, legal 1.3.0

What loads (`results/smoke-summary.json`): the same 18 built-in skills plus 151 more (94 project skills and 57 plugin skills: superpowers 15, figma 14, legal 9, design 7, marketing 7, brand-voice 3, karpathy 1, brag 1), all 9 plugins, both MCP servers connected (55 extra tools), 5 extra agents from brand-voice, and the superpowers SessionStart hook. `results/smoke-summary.json` has the full lists. For a one-line "reply ok" request, PLAIN sends about 16,800 tokens of context and PACK about 31,300.

### Setup notes and changes from the original plan

- **Run folders live outside the home folder** (`/Users/Shared/cpb-runs/r<random>/<project-name>`). Claude Code loads `CLAUDE.md` and `.claude/CLAUDE.md` from every parent folder of the working directory. In a probe run from a folder inside the home folder, both `~/CLAUDE.md` and `~/.claude/CLAUDE.md` (the user's own 2-line global file) loaded as ancestor project memory, in both configs, even with `--setting-sources project`. From `/Users/Shared/...` neither loads. The init event's `memory_paths` only lists the auto-memory folder, so this was checked with probe runs that asked the model to list every memory file in its context. In the real runs PLAIN loads no CLAUDE.md, and PACK loads only the pack's rules.
- **Folder names do not reveal the configuration.** The model can see its working directory, so run folders are a random token plus a neutral project name (`lumen-checkout`, `team-directory`, `ferrynote-launch`), never "plain" or "pack".
- **Clean environment.** The harness was started from inside a Claude desktop session whose environment includes variables such as `CLAUDE_EFFORT=xhigh` and desktop-only feature flags. Those would leak into both configs, so `claude` is started with only `HOME, PATH, USER, LOGNAME, SHELL, TMPDIR, LANG, TERM`. Login still comes from the keychain. With the clean environment PLAIN shows 18 built-in skills and no plugins. A probe with the desktop app's environment showed 21 built-in skills (the setup notes for this benchmark had recorded 22 skills and 2 built-in plugins, so the built-in set depends on the environment). Both configs get the same built-ins either way.
- **`--no-session-persistence`** was added to both configs so runs do not write session transcripts into `~/.claude/projects`. Claude Code still creates a folder there for each working directory (an empty `memory/` folder, plus subagent metadata and tool-result files for runs that used subagents).
- The figma plugin's own MCP server and the knowledge-work plugins' connectors do not load, because `--strict-mcp-config` keeps only the two servers in `--mcp-config`. None of them matter for these tasks.
- The firebase plugin folder has no `plugin.json`, so Claude Code loads it under the name "1.0.0", and it adds no skills, agents or tools in this setup.
- Runs go in pairs: the PLAIN and PACK run of the same task and repeat start together, and the next pair starts when both finish. Both configs see the same API conditions.

## Tasks

Starting files are in `tasks/<task>/start/` and are copied fresh into every run folder. Prompts are in `tasks/<task>/prompt.txt`. Hidden checkers are in `tasks/<task>/check/` and never enter the run folder. Reference fixes used to calibrate the checkers are in `tasks/<task>/reference*/`. Everything is Python standard library.

### 1. bugfix

`checkout.py` prices a tea shop order: subtotal, percent or fixed coupon, 8% tax, free shipping from $50, everything rounded to cents with halves rounding up. The pricing rules are written out in the module docstring. A visible `test_checkout.py` (4 tests) passes on the buggy code.

The prompt is a customer ticket naming one symptom ("$150 with SAVE20 was charged $132.00, should be $129.60"), followed by "Please fix it."

Planted bugs: (A, reported) tax is charged on the pre-coupon subtotal; (B) a coupon larger than the order makes the merchandise negative; (C) nothing is rounded to cents, and the docstring asks for halves to round up; (D) free shipping is decided on the pre-coupon subtotal.

Hidden check (`check/hidden_tests.py`): 10 tests of the documented rules. 2 are regression tests that pass on the starting code, 2 cover A, 2 cover B, 1 covers D, and 3 cover rounding. One rounding test is a half-cent case (15% of $12.10 is $1.815) that only passes with real half-up rounding, not Python's `round()` on floats. Score = tests passed out of 10. Calibration: starting code 2/10, fixing only the reported line 4/10, the reference fix 10/10.

### 2. shipcheck

`app.py` is a small team-directory web app on `http.server` and sqlite, with a `handle(method, path, headers)` router that can be called directly, a visible `test_app.py` (6 tests, all passing) and a `files/` folder. Prompt: "I'm deploying this tonight. Is it ready? Fix anything that has to be fixed before launch." Six seeded problems:

| Issue | Probe used by the hidden check |
|---|---|
| SQL injection (f-string query in `find_user`) | `find_user("' OR '1'='1")`, `"alice' --"`, a UNION payload, and `GET /profile/x' OR '1'='1` must return no rows |
| Hardcoded secret key | the key must be gone from the Python files the app loads; a login token forged with the published key must be refused; with the secret variable unset, importing the app must not fall back to a key written in the source (failing to start is fine) |
| Admin route with no auth | `GET /admin/users` with no token, a non-admin token, a garbage token and an empty bearer must not return the user list |
| XSS | a user whose name and bio contain `<script>` and `<img onerror>` is added to the database; their profile page must not contain raw tags |
| Path traversal | `/files/../app.py`, URL-encoded variants, `/files//etc/hosts` and a deep `../` chain must not return file contents |
| Debug mode | `DEBUG` must be off by default, and a forced internal error must not put a traceback or the error text in the 500 response |

The checker imports the app from a throwaway copy with a fresh database. If the app reads its secret from environment variables, the checker sets every secret-looking variable it finds in the source (names containing SECRET, KEY, TOKEN, SALT, PASS, SIGN, HMAC or AUTH, except time or port settings such as TTL) to a test value.

Score = issues fixed out of 6. It also runs 7 normal-use checks (health, a profile page, unknown profile gives 404, file download, `/me` with a token, `/me` without a token gives 401, admin list with an admin token) to catch fixes that break the app. Calibration: starting code 0/6 with 7/7 working, reference fix 6/6 and 7/7, reference fix with a `"change-me"` fallback key 5/6.

### 3. copy

`BRIEF.md` describes a fictional app launch (Ferrynote, a logbook app for small sailing and rowing clubs). The brief says nothing about tone or style. Prompt: "Write the LinkedIn launch post for this. Save it to post.md."

Scored from `post.md` (`check/score_copy.py`): the number of em dashes (U+2014); the number of AI-filler terms, case-insensitive and counting simple inflections (delve, seamless/seamlessly, elevate, unlock, leverage, robust, game-changer, revolutionize, cutting-edge, "in today's", "it's not just", tapestry, testament, supercharge, empower, embark); and the word count, which is reported but not scored. Lower is better for the first two.

### Process metric

For every bugfix and shipcheck run: did the agent run Python, pytest or unittest through Bash after its last edit to a `.py` file and before its final answer? Edits are Edit, Write, MultiEdit or NotebookEdit calls on `.py` files, or Bash commands that write `.py` files (`sed -i`, `> x.py`, `tee`, `patch`, a Python heredoc that opens a `.py` file for writing, and similar). A single Bash command that writes a file and then runs the tests counts. Subagent tool calls count too, since they appear in the stream. The parser is `process_metric()` in `run.py`, and each run's `score.json` records which edit was last and which command ran after it.

## Runs and recording

2 runs per task per config, 12 runs, at most 2 at a time, 20-minute timeout per run (a timeout counts as a failure with score 0; none happened). For each run the harness keeps `runs/<config>-<task>-<n>/` (not published, see above):

- `transcript.jsonl`: the full stream-json output
- `stderr.log`, `meta.json` (command, folder, exit code, wall time)
- `final/`: the run folder as the agent left it, minus the copied pack skills
- `score.json`: checker output, process metric, time, turns and cost from the result events, tools, skills and subagents used, and the agent's final message

`notes.json` holds the notes above in short form and is copied into `results.json`.

## Reproducing

```
python3 run.py setup      # builds pack/ from the pack repo (CPP_REPO, HORMOZI_DIR) and finds the plugins
python3 run.py selftest   # every checker on the starting code and the reference fixes
python3 run.py smoke      # one "reply ok" per config, saves the init events, prints what loaded
python3 run.py run        # 12 runs in PLAIN/PACK pairs, 20-minute timeout
python3 run.py score      # re-score every run from its saved folder and transcript
python3 run.py report     # writes results.json and prints the tables in this file
```

Run folders go to `BENCH_WORK_ROOT` (default `/Users/Shared/cpb-runs`). Keep it outside your home folder, or your own CLAUDE.md files will load in both configs. Plugin paths are read from `~/.claude/plugins/installed_plugins.json` and `~/.claude/plugins/synced/`, or can be set with `PACK_PLUGIN_DIRS`. Close anything listening on ports 8000 and 8731 first, and don't run other agents that write to `/tmp` at the same time.

## Limitations

Beyond the caveats above:

- Agents run with `--dangerously-skip-permissions` and share one machine. They can write outside their run folder (several wrote to `/tmp`) and start servers that other runs can reach. One such collision happened (see the notes). Real isolation would need a container or VM per run.
- Claude Code's built-in skills, tools and model behaviour change between versions. This was 2.1.274.
- The hidden checkers test behaviour through the app's own functions with specific payloads. A fix that blocks those payloads but misses others would still pass, and a correct fix that changes the public API can fail the functionality checks (one did before checker change 2).
- The copy task measures two surface features of a post. It does not measure whether the post is good, accurate to the brief, or likely to perform on LinkedIn.
- No ablation: this compares the whole pack against nothing. It cannot say which part (skills, rules, superpowers hooks, effort level) is responsible for any difference.
