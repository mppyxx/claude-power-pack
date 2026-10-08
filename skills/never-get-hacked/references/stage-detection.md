# Stage detection: classify a project without interrogating the user

Load this file first, every time the skill runs, before asking the user a single question. The user's self-report of "where the project is" is systematically optimistic — not because they lie, but because AI-generated code contains decisions they never saw made, and because "it works" and "it is done" feel identical from the outside. The filesystem and git history do not have that problem. This file gives you a read-only evidence block, a deterministic first-match-wins mapping from that evidence to a stage on the 0–10 map, an architecture-lane decision rule that changes what advice is even *possible*, and a routing table telling you which reference file to open next.

---

## 0. What you must produce before anything else

Three answers, in this order. Do not start auditing until you have all three.

| Output | Values | Why it must come first |
|---|---|---|
| **Lowest unresolved stage** | 0–10, or E | Sets what to fix. A live app with no lockfile is a Stage 2 problem wearing a Stage 6 costume. |
| **Furthest stage reached** | 0–10 | Sets urgency. Unresolved 3 + reached 6 means the open database is already on the internet. |
| **Architecture lane** | A, B, or C | Sets which defenses are expressible at all. Getting this wrong is the worst failure mode of the skill. |

The two stage numbers are usually different, and the gap between them *is* the finding. Report both. "Stage 6 reached without passing Gate 5" is a sentence the dossier uses directly, and it is more useful to the user than a single number.

**Stage E overrides everything.** It is reachable from any stage and runs out of band. If there is any indication of a live incident — unexplained charges, unfamiliar rows or users, an abuse report, a bug-bounty style email, a key the user thinks leaked — stop stage detection and go to `references/incident-and-challenge.md` Part 1. The Stage E order is **preserve → contain → patch → rotate → scope → notify**, and it is the reverse of what people do. Running an audit that writes files, runs a build, or triggers a deploy destroys the log evidence that drives the notification decision.

---

## 1. The stage map

| Stage | Name | The question it answers | Exit gate proves |
|---|---|---|---|
| 0 | Data decisions | What will I hold, and what will I refuse to hold? | Three files exist; no field lacks a reader |
| 1 | Accounts | Who can reach my accounts and my agent's credentials? | Hardware keys on five root accounts; agent scoped |
| 2 | Scaffold | Is the toolchain itself trustworthy? | No secret in git or bundle; lockfile committed; scripts off |
| 3 | Data model | Where does authorization live? | External curl with the public key returns nothing |
| 4 | Features | Does each new endpoint re-check? | Differential two-account test passes on every route |
| 5 | Pre-deploy | Is the build artifact clean? | Grep of built output; headers; debug routes gone |
| 6 | First URL | Is anything public that shouldn't be? | Previews gated; `.env`/`.git`/`_src` 404; DNS clean |
| 7 | Real users | Can one user hurt another, or hurt my bill? | Rate limits with shared state; spend caps with actions |
| 8 | Money | Can someone get the product for free? | Webhook signature verified; entitlement only from webhook |
| 9 | Steady state | Is the clock installed? | Dependabot + cooldown + weekly slot + tested restore |
| 10 | Decommission | Did I leave a claimable attack surface? | DNS first, then resources, then revoke; nothing resolves |
| E | Incident | (out of band) | Evidence preserved before anything is touched |

Stages 0–2 are cheap and are the ones skipped. **Stage 3 is where almost every published breach in the dossier actually lives** — CVE-2025-48757 (Lovable, CVSS 9.3, missing RLS), the Tea app's open Firebase Storage bucket (~72,000 images including ~13,000 ID selfies), Moltbook (~4.75M records via a publishable key against tables with RLS off), Base44's unauthenticated `register`/`verify-otp` endpoints keyed only on a public `app_id`. None of those needed an exploit. They needed a `curl`. If your detection is ambiguous between two stages and one of them is 3, check Stage 3 first.

---

## 2. The detection block

Every command here is read-only: no writes, no network, no build, no deploy. Run the whole block from the project root and keep the raw output — you will cite it in the report. Missing files produce no output rather than an error, which is why every `ls` carries `2>/dev/null`.

### 2.1 Architecture signals

```bash
# Does a server tier exist at all? This single line decides Lane A/B.
ls -d app/api api/ server/ functions/ supabase/functions netlify/functions 2>/dev/null

# Server-side execution markers
grep -rl "'use server'" app/ src/ 2>/dev/null | head
grep -rl "createClient(" src/ app/ 2>/dev/null | head

# Which framework, if any
ls next.config.* nuxt.config.* vite.config.* svelte.config.* remix.config.* 2>/dev/null

# Lane C markers
ls requirements.txt pyproject.toml manage.py 2>/dev/null
```

### 2.2 Deployment-state signals

```bash
# Is this configured to be deployed, and where
ls vercel.json netlify.toml fly.toml render.yaml Dockerfile docker-compose.y*ml 2>/dev/null

# Is it alive, and how recently
git log --oneline -1 2>/dev/null
git log -1 --format=%cd -- package-lock.json pnpm-lock.yaml yarn.lock
```

### 2.3 Maturity signals

```bash
# Is the maintenance clock installed
ls .github/workflows .github/dependabot.yml renovate.json 2>/dev/null

# Did Stage 0 ever happen
ls security/data-map.yaml security/processors.yaml docs/breach-runbook.md 2>/dev/null

# Where does authorization live
ls supabase/migrations firestore.rules 2>/dev/null

# Is money involved
grep -rl "stripe" package.json 2>/dev/null
```

Two follow-ups that cost nothing and repeatedly change the answer:

```bash
# Is the authorization boundary actually written, or just present as a file?
grep -rniE "enable row level security|create policy" supabase/migrations/ 2>/dev/null | head
grep -rnE "if true|request\.auth != null" firestore.rules storage.rules 2>/dev/null

# Does a webhook handler verify anything?
grep -rn "constructEvent" . --include=*.ts --include=*.js --include=*.py 2>/dev/null | head
```

A `supabase/migrations/` directory with zero `enable row level security` lines is not "Stage 3 in progress." It is Stage 3 unresolved, and it is the highest-prevalence critical finding in the entire catalog (`BAAS-01`).

---

## 3. The architecture lane decision rule

**Run this before the stage mapping.** The lane is not a detail of the stage — it decides which findings are even reportable, and a lane error produces advice that reads as competent and cannot be executed.

```bash
# Step 1 — is there any server tier?
SERVER=$(ls -d app/api api/ server/ functions/ supabase/functions netlify/functions 2>/dev/null | wc -l)

# Step 2 — does the client talk to a BaaS directly?
grep -rnE "supabase\.from\(|from\(['\"]" src/ app/ 2>/dev/null | head
grep -rnE "firebase/firestore|firebase/storage|getFirestore\(|collection\(" src/ app/ 2>/dev/null | head

# Step 3 — Python?
ls requirements.txt pyproject.toml manage.py 2>/dev/null
```

| Lane | Detection | Shape |
|---|---|---|
| **A** | A framework config exists **and** `app/api`, `server/`, `supabase/functions` or `netlify/functions` is non-empty, or `'use server'` appears | Server-rendered framework + managed auth + Postgres, data access in a server-only DAL |
| **B** | `src/` contains `supabase.from(...)` or Firebase Web SDK calls **AND** there is no `app/api`, no `supabase/functions`, no `netlify/functions` | Client-only SPA + BaaS |
| **C** | `requirements.txt`, `pyproject.toml` or `manage.py` present | Python backend (FastAPI / Django / Flask) |

A project can be A **and** C (a Next.js frontend with a FastAPI service). Report both and audit both. A project cannot be A and B — the presence of a server module is exactly what ends Lane B.

### Why Lane B is different in kind, not degree

In a pure client + BaaS repo, the dossier's flagship server-side defenses are **not merely missing — they are inexpressible**. There is no server module in which to write them. Say this to the user plainly instead of emitting DAL advice into a file that will never execute.

| Standard defense | Status in pure client + BaaS | What actually substitutes |
|---|---|---|
| Server-only DAL with `import 'server-only'` | **Impossible** — no server module | RLS policies / Security Rules *are* the DAL, written in SQL/CEL |
| Zod `safeParse` at the trust boundary | **Cosmetic** — runs in the attacker's browser | `CHECK` constraints, `DOMAIN` types, `NOT NULL`, RLS `WITH CHECK`, `request.resource.data` predicates |
| Authorization re-checked in a route handler | **Impossible** | RLS `USING` + `WITH CHECK` on `(select auth.uid())` |
| `@upstash/ratelimit` on Redis | **Impossible** for data traffic | Platform auth limits, Firebase App Check, a DB counter table + `BEFORE INSERT` trigger, spend caps |
| Third-party API key kept out of the bundle | **Impossible** — anything the client sends, it possesses | One Edge Function / Worker. You are no longer client-only |
| Stripe webhook signature verification | **Impossible** — a browser cannot receive an inbound POST | A function. No workaround |
| Turnstile / hCaptcha `siteverify` | **Impossible** for your own writes | Supabase Auth CAPTCHA (auth endpoints only) |
| CSRF tokens, `httpOnly` session cookies | **Impossible** — no server to set them | N/A; BaaS uses bearer tokens, so the risk shifts to XSS/token theft |

Telling a Lane B user to "add authorization to your API route" or "validate the input with Zod on the server" is the failure this rule exists to prevent.

### The seven triggers that end Lane B

Any single YES means client-only cannot be made *correct*, only less wrong. Check these during detection, because they change the plan from "harden" to "add exactly one function":

```bash
# 1. A bearer-secret third-party API called from the client
grep -rniE "api\.(openai|anthropic|resend|stripe|twilio|sendgrid)\.com" src/ 2>/dev/null
# 2. Something outside the app must POST in (webhooks)
grep -rn "whsec_" . 2>/dev/null | head
# 3. A value on the user's own row the user must not set
grep -rniE "\b(credits|plan|role|is_pro|is_admin|verified|subscription_status)\b" src/ supabase/ 2>/dev/null | head
```

4. Any per-invocation cost (model tokens, email, SMS, image generation, egress).
5. A rule depending on state the caller may not read.
6. Email beyond Supabase auth — the built-in provider is **2 emails/hour project-wide**.
7. Per-user rate limits on data writes, not just on auth.

Triggers 1, 2 and 4 are the ones this population hits within a week of launching.

### Two Lane B mechanics to carry into every finding

**PostgREST hands the attacker a query builder, not just parameters.** `select=`, `eq/gt/like/in/is/fts`, `order=`, and resource embedding (`?select=*,actors(*)`) that pivots through foreign keys into tables whose RLS you forgot. An unfiltered GET returns the full contents of a table. Firestore made the opposite choice — "Rules are not filters—queries are all or nothing" — so the two mental models are **not transferable**, and advice written for one is wrong for the other. Detect which one you are in before writing a single fix.

**RLS scopes rows, not columns, and a policy cannot compare OLD to NEW.** A perfectly correct owner-scoped UPDATE policy still permits `PATCH {"role":"admin","credits":999999}` against the user's own row (`AUTHZ-05`). `WITH CHECK` sees only the new row; there is no `OLD` in a policy, so column immutability is inexpressible in RLS. Firestore's equivalent control is `request.resource.data.role == resource.data.role`.

---

## 4. Signal → stage mapping (first match wins)

Walk these in order and stop at the first match. That match is the **lowest unresolved stage**. Then keep walking to the end without stopping, and the last rule that also matches gives you the **furthest stage reached**. Two passes over one list, one deterministic answer.

| # | Signal | Verdict |
|---|---|---|
| R1 | No repo, or a repo with only README/config | **Stage 0.** And independently: if `security/data-map.yaml` is absent, Stage 0 is *not* complete regardless of how much code exists |
| R2 | `package.json` exists, no lockfile committed, **or** lockfile mtime equals the first commit date | **Stage 2 unresolved.** A lockfile whose mtime equals launch day is the single clearest sign the maintenance clock was never installed |
| R3 | `supabase/migrations/*.sql` or `firestore.rules` exists but contains no `enable row level security`, or only `allow read, write: if true` / `if request.auth != null` | **Stage 3 unresolved**, regardless of how polished the UI is. This is `BAAS-01` / `BAAS-03` |
| R4 | `src/` contains `supabase.from(...)` or Firebase Web SDK calls **and** there is no `app/api`, no `supabase/functions`, no `netlify/functions` | **Lane B** (§3). Not a stage — a lane. Apply it on top of whatever stage matched |
| R5 | `vercel.json`, `netlify.toml`, or a live deployment URL in the README, **plus** no `.github/workflows/` | **Stage 6 reached without passing Gate 5** |
| R6 | `stripe` in `package.json` but no route matching `webhook`, or a webhook route with no `constructEvent` | **Stage 8 entered without Gate 8** (`PAY-01`) |
| R7 | `.github/dependabot.yml` absent **and** last lockfile change > 60 days ago on a repo with a live URL | **Stage 9 not started.** This is the modal state of a shipped vibe-coded repo |
| R8 | No commits in > 6 months, live DNS still resolving | **Stage 10 candidate** — treat as an abandoned live attack surface, not as "done" |

Rules R5–R8 are the ones that give you *furthest reached*; R1–R3 are the ones that give you *lowest unresolved*. R4 is orthogonal to both.

### Three cases the file listing alone cannot resolve

**No local repo at all, but a live app.** The project may be platform-hosted (Lovable, Bolt, Base44, Replit, Bubble, Wix, Glide). Ask for the URL only — never for a description of the architecture — and go to `references/migration-paths.md` §3. Understand what the user actually has: Lovable's GitHub integration is a **live two-way sync**, not an export ("Changes made in Lovable sync to GitHub" and "Changes pushed to the active GitHub branch sync back into Lovable"), so hardening the repo does not detach the platform agent from the branch you just audited. Bolt's Supabase integration is Vite-only ("Next.js projects are not supported at this time"), so the export is a client-side SPA — Lane B by construction.

**A repo that looks complete but has never been deployed.** Check `git log --oneline -1` against the deployment config. Config without a deploy is Stage 5, not Stage 6. This is the best possible moment to reach the user and the one time the whole gate list is cheap.

**Detection contradicts the user's story.** Believe the files. Record the contradiction as a finding in its own right — a user who thinks they are at Stage 9 and is actually at Stage 3 has been shipping features on top of an open database, which is a different conversation than "add Dependabot."

---

## 5. Signals the user is lying to themselves about the stage

These are not vulnerabilities on their own. They are evidence that a security artifact was produced by an AI and never read by anyone — which recalibrates how much weight to put on every other reassuring file in the repo. Surface them explicitly; they are usually the fastest way to get a user to stop arguing about the stage.

```bash
# A SECURITY.md written against a superseded OWASP edition
grep -rn "A0[0-9]:2021" . 2>/dev/null
grep -rn "OWASP Top 10 (2021)" SECURITY.md 2>/dev/null

# .env.example carrying real-looking values rather than placeholders
grep -nE "=(sk-|sk_live_|sb_secret_|eyJ|postgres://|AKIA|re_|SG\.)" .env.example .env.sample 2>/dev/null

# Vercel config that publishes what it should gate
grep -n '"public"[[:space:]]*:[[:space:]]*true' vercel.json 2>/dev/null

# Python entrypoint running the dev server, or the debug console
grep -rn "debug=True\|app.run(" --include=*.py . 2>/dev/null

# Django mass assignment
grep -rn 'fields[[:space:]]*=[[:space:]]*"__all__"' --include=*.py . 2>/dev/null
```

| Signal | What it actually tells you | The finding it points at |
|---|---|---|
| `A0x:2021` references, or `SECURITY.md` citing "OWASP Top 10 (2021)" | The security documentation is generated boilerplate nobody maintains. A05:2021 is not A05:2025 — the mapping the doc implies is wrong | None directly; downgrade trust in every self-reported control |
| `.env.example` with real-looking values | The example file was created by copying the real one. Assume the real one is or was in git | `SECRET-01`, `SECRET-05` — check git history, not just the working tree |
| `"public": true` in `vercel.json` | Something was made public to stop an error, and nobody scoped it afterwards | `INFRA-01` — enumerate what is now served |
| `debug=True` / `app.run(` in a Python entrypoint | Flask/Werkzeug debug console in production is **unauthenticated RCE**, and the PIN is derivable offline from `uuid.getnode()` and `/etc/machine-id` given any file-read or SSRF primitive. Flask's own docs call it "a major security risk" | `PY-01`, critical |
| `fields = "__all__"` in a Django form | Mass assignment that Django's own docs say "has led to serious exploits on major websites (e.g. GitHub)". It is a time bomb: correct when written, vulnerable the day someone adds `is_premium` | `PY-05`, `AUTHZ-05` |

Two further tells worth checking whenever the repo claims to be tested:

```bash
# Does the test suite ever authenticate as a SECOND user?
grep -rlniE "user(B|2)|otherUser|secondUser|tenantB|otherOrg" test/ tests/ e2e/ __tests__/ 2>/dev/null \
  || echo "NO DIFFERENTIAL AUTHZ TESTS EXIST — this is the finding"
```

Every generated test is A-reads-A's-own-data, which passes in a completely broken app. A green suite is not evidence of Stage 4. Static analysis structurally cannot find broken access control, and OWASP A01:2025 reports **100% of applications in the contributed dataset had some form of broken access control**, across 1,839,701 occurrences.

---

## 6. The coding-agent footprint

The agent that built the app is its own risk surface, and it is invisible to every scanner aimed at the app. Two greps classify it.

```bash
# 1. Which agent config exists
ls .claude/ .cursor/ .mcp.json AGENTS.md CLAUDE.md 2>/dev/null

# 2. Is any of it ignored?
git check-ignore -v .claude/settings.local.json .mcp.json .env 2>/dev/null || echo "NOT IGNORED"
```

The second command exits 0 if *any* argument is ignored, so on a mixed repo it can look clean. When the first pass is ambiguous, resolve per file:

```bash
for f in .claude/settings.local.json .claude/settings.json .mcp.json .env .cursor/mcp.json; do
  [ -e "$f" ] || continue
  git check-ignore -q "$f" && echo "ignored:     $f" || echo "NOT IGNORED: $f"
done
```

### Why `.claude/settings.local.json` is the dangerous one

`.claude/settings.local.json` stores previously-approved shell commands **verbatim**. A token typed inline in a `curl` during a debugging session persists there — in a file no default ignore list covers, in a repo that may be public, long after the terminal scrollback is gone. It is not a secrets file, so nothing treats it as one; it is a *history* file that happens to contain whatever you typed.

GitGuardian measured a **3.2% secret-leak rate on Claude Code-assisted commits against a 1.5% baseline**, and found **24,008 unique secrets in MCP config files (2,117 valid)**. Source: <https://blog.gitguardian.com/the-state-of-secrets-sprawl-2026/>

Then check the contents, not just the ignore status:

```bash
grep -rnE 'sk-|sk_live_|sb_secret_|postgres://|eyJ[A-Za-z0-9_-]{20,}' \
  .claude/ .cursor/ .mcp.json CLAUDE.md AGENTS.md .cursorrules 2>/dev/null

jq '.mcpServers' ~/.claude.json .mcp.json 2>/dev/null

# npx -y fetches and executes the LATEST published version on every launch
grep -rn '"-y"\|@latest' .mcp.json ~/.claude.json 2>/dev/null

# Supabase MCP: every hit must carry BOTH read_only=true AND a DEV project_ref
grep -rn "mcp.supabase.com" ~/.claude.json ~/.cursor/mcp.json .mcp.json 2>/dev/null
```

Three facts that make these greps worth running at detection time rather than later:

- **`.mcp.json` is documented as a file to commit to version control**, and MCP server env vars and `Authorization` headers live in it and in `~/.claude.json`. Microsoft's ChainDrop worm specifically targeted `.claude/settings.json` and `.vscode` paths and grabbed the GitHub CLI token plus all process environment variables.
- **`npx -y <server>` fetches and executes the latest published version on every launch.** That is how the `postmark-mcp` backdoor arrived automatically: versions 1.0.0–1.0.15 were clean at ~1,500 weekly downloads, then v1.0.16 on 2025-09-17 added one line BCC'ing every outgoing email to `phan@giftshop[.]club`. No CVE was assigned because it is a behavioral backdoor, not a code flaw — **no CVE-based scanner could ever have caught it.**
- **A `CLAUDE.md` rule forbidding secret display is read by the model, not enforced by the tool.** Treat `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/`, `copilot-instructions.md`, `.claude/settings.json` and every `SKILL.md` as executable code. Pillar Security's Rules File Backdoor (disclosed to Cursor 2025-02-26, GitHub 2025-03-12) hid instructions in zero-width joiners and bidi markers that render as nothing to humans *and in PR diffs* but are read by the model.

```bash
# Invisible-Unicode instructions in agent config (needs PCRE: GNU grep -P, or ripgrep)
grep -rlP '[\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{2064}\x{FEFF}\x{E0000}-\x{E007F}]' \
  .claude/ .cursor/ .github/ CLAUDE.md AGENTS.md 2>/dev/null
# macOS BSD grep has no -P; use ripgrep instead:
rg -l '[\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{2064}\x{FEFF}]' .claude .cursor .github CLAUDE.md AGENTS.md 2>/dev/null
```

*True positive:* any file listed. *False positive to expect:* none in a hand-written repo, but emoji-adjacent variation selectors and a UTF-8 BOM on a Windows-authored file will match `\x{FEFF}` — open the hit and look at where the character sits before reporting `AGENT-02`.

**Do not report the mere existence of `.claude/` or `CLAUDE.md` as a finding.** It is how the app was built. The findings are: it is not gitignored, it contains a credential, it contains invisible characters, or an MCP server in it points at a production project.

---

## 7. What to do with the answer

Routing table. Load exactly one primary file next; do not preload the library.

| Detection result | Load next |
|---|---|
| Any incident indicator, any stage | `references/incident-and-challenge.md` Part 1 — **before** anything else, before any command that writes |
| Stage 0 — no repo, or no `security/data-map.yaml` | `references/stack-and-lanes.md`. Stack choice at stage zero deletes whole bug classes; that leverage is gone later |
| Stage 1–2 unresolved (accounts, lockfile, secrets, scripts) | `references/gates.md`, Gate 1→2 and Gate 2→3 |
| Stage 3 unresolved (no RLS, `if true` rules) | `references/gates.md` Gate 3→4 first, then `references/authz-verification.md`. If the app is live, run the external `curl` with the publishable key before writing any plan |
| Stage 4 — features, multi-user, any auth | `references/authz-verification.md`, the differential two-account test |
| Stage 5 — pre-deploy | `references/gates.md` Gate 5→6, plus the bundle grep |
| Stage 6 reached without Gate 5 (R5) | `references/gates.md` — run Gate 5 and Gate 6 retroactively, in that order |
| Stage 7 — real users | `references/gates.md` Gate 7→8; spend caps with an **action**, not an alert |
| Stage 8 (R6 — Stripe, no `constructEvent`) | `references/gates.md` Gate 8→9 |
| Stage 9 not started (R7) | `references/gates.md` Gate 9, the continuous one |
| Stage 10 candidate (R8 — abandoned, DNS live) | `references/migration-paths.md`, then Gate 10: DNS first, then resources, then revoke |
| **Lane B** at any stage | `references/stack-and-lanes.md` — work from the impossible-vs-substitute table, never from generic server advice |
| **Lane C** at any stage | `references/stack-and-lanes.md`; `python manage.py check --deploy` is Django's own automated posture audit and is the cheapest real signal available on this lane |
| No local repo, platform-hosted | `references/migration-paths.md` §3 |
| Static HTML with a key in a `<script>` tag | `references/migration-paths.md` §2 — often the answer is static plus one serverless function, **not** a Next.js rewrite |
| Live with real users and a critical finding | `references/migration-paths.md` §4, and preserve evidence first |
| A public "try to hack this" challenge | `references/incident-and-challenge.md` Part 2, *before* it goes live |

Regardless of route: **`references/false-positives.md` is loaded before you report anything.** The stage number tells you what to check; it never tells you that something is safe.

---

## 8. Worked example

A user says: *"My app's been live for a couple of months, I've got about 40 users, I just added Stripe. Can you check the security? I think I'm in pretty good shape — I had Claude write me a SECURITY.md."*

### The repo

```
mealplanr/
├── .claude/
│   ├── settings.json
│   └── settings.local.json
├── .env
├── .env.example
├── .gitignore
├── CLAUDE.md
├── README.md
├── SECURITY.md
├── index.html
├── package.json
├── package-lock.json
├── vercel.json
├── supabase/
│   └── migrations/
│       ├── 20260114_init.sql
│       └── 20260119_add_credits.sql
└── src/
    ├── main.tsx
    ├── lib/supabase.ts
    ├── pages/Dashboard.tsx
    ├── pages/Billing.tsx
    └── components/PlanCard.tsx
```

No `.github/`, no `tests/`, no `app/`, no `api/`, no `security/`.

### The detection block, run

```
$ ls -d app/api api/ server/ functions/ supabase/functions netlify/functions 2>/dev/null
(no output)

$ grep -rl "'use server'" app/ src/ 2>/dev/null | head
(no output)

$ grep -rl "createClient(" src/ app/ 2>/dev/null | head
src/lib/supabase.ts

$ ls next.config.* nuxt.config.* vite.config.* svelte.config.* remix.config.* 2>/dev/null
vite.config.ts

$ ls requirements.txt pyproject.toml manage.py 2>/dev/null
(no output)

$ ls vercel.json netlify.toml fly.toml render.yaml Dockerfile docker-compose.y*ml 2>/dev/null
vercel.json

$ git log --oneline -1
a3f1c02 add stripe checkout button

$ git log -1 --format=%cd -- package-lock.json pnpm-lock.yaml yarn.lock
Tue Jan 14 09:12:44 2026 +0000

$ ls .github/workflows .github/dependabot.yml renovate.json 2>/dev/null
(no output)

$ ls security/data-map.yaml security/processors.yaml docs/breach-runbook.md 2>/dev/null
(no output)

$ ls supabase/migrations firestore.rules 2>/dev/null
supabase/migrations

$ grep -rl "stripe" package.json 2>/dev/null
package.json

$ grep -rniE "enable row level security|create policy" supabase/migrations/ 2>/dev/null | head
(no output)

$ grep -rn "constructEvent" . --include=*.ts --include=*.js --include=*.py 2>/dev/null | head
(no output)

$ grep -rnE "supabase\.from\(" src/ 2>/dev/null | head
src/pages/Dashboard.tsx:18:  const { data } = await supabase.from('meal_plans').select('*')
src/pages/Billing.tsx:22:  await supabase.from('profiles').update({ credits: newCredits }).eq('id', user.id)

$ grep -rn "A0[0-9]:2021" . 2>/dev/null
./SECURITY.md:14:- A01:2021 Broken Access Control — mitigated via Supabase RLS

$ grep -nE "=(sk-|sk_live_|sb_secret_|eyJ|postgres://|AKIA|re_|SG\.)" .env.example 2>/dev/null
.env.example:2:VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

$ for f in .claude/settings.local.json .mcp.json .env; do [ -e "$f" ] || continue;
    git check-ignore -q "$f" && echo "ignored:     $f" || echo "NOT IGNORED: $f"; done
NOT IGNORED: .claude/settings.local.json
ignored:     .env
```

### The classification

**Lane: B.** `src/` calls `supabase.from(...)`, and `ls -d app/api api/ server/ functions/ supabase/functions netlify/functions` returned nothing. Vite + no server tier. Every server-side defense in the standard playbook is inexpressible here, and two of the seven Lane-B exit triggers are already met: Stripe is present (trigger 2 — something outside the app must POST in) and `Billing.tsx` writes `credits` from the browser (trigger 3 — a value on the user's own row the user must not set).

**Lowest unresolved stage: 3.** Walking the rules first-match-wins:

- R1 no — code exists. *But note separately:* `security/data-map.yaml` is absent, so Stage 0 was never done either. Record it; do not let it displace R3.
- R2 no — `package-lock.json` is committed. Its mtime is the first commit date, which is a Stage 9 signal, not a Stage 2 one; it fires at R7 below.
- **R3 yes** — `supabase/migrations/` exists and contains zero `enable row level security` and zero `create policy`. **Stop. Stage 3 unresolved.** `BAAS-01`.

**Furthest stage reached: 8.** Continuing the walk without stopping: R5 matches (`vercel.json` present, no `.github/workflows/`) → Stage 6 reached without Gate 5. R6 matches (`stripe` in `package.json`, no `constructEvent` anywhere) → Stage 8 entered without Gate 8, which is `PAY-01`. R7 matches (no `dependabot.yml`, lockfile untouched since 2026-01-14 on a live app) → Stage 9 never started. R8 does not match — there is a commit from this week.

**Self-deception signals: two.** `SECURITY.md` cites `A01:2021` *and* claims access control is "mitigated via Supabase RLS" in a repo where no migration enables RLS. That is the single most useful sentence in the audit, because it explains why the user believes they are in good shape. The `.env.example` carries a real-looking `eyJ` value — worth checking git history for the real `.env`, though on inspection this one is the publishable anon key, which is `NOTVULN-01` and must not be reported as a leak.

**Agent footprint: one finding.** `.claude/settings.local.json` exists and is not gitignored, on a repo that is likely public. It stores previously-approved shell commands verbatim, so any token the user pasted into a `curl` during debugging is committed. Check its contents before deciding severity.

### What the agent does next

1. Load `references/false-positives.md`. The `VITE_SUPABASE_ANON_KEY` in the bundle is **not** the finding, and rotating it as remediation would be a tell that the real problem went unfixed.
2. Because the app is live and Stage 3 is unresolved, run the single highest-value command in the skill before writing any plan — an external read against the data API using only the publishable key from the bundle. If rows come back, nothing else in the report matters until that is fixed.
3. Load `references/gates.md` for Gate 3→4, then `references/stack-and-lanes.md` for the Lane B substitution table — because the fix for `Billing.tsx` writing its own `credits` is not "validate on the server" (there is no server), it is column-level `REVOKE`/`GRANT` plus moving `credits` into a table with no client write policy at all.
4. Report the gap out loud: *reached Stage 8, unresolved at Stage 3.* Forty real users have been sitting on top of it, and per `OPS-01` there may be no retained logs to tell whether anyone noticed.
