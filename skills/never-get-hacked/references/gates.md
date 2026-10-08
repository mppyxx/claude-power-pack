# The exit gates: mechanically verifiable checks per stage

Load this file after the audit has placed the project on the stage map and you need to decide one thing: *may this project move to the next stage, or not.* Every gate below is a block of commands whose output decides the answer, not a judgement call — the point of a gate is that the builder cannot talk their way past it and neither can you. Run the block for the stage the project is in, report PASS/FAIL per line, and do not clear the stage while any line reads FAIL. If the project is in Lane B (client-only SPA + BaaS), read the Lane B note in each stage before running anything: several gate lines are structurally unpassable there, and emitting them as failures is a bug in the skill, not a finding about the app.

---

## How to run a gate

Every block is standalone and copy-pasteable. Run with `bash`, from the repo root, with these set where the block uses them:

```bash
export URL="https://<project-ref>.supabase.co"     # BaaS REST root
export ANON="sb_publishable_..."                    # publishable / anon key ONLY, never the secret key
export SITE="https://staging.example.com"           # the deployment under test — one you own
export DOMAIN="example.com"
```

Reporting convention used throughout: each check prints exactly one `PASS <name>` or `FAIL <name>` line, so the skill can collect them. A gate passes only when zero `FAIL` lines are printed. Lines marked **[ATTEST]** cannot be read from an API — the mechanism is a dated line in `security/attestations.yaml` that the check verifies exists and is fresh; a stale attestation is a FAIL exactly like a failing command.

### Lane map

| Lane | Shape | Gate consequence |
|---|---|---|
| **A** | Server-rendered framework + managed auth + Postgres, all data access in a server-only DAL | Every gate line below is runnable |
| **B** | Client-only SPA + BaaS (Supabase / Firebase), no server module | Gate 4, 7 and 8 have lines that are *inexpressible*, not failed — see the Lane B note per stage |
| **C** | Python backend (FastAPI / Django / Flask) | Same gates; substitute the Python-side commands noted inline |

Detect the lane mechanically before choosing a block:

```bash
ls -d app/api api/ server/ functions/ supabase/functions netlify/functions 2>/dev/null
grep -rl "'use server'" app/ src/ 2>/dev/null | head
ls requirements.txt pyproject.toml manage.py 2>/dev/null
```

`src/` containing `supabase.from(...)` or Firebase Web SDK calls with **no** `app/api`, `supabase/functions` or `netlify/functions` is Lane B.

### The one false-positive rule that governs every gate

> **Flag the missing control, not the visible key.**

A Supabase publishable/anon key, a Firebase web config, a Stripe `pk_`, a PostHog `phc_`, a Sentry DSN, a Mapbox `pk.` — all of these are *supposed* to be in the bundle (`NOTVULN-01`, `NOTVULN-02`, `NOTVULN-03`, `NOTVULN-04`). None of them is a gate failure. The gate that covers them is Gate 3, and it fails on *what the key can do*, not on the key existing. Supabase's CEO, on a scan that reported 11.04% of indie launches "exposing Supabase credentials": *"Finding a Supabase project URL and anon key in client code is expected, as both are designed to be public."* Report a leaked-key finding here and the real finding gets ignored.

---

## Stage 0 — Data decisions, before any code

**What the stage is for.** The schema is the blast radius, and it is chosen by a prompt phrased in product language. "Build me a profile page" is a product request; the column list is a legal decision. Every field with no reader is invisible liability until the day the table is dumped.

**Do**

- Run the five-question exercise in a text file before the first migration: what am I storing (name the screen or job that reads each field); who wants it; what if this whole table is published tomorrow; what if it is destroyed (do you have a backup you have *restored from*); who else can reach it (processors, collaborators, the coding agent, every preview deployment).
- Store derived assertions (`is_over_18`), not raw facts (`date_of_birth`). Delete any field with no reader **now**, while deleting it is a one-line edit.
- Design retention at the same time as the schema: `expires_at` next to the data, a daily purge job, hard-delete of personal columns to NULL leaving a non-identifying tombstone (keep a salted email fingerprint so trial and abuse controls survive), and never revive a tombstone on signup.
- Round geolocation client-side to 2 decimals (~1.1 km) and enforce it in the column type — `numeric(5,2)` / `numeric(6,2)`. `navigator.geolocation` returns 7+ decimals and the model stores what it is given.
- Configure the error tracker before it ever sees production data: `sendDefaultPii: false` plus a `beforeSend` that deletes `request.data`, cookies, `authorization`/`cookie` headers, and reduces `event.user` to an opaque id.

**Never**

- Never let a prompt choose the schema.
- Never store a government ID image or a face/voice template because a tutorial did. If unavoidable: private bucket, seconds-long signed URLs, server-side authz before minting, hard TTL, and the deletion job written *before* the upload feature ships.
- Never load a third-party SDK before an age determination resolves — 16 CFR 312.2 counts a cookie number, an IP address or a device identifier as personal information, and "collection" expressly includes passive tracking.
- Never treat "it's just fitness data" as meaning nothing applies. 16 CFR 318.2 expressly includes fitness, fertility, sexual health, sleep, mental health, genetic information and diet.

### GATE 0 → 1

```bash
# GATE 0 -> 1   requires: repo root
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

test -f security/data-map.yaml    && P "data-map exists"        || F "data-map exists"
test -f security/processors.yaml  && P "processor inventory"    || F "processor inventory"
test -f security/retention.md     && P "retention policy"       || F "retention policy"
test -f docs/breach-runbook.md    && P "breach runbook"         || F "breach runbook"

# every field names a reader, a purpose, a lawful basis and a retention period
for k in read_by purpose lawful_basis retention; do
  n=$(grep -c '^[[:space:]]*-[[:space:]]*name:' security/data-map.yaml 2>/dev/null)
  m=$(grep -c "^[[:space:]]*${k}:" security/data-map.yaml 2>/dev/null)
  [ "$n" -gt 0 ] && [ "$n" -eq "$m" ] && P "every field has $k ($m/$n)" || F "every field has $k ($m/$n)"
done
# and none of them is left blank
grep -nE '^[[:space:]]*(read_by|purpose|lawful_basis|retention):[[:space:]]*$' security/data-map.yaml \
  && F "empty values in data-map" || P "no empty values in data-map"

# no BAA required-but-unsigned  (this is the line that greys the deploy)
grep -A3 'baa:[[:space:]]*required' security/processors.yaml 2>/dev/null | grep -q 'status:[[:space:]]*unsigned' \
  && F "BAA required and unsigned" || P "no unsigned required BAA"

# the runbook carries all three deadlines
for d in 72 60 45; do
  grep -qE "\b${d}\b" docs/breach-runbook.md && P "runbook names the ${d} deadline" || F "runbook names the ${d} deadline"
done

# the processor list is built from the code's outbound hosts, not from memory
grep -rhoE 'https?://[a-zA-Z0-9._-]+' src/ app/ lib/ 2>/dev/null | sed -E 's#https?://##' | sort -u \
 | while read -r h; do grep -q "$h" security/processors.yaml || F "outbound host missing from processors.yaml: $h"; done
P "outbound-host diff complete"
```

**What the deadlines are.** 72 hours (GDPR Art. 33 notification), 60 days (16 CFR 318.4, the FTC Health Breach Notification Rule), 45 days (RCW 19.373 / Washington My Health My Data deletion, reaching "archived or backup systems").

**Lane B note.** Nothing here is lane-dependent. Stage 0 is identical for A, B and C — it is about what you store, not where the code runs. A Lane B project that skips it is *more* exposed, not less, because in Lane B any read primitive returns whole rows.

**False positive to expect.** `processors.yaml` will flag CDN and font hosts from the outbound-host diff. Those are real processors for the purposes of the inventory (they see IPs), but they are not a gate failure once listed — the check is "listed", not "eliminated".

---

## Stage 1 — Accounts and the identity root of trust

**What the stage is for.** Before any code exists there is already an attack surface: the accounts that will own it. Compromise here bypasses every control in the rest of the skill. The circular dependency almost everyone ships: the registrar contact address lives on the domain that registrar manages, so registrar compromise yields DNS → an ACME `dns-01` challenge → a valid publicly trusted certificate with no browser warning → repointed MX → every reset email in the product and from every vendor.

**Do**

- Dedicated business identity on a domain you do **not** ship the product on, registered at a **different registrar** than the product domain.
- **Two hardware keys** on five root accounts: email, registrar, GitHub, hosting/BaaS, Stripe. Register two passkeys, then **delete TOTP and SMS as login and recovery factors** — a retained TOTP fallback nullifies the passkey, because NIST SP 800-63B is explicit that manual-entry authenticators SHALL NOT be considered phishing-resistant. Supabase dashboard MFA is TOTP-only and returns no recovery codes, so register a second TOTP factor on a different device there.
- Independent, non-social logins for Vercel and Supabase. Clicking "Continue with GitHub" collapses six independent nodes into one.
- Publish CAA pinning your CA with `accounturi` and `validationmethods=dns-01`, `issuewild ";"`, and an `iodef` mailto. Enable DNSSEC. Verify registrar transfer lock. Monitor crt.sh for certificates you did not request.
- Scope every token. Vercel tokens default to **Full Account** ("acts on your personal account and every team you belong to") — use `vercel tokens add --project <PROJECT_ID>` with an expiration. Supabase PATs "carry the same privileges as your user account" with no finer scope: treat as root, keep out of CI. GitHub classic `repo` scope reaches "organization-owned resources including projects, invitations, team memberships and webhooks" — use a fine-grained PAT on selected repos with `expires_in`, or a GitHub App with 1-hour installation tokens. Deploy keys are "credentials that don't have an expiry date," and write access equals org admin on that repo.
- Give the agent a devcontainer with no cloud credentials in the environment, a fine-grained PAT, and a **branch database, never the linked production project**.
- Two standing behavioural rules, worth more than any config: **no message or call ever legitimately asks you to enroll, reset or verify an MFA factor**, and **no CAPTCHA, download or verification step ever requires pasting into Terminal**. Only ever enter a device code that a terminal you are looking at just printed — never from a link, chat, email or QR.

**Never**

- Never put `sk_live_` or a Postgres URL in an MCP env block — use `claude mcp login` / `claude mcp logout`. Gitignore `.mcp.json` and `.claude/settings.local.json`.
- Never point an MCP server at production. Configure by URL query parameters: `?project_ref=DEV_REF&read_only=true&features=database,docs`.
- Never add yourself to a branch-protection ruleset's bypass list — "anyone with read access to a repository can view its active rulesets," so you would be publishing that the most phishable account is exempt.
- Never treat a "Viewer" role as secret-safe. Vercel documents a Project Viewer responsibility as "Examine environment variables across all environments," and Billing and Viewer members automatically act as project viewers for every project. A Vercel *Developer* can ship to production by merging to the production branch.

### GATE 1 → 2

```bash
# GATE 1 -> 2   requires: gh authenticated, dig, whois; export DOMAIN=example.com
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

gh api repos/:owner/:repo/keys --jq 'length' 2>/dev/null | grep -qx '0' \
  && P "no repo deploy keys" || F "repo deploy keys present (no expiry, write == org admin)"

gh api repos/:owner/:repo/actions/permissions/workflow --jq '.default_workflow_permissions' 2>/dev/null \
  | grep -qx 'read' && P "default workflow token is read-only" || F "default workflow token is write"

gh api repos/:owner/:repo/rulesets --jq '.[].name' >/dev/null 2>&1 \
  && P "rulesets readable" || F "cannot read rulesets"
gh api repos/:owner/:repo/branches/main/protection --jq '.' >/dev/null 2>&1 \
  && P "branch protection on main" || F "no branch protection on main"

whois "$DOMAIN" | grep -qi 'clientTransferProhibited' \
  && P "registrar transfer lock" || F "registrar transfer lock"
[ -n "$(dig +short CAA "$DOMAIN")" ] && P "CAA record published" || F "CAA record published"
[ -n "$(dig +short DS "$DOMAIN")" ]  && P "DNSSEC DS record"     || F "DNSSEC DS record"

git check-ignore -q .mcp.json && P ".mcp.json ignored" || F ".mcp.json ignored"
git check-ignore -q .claude/settings.local.json && P "settings.local.json ignored" || F "settings.local.json ignored"
git check-ignore -q .env && P ".env ignored" || F ".env ignored"

grep -rniE '(sk_live_|postgres://|SUPABASE_SERVICE)' ~/.claude.json .mcp.json 2>/dev/null \
  && F "secret in agent/MCP config" || P "no secret in agent/MCP config"

# the agent is not pointed at production
test -f supabase/.temp/project-ref && cat supabase/.temp/project-ref
grep -q 'read_only=true' .mcp.json 2>/dev/null && P "MCP read_only" || F "MCP read_only not set"

# [ATTEST] screen-only facts, dated, max 90 days old
for a in hw-keys-email hw-keys-registrar hw-keys-github hw-keys-hosting hw-keys-stripe \
         totp-sms-removed recovery-codes-on-paper github-oauth-apps-audited registrar-email-offdomain; do
  d=$(grep -A1 "^${a}:" security/attestations.yaml 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
  if [ -n "$d" ] && [ $(( ($(date +%s) - $(date -j -f %Y-%m-%d "$d" +%s 2>/dev/null || date -d "$d" +%s)) / 86400 )) -le 90 ]
  then P "[ATTEST] $a ($d)"; else F "[ATTEST] $a missing or older than 90 days"; fi
done
```

**What the attestations must actually mean.** Two hardware keys registered on all five root accounts *and TOTP/SMS removed* wherever a passkey exists. Recovery codes printed on paper, not in cloud photos or `~/Downloads`. All four GitHub third-party surfaces audited — Installed GitHub Apps, Authorized GitHub Apps, Authorized OAuth Apps, org Third-party Access — knowing that uninstalling an app does **not** de-authorize it and suspending revokes neither; full revocation requires the "Authorized GitHub Apps" tab, which "will fully deactivate any tokens issued to the app on your behalf." Registrar contact email on a different domain at a different provider than the product domain.

**Lane B note.** Identical for all lanes. A Lane B project usually has *more* root accounts in play (BaaS console is the database), not fewer.

**False positive to expect.** `gh api repos/:owner/:repo/keys` returning a non-empty list is a FAIL only if you did not intend those keys; a known, inventoried deploy key on a read-only mirror is a documented exception — record it in `security/attestations.yaml` rather than deleting the check.

---

## Stage 2 — Scaffold, dependencies, secrets

**What the stage is for.** The first `npm install` is the first time attacker-controlled code runs on your machine. `preinstall`/`install`/`postinstall`/`prepare` scripts execute arbitrary shell as your user for every package in the transitive tree. Every 2025–26 registry compromise depended on the publication window: chalk/debug lived ~2.5 hours across 18 packages with >2B combined weekly downloads; Nx s1ngularity ~4 hours, and its postinstall invoked already-installed AI CLIs (`claude --dangerously-skip-permissions -p`, `gemini --yolo -p`, `q chat --trust-all-tools`) to hunt for `.env`, `id_rsa` and wallet keystores.

**Do**

```ini
# .npmrc  (repo AND ~/.npmrc)
ignore-scripts=true
save-exact=true
```

```yaml
# .github/dependabot.yml — cooldown is the control that actually works
version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule: { interval: "weekly", day: "monday" }
    open-pull-requests-limit: 5
    cooldown: { default-days: 7, semver-major-days: 14, semver-patch-days: 3 }
    groups:
      weekly-patch-and-minor:
        applies-to: version-updates
        update-types: ["patch", "minor"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "weekly" }
```

On pnpm set `minimumReleaseAge` (default 1440 minutes since v11) plus `minimumReleaseAgeStrict` and `trustPolicy: no-downgrade`. A 24-hour cooldown would have blocked both Shai-Hulud waves and all 18 chalk/debug versions.

Verify any AI-suggested package before installing it — USENIX Security 2025 measured **19.7%** of LLM-recommended packages as non-existent, 205,000+ unique hallucinated names, 43% recurring across all 10 reruns:

```bash
PKG=some-package-name
npm view "$PKG" time.created repository maintainers   # 404 = hallucination; under 90 days + "popular" = stop
pip index versions "$PKG"                             # Python equivalent
```

Pin every GitHub Action to a full 40-char commit SHA (`npx -y pinact@latest run`) — `tj-actions/changed-files` (CVE-2025-30066, CVSS 8.6, CISA KEV, 23,000+ repos) was backdoored by re-pointing existing *tags*, so `uses: action@v44` became malicious with zero change on the consumer side. Add `permissions: contents: read` to every workflow. Never use `pull_request_target` with untrusted input, and never interpolate `github.event.*` into a `run:` step — pass through `env:`.

Secrets, layered: `.gitignore` with `.env*` and `!.env.example`; a gitleaks pre-commit hook pinned to **v8.30.1** (the gitleaks README's own example still shows v8.24.2 — do not copy it); GitHub push protection turned **on**, because it is disabled by default and must be enabled by an admin/owner/security manager; on Vercel mark production secrets **Sensitive** (non-readable after creation, values ≥32 characters redacted in build logs).

**Never**

- Never `curl … | sudo bash`. The server can detect a piped invocation and serve different content than it serves a browser. Download → hash-check against the vendor's published hash → read → run, as separate steps.
- Never use `npx -y <server>` in an MCP config — it fetches and executes the latest published version on every launch. `postmark-mcp` was clean through 1.0.15, then added a one-line hidden BCC to `phan@giftshop.club` on every outgoing email in v1.0.16 at ~1,500 weekly downloads. Use `npx --no --` or an exact-pinned local devDependency.
- Never trust "0 vulnerabilities" from `npm audit` (`SUPPLY-05`). It is a CVE lookup against the GitHub Advisory Database and structurally cannot flag a package published minutes ago with a malicious postinstall, a typosquat, or the pre-disclosure window of chalk/debug and Shai-Hulud.
- Never assume Dependabot's "0 open alerts" is real. "Dismiss low impact issues for development-scoped dependencies" is enabled by default on public repos and "alerts that are auto-dismissed upon creation do not send notifications" — while devDependencies run with full filesystem, network and secret access on your CI runner.
- Never inherit `.claude/`, `.cursor/rules/`, `CLAUDE.md`, `AGENTS.md` or a `SKILL.md` from a cloned template without reading it. Snyk's ToxicSkills scan of 3,984 agent skills found 36.82% with at least one security flaw, 534 critical, 76 with confirmed malicious payloads. Pillar Security's Rules File Backdoor hides instructions in zero-width joiners and bidi markers that render as nothing in a PR diff and are fully read by the model.

### GATE 2 → 3

```bash
# GATE 2 -> 3   requires: gitleaks, trufflehog, gh, node
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

grep -q 'ignore-scripts=true' .npmrc 2>/dev/null && P "install scripts disabled" || F "install scripts disabled"
git ls-files | grep -qE 'package-lock\.json|pnpm-lock\.yaml|yarn\.lock' \
  && P "lockfile committed" || F "lockfile committed"
git check-ignore -q .env && git check-ignore -q .env.local \
  && P ".env family ignored" || F ".env family ignored"

# FULL HISTORY, not the working tree
gitleaks git -v --log-opts="--all" . >/dev/null 2>&1 && P "gitleaks clean across full history" \
  || F "gitleaks findings in history — rotate at the provider before anything else"
trufflehog git file://. --results=verified 2>/dev/null | grep -q . \
  && F "trufflehog found a VERIFIED-LIVE credential" || P "no verified-live credentials"

npx -y pinact@latest run --check >/dev/null 2>&1 && P "all actions pinned to SHA" \
  || { grep -rn 'uses: .*@v[0-9]' .github/workflows/ ; F "actions pinned to a mutable tag"; }
grep -rqn 'permissions:' .github/workflows/ 2>/dev/null \
  && P "explicit workflow permissions" || F "no explicit workflow permissions"
grep -rqn 'pull_request_target' .github/workflows/ 2>/dev/null \
  && F "pull_request_target in a workflow" || P "no pull_request_target"

# every resolved tarball comes from the public registry you think it does
grep -rhoE '"resolved": *"[^"]*"' package-lock.json 2>/dev/null | grep -v registry.npmjs.org \
  && F "lockfile resolves off-registry" || P "lockfile resolves to registry.npmjs.org only"

# the alerts you were never notified about
n=$(gh api '/repos/:owner/:repo/dependabot/alerts?state=auto_dismissed' --paginate --jq 'length' 2>/dev/null | paste -sd+ - | bc)
[ "${n:-0}" -eq 0 ] && P "no auto-dismissed dependabot alerts" || F "$n auto-dismissed dependabot alerts unread"

test -f .claude/claude-security-guidance.md && test -f .claude/security-patterns.yaml \
  && P "repo-specific agent security rules committed" || F "repo-specific agent security rules committed"
grep -qE 'NEXT_PUBLIC_\[A-Z_\]\*\(KEY\|SECRET\|TOKEN\|PASSWORD\)|queryRawUnsafe' .claude/security-patterns.yaml 2>/dev/null \
  && P "at least one deterministic repo rule" || F "at least one deterministic repo rule"

# [ATTEST] screen check: Settings -> Advanced Security -> push protection ON (off by default)
grep -q 'push-protection-enabled' security/attestations.yaml 2>/dev/null \
  && P "[ATTEST] push protection enabled" || F "[ATTEST] push protection enabled"
```

**If gitleaks hits history, the gate is not the fix.** Rotate at the provider first. GitHub's own words: "Simply removing the secret from the codebase, pushing a new commit, or deleting and recreating the repository do not prevent the secret from being exploited." Truffle Security's Cross Fork Object Reference research shows commits survive deletion of the fork *and* of the upstream, reachable at `/commit/<hash>`, with 4-hex-char short SHAs brute-forceable through the UI.

**Lane B note.** Fully runnable. Lane B repos still have a `package.json`, a lockfile, a CI config and an agent footprint — this gate is where a client-only project has the most to gain, because it has no server-side controls to fall back on.

**Lane C substitution.** PyPI has **no `--ignore-scripts` equivalent**; the litellm/telnyx April 2026 incident used a `.pth` file, which CPython's `site` module executes "at every Python startup, regardless of whether a particular module is actually going to be used" (`litellm==1.82.8` was live 2h32m with over 119,000 downloads, GHSA-98x5-vq43-vc5p). Replace the `.npmrc` line with a hash-pinned lockfile check and `pip index versions <name>` for hallucination checks:

```bash
grep -qE '^\s*--hash=sha256:' requirements.txt && echo "PASS  hash-pinned requirements" \
  || echo "FAIL  hash-pinned requirements"
```

**False positive to expect.** `npm audit` noise will crowd this gate if you let it (`NOTVULN-12`). `brace-expansion` CVE-2025-5889 is **CVSS 1.3 LOW**; `ajv` CVE-2025-69873 is **disputed — 2.9 LOW from the CNA, 7.5 HIGH from CISA/Red Hat** — and only exploitable if you enable `$data`, off by default. Neither is a gate failure. `path-to-regexp` CVE-2026-4867 (CVSS 7.5, all versions **before 0.1.13**, reachable from any request to a matching route) is. Report reachability, not count.

---

## Stage 3 — The data model and the authorization boundary

**What the stage is for.** The most important stage in the whole skill. Decide *where authorization lives* before writing features, because retrofitting it later is a migration, not an edit.

**Do**

- Enable RLS on every table in an exposed schema, with **four separate per-command policies** and a `WITH CHECK` on every write. `using ((select auth.uid()) = owner_id)`, never `using (true)`.
- Kill mass assignment explicitly, because **it is inexpressible in RLS** (`BAAS-08`): a perfectly correct owner-scoped UPDATE policy still permits `PATCH {"role":"admin","credits":999999}` against your own row. `WITH CHECK` sees only the new row; there is no `OLD` in a policy. Three layers, best last:

```sql
-- 1. column grants: PostgREST honours them
revoke update on public.profiles from anon, authenticated;
grant  update (display_name, avatar_url, bio) on public.profiles to authenticated;

-- 2. a trigger that pins the columns a policy cannot see
create or replace function public.pin_privileged_columns()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  new.role     := old.role;
  new.credits  := old.credits;
  new.is_pro   := old.is_pro;
  return new;
end $$;
create trigger pin_privileged_columns before update on public.profiles
  for each row execute function public.pin_privileged_columns();

-- 3. best: privileged attributes live where the client cannot write at all
create table public.entitlements (
  user_id uuid primary key references auth.users(id) on delete cascade,
  status text not null, current_period_end timestamptz, credits integer not null default 0
    check (credits >= 0)
);
alter table public.entitlements enable row level security;
create policy ent_select_own on public.entitlements for select to authenticated
  using ((select auth.uid()) = user_id);
revoke insert, update, delete on public.entitlements from anon, authenticated;
```

Firestore equivalent of layer 2, since Firestore rules are all-or-nothing filters rather than row filters:

```javascript
match /users/{uid} {
  allow update: if request.auth.uid == uid
    && request.resource.data.role     == resource.data.role
    && request.resource.data.credits  == resource.data.credits;
}
```

- Write a pgTAP suite with **positive and negative** cases per verb, run under `supabase test db`, using `set local role authenticated` plus `set_config('request.jwt.claims', …, true)`.
- Firestore: `firebase emulators:exec "npm test"` green, and capture the rules coverage report at `http://<host>:<port>/emulator/v1/projects/<projectId>:ruleCoverage.html`.

**Never**

- Never validate RLS by clicking around the UI. A server route holding the service key (which carries `BYPASSRLS`) renders pages happily while nothing about the attack path changed.
- Never test RLS from a migration, the Supabase SQL editor, or as `service_role`. PostgreSQL's manual: "Superusers and roles with the BYPASSRLS attribute always bypass the row security system… Table owners normally bypass row security as well" — and all three of those contexts are owner or BYPASSRLS.
- Never build a pgTAP suite only on `throws_ok`. `USING` failures are **silent** (zero rows, no error) while `WITH CHECK` and missing grants raise 42501, so a throws-only suite passes against a wide-open UPDATE policy.
- Never assume Firestore rules cascade to subcollections. A rule on `/users/{uid}` does not cover `/users/{uid}/messages`. Map every collection path in code against the rules file.

### GATE 3 → 4

The gate is **external**, with the public key, from outside the app.

```bash
# GATE 3 -> 4   requires: curl; export URL=https://<ref>.supabase.co ANON=<publishable key> TABLE=<table>
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

r=$(curl -s "$URL/rest/v1/$TABLE?select=*&limit=1" -H "apikey: $ANON")
[ "$r" = "[]" ] || echo "$r" | grep -q '"code"' \
  && P "anon SELECT returns empty or an error" || F "BAAS-01: anon SELECT returned rows -> $r"

c=$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$URL/rest/v1/$TABLE?id=eq.00000000-0000-0000-0000-000000000000" \
      -H "apikey: $ANON" -H 'Content-Type: application/json' -d '{"role":"admin"}')
case "$c" in 401|403|404) P "anon PATCH refused ($c)";; *) F "anon PATCH accepted ($c) — mass assignment open";; esac

curl -s -X POST "$URL/auth/v1/signup" -H "apikey: $ANON" -H 'Content-Type: application/json' \
     -d '{"email":"gatecheck@example.com","password":"aaaaaaaaaaaa"}' | grep -q 'access_token' \
  && F "open signup mints a session instantly — 'to authenticated' is not authorization" \
  || P "signup does not hand back an access_token"

# Firebase equivalents — expect 401 / 403 / PERMISSION_DENIED on all three
# export FB_PROJECT=my-project FB_BUCKET=my-project.appspot.com COLL=users
for u in "https://$FB_PROJECT.firebaseio.com/.json?shallow=true" \
         "https://firestore.googleapis.com/v1/projects/$FB_PROJECT/databases/(default)/documents/$COLL" \
         "https://firebasestorage.googleapis.com/v0/b/$FB_BUCKET/o"; do
  b=$(curl -s "$u"); echo "$b" | grep -qiE 'PERMISSION_DENIED|"error"' \
    && P "firebase denied: $u" || F "firebase OPEN: $u -> $(echo "$b" | head -c 120)"
done
```

```sql
-- expect ZERO rows from all three
select schemaname, tablename from pg_tables where schemaname='public' and rowsecurity=false;
select * from pg_policies where qual = 'true' or with_check = 'true';
select grantee, table_name, privilege_type from information_schema.role_table_grants
 where grantee in ('anon') and table_schema='public';
```

Plus, as machine-readable artifacts rather than opinions:

- Supabase Security Advisor: zero `rls_disabled_in_public`, zero `security_definer_view`, zero `rls_references_user_metadata` lints. A permissive `using (true)` policy shows as lint `0024_permissive_rls_policy`.
- `supabase test db` green with negative cases present: `grep -c "set local role" supabase/tests/*.sql` must be > 0, and the suite must contain assertions that expect **zero rows**, not only `throws_ok`.
- Firebase rules coverage report captured. Any uncovered rule line is a rule no test proves anything about — this is the strongest machine-generated "prove the fix" artifact available anywhere in the skill.

**Lane B note.** This gate is *the whole security model* in Lane B. RLS policies and Security Rules **are** the DAL, written in SQL/CEL. Nothing here is substituted or skipped; if anything, tighten it, because PostgREST hands the caller a query builder, not just parameters — `select=`, `eq/gt/like/in/is/fts`, `order=`, and resource embedding (`?select=*,actors(*)`) that pivots through foreign keys into tables whose RLS you forgot. Add one extra line to the block for Lane B, walking every foreign key:

```bash
for t in $(psql "$DB_URL" -Atc "select tablename from pg_tables where schemaname='public'"); do
  echo -n "$t embed -> "; curl -s -o /dev/null -w '%{http_code}\n' \
    "$URL/rest/v1/$TABLE?select=*,${t}(*)&limit=1" -H "apikey: $ANON"   # expect 4xx or []
done
```

**False positives this gate will produce if you are careless.**

| Looks alarming | Verdict | The real check |
|---|---|---|
| `sb_publishable_…` / anon JWT in the bundle | `NOTVULN-01` — designed to be public, **do not rotate it** | Did the anon SELECT above return rows? If yes the finding is `BAAS-01` |
| `GET /rest/v1/` listing every table and RPC | `NOTVULN-05` — inherent to PostgREST, hiding it is not a fix | Same: enumeration returning *nothing* is the goal, not enumeration being impossible |
| Firebase `apiKey` in the bundle | `NOTVULN-02` — "only identify your Firebase project and app" | Security Rules plus App Check. **But** a Gemini Developer API key has the identical `AIza` prefix and must never be in code — check what the key is restricted to in Google Cloud, not the prefix |
| `anon` / `authenticated` roles in `pg_policies` | `NOTVULN-08` — the normal Supabase model | `qual = true`, `'anon' = any(roles)` on non-public data, un-revoked grants (`BAAS-07`), or `to authenticated` combined with open signup (`AUTHN-07`) |
| A JWT visible in the browser | `NOTVULN-07` — that is how the app works | Is it in `localStorage`; is it *verified* or merely decoded; does it carry `"role":"service_role"` (`BAAS-04`, critical) |

One more non-finding worth pre-empting, because it wastes a whole audit cycle: `auth.uid() = user_id` **does not fail open on NULL rows**. `null = user_id` evaluates to `NULL`, which denies. That pattern fails *closed* and produces a confusing outage, not a leak. The genuine fail-open in the neighbourhood is the ungranted-revoke problem, `BAAS-07`.

---

## Stage 4 — Features, one trust boundary at a time

**What the stage is for.** Per-feature prompting cannot enforce a cross-cutting invariant. Feature #7 gets `where: {id}` while feature #1 got `where: {id, orgId}`. Background jobs, cache keys, admin routes and export routes are the usual survivors. OWASP Top 10:2025 A01 reports **100% of tested applications have some broken access control**.

**Do, per feature, in this order**

1. **Parse at the boundary.** `zod` `.strict()` with an explicit `.max()` on every string, `safeParse` with a 400 on failure, and only `parsed.data` flowing downstream. `formData.get('x') as string` is a compile-time lie with zero runtime effect and is the canonical AI-generated line; this one gap underlies NoSQL operator injection, prototype pollution, mass assignment, ReDoS and much of the XSS.
2. **Authenticate, then authorize on ownership**, inside the handler/action/tool — not in middleware, not in a layout, not in the UI (`AUTHZ-03`). Put the predicate *inside* the query: `findFirst({where:{id, orgId: user.orgId}})`, not `findUnique` plus an `if`. Return **404, not 403**. UUIDs are not authorization — they stop enumeration, not leakage.
3. **Return a minimal DTO.** Props passed from a Server Component to a Client Component are serialized into the Flight/HTML payload; `SELECT *` → `<Profile user={row}/>` leaks `passwordHash` into page source even if never rendered.
4. **Write the deny test before the fix.** Watch it FAIL on the vulnerable build, then fix. Otherwise the test frequently passes vacuously. Pair every deny test with an allow test on the same endpoint, or a typo'd path 404s for everyone and reports a clean pass.

**Traps AI reliably gets wrong at this stage**

| Feature | The trap | The fix |
|---|---|---|
| Any rendered text | XSS is empirically the #1 vulnerability LLMs generate — Veracode measures CWE-80 at a 13.53% (2025) / ~15% (2026) pass rate | Framework auto-escaping; `textContent` not `innerHTML`; DOMPurify ≥3.2.7 with explicit `ALLOWED_TAGS`/`ALLOWED_ATTR` |
| LLM output rendered as markdown | `rehype-raw` without `rehype-sanitize` renders raw HTML from assistant messages as live DOM | Drop `rehype-raw`, or add `[rehypeSanitize, defaultSchema]` after it in the same array |
| Credits, coupons, quotas | `read → check → write` is the natural shape and every model writes it; 50 parallel requests all pass the check | `UPDATE … SET credits = credits - 1 WHERE id = ? AND credits >= 1 RETURNING *` (zero rows = insufficient); `UNIQUE(coupon_id, user_id)` |
| File upload | `file.mimetype` is client-controlled; an uploaded `avatar.svg` served from your origin is stored XSS | Magic-byte sniff with `file-type`; allowlist `image/jpeg\|png\|webp` (**never** SVG or text/html); re-encode via `sharp` with `limitInputPixels`; `crypto.randomUUID()` names; serve from a separate registrable domain with `nosniff` + `Content-Disposition: attachment` |
| ffmpeg / ImageMagick wrappers | `child_process.exec` spawns `/bin/sh -c`; quoting is not a fix because `$( )` and backticks survive double quotes | `execFile`/`spawn` with an argv array and `shell: false`, a filename regex, and a timeout. Python twin: `subprocess(shell=True)` |
| Tokens, reset codes, invites | `Math.random()` is xorshift128+ and algebraically invertible — a few outputs recover the state and all future outputs | `crypto.randomBytes(32).toString('base64url')`; store only a SHA-256 hash with a short TTL and a single-use flag |
| Link preview, webhook tester, image proxy, RAG "add a URL" | SSRF; string blocklists lose to redirects and DNS rebinding | Resolve all A+AAAA records, reject private ranges, then **pin the connection to the validated IP** with `redirect: 'manual'` and a timeout |
| AI endpoint | The default AI-SDK scaffold ships `POST /api/chat` with no auth, size cap or quota; the resale economy is real (Sysdig documented up to ~$46k/day of victim spend) | Session check as line one; reject >8k-char payloads; server-chosen model from an allowlist; hard `maxOutputTokens`; per-user budget. Never `eval` model output — enforce in the DB with an `ai_reader` role, SELECT-only, `default_transaction_read_only`, `statement_timeout` |
| WebSocket / realtime | Socket.IO middleware "runs only once per connection", so every `join`/`publish` frame after the upgrade is trusted; its `cors` option does not protect the WebSocket transport at all ("WebSocket connections are not subject to CORS restrictions") | Treat every room/channel id in a frame as untrusted path-parameter-grade input, and call the **same** authorization module the REST routes import — on every join *and* every publish |

**Never**

- Never rely on static analysis to pass this gate. CodeQL's JavaScript security pack has directories for 56 CWEs and **no** CWE-285, CWE-863 or CWE-639 directory; its CWE-862 directory contains exactly two files, both about empty passwords in config. Missing authorization is the *absence* of a check the tool cannot know was required. `zap-baseline.py` is passive and single-identity and can never find a BOLA.
- Never let a guard live only in `middleware.ts` / `proxy.ts`. CVE-2025-29927 (CVSS 9.1) skips it with an `x-middleware-subrequest` header; CVE-2026-64642 does it via Turbopack plus a single `config.i18n.locales` entry. As of Next.js 16.0.0 the file is renamed `middleware.ts` → `proxy.ts`, so every grep must cover both — and Server Functions are POSTs to the route they are used on, so "a Proxy matcher that excludes a path will also skip Server Function calls on that path." That is an auth bypass produced by a *refactor*.

### GATE 4 → 5

```bash
# GATE 4 -> 5   requires: node, playwright; export COOKIE_A / COOKIE_B for two accounts in two tenants
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

# 1. two-account, two-tenant replay across every route, as owner / other-tenant / anonymous
COOKIE_A="$COOKIE_A" COOKIE_B="$COOKIE_B" npx tsx authz-diff.ts \
  && P "differential authz replay clean" || F "differential authz replay leaked"

# 2. table-driven role x endpoint x object-owner matrix, wired as a REQUIRED CI check
npx playwright test tests/authz.spec.ts && P "authz matrix green" || F "authz matrix red"

# 3. route inventory diffed against the test matrix — nothing untested
node scripts/route-coverage.mjs && P "every entrypoint appears in the matrix" || F "untested entrypoints"

# 4. deny rows must OUTNUMBER allow rows
d=$(grep -c "expect: 'deny'" tests/authz.spec.ts 2>/dev/null)
a=$(grep -c "expect: 'allow'" tests/authz.spec.ts 2>/dev/null)
[ "${d:-0}" -gt "${a:-0}" ] && P "deny rows ($d) outnumber allow rows ($a)" || F "deny rows ($d) do not outnumber allow rows ($a)"

# 5. quick manual sweeps
for f in $(find app -name route.ts 2>/dev/null); do
  grep -qE 'auth\(|getUser\(|getSession\(' "$f" || F "no auth call in $f"; done; P "route auth-call sweep complete"
grep -rn 'where: { id' --include='*.ts' . | grep -v 'userId\|orgId\|tenantId\|ownerId' \
  && F "object lookup without an owner predicate" || P "no unscoped object lookups"
grep -rl 'userB\|otherUser\|tenantB' test/ tests/ >/dev/null 2>&1 \
  && P "differential authz tests exist" || F "NO DIFFERENTIAL AUTHZ TESTS"

# 6. the fail-open check: boot with AUTH_SECRET deliberately unset, assert NON-200 on a protected route
AUTH_SECRET= npm run start >/dev/null 2>&1 &
sleep 5; c=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/me); kill %1
[ "$c" != "200" ] && P "fails closed with AUTH_SECRET unset ($c)" || F "fails OPEN with AUTH_SECRET unset (200)"

# 7. cache-leak check — run the anonymous fetch TWICE, the leak often appears only once the entry is populated
curl -s -H "Cookie: $COOKIE_A" "$SITE/api/me" -o /tmp/a.json
curl -s -H "Cookie: $COOKIE_B" "$SITE/api/me" -o /tmp/b.json
curl -s "$SITE/api/me" -o /tmp/anon1.json; curl -s "$SITE/api/me" -o /tmp/anon2.json
cmp -s /tmp/a.json /tmp/b.json && F "same body for two identities — cache key ignores identity" || P "identity-distinct bodies"
cmp -s /tmp/a.json /tmp/anon2.json && F "authenticated body served to anonymous on second fetch" || P "no anonymous cache leak"
curl -sI -H "Cookie: $COOKIE_A" "$SITE/api/me" | grep -iE 'cache-control|vary|age|x-vercel-cache'
```

**Deny assertions accept only `[401,403,404]`.** A 200 fails even with an empty body. Deny rows must outnumber allow rows because an agent will delete an authz check to make an "expected 200, got 403" test pass, and only a test that asserts 403 survives that.

**One matrix row per second entrypoint** — MCP server, mobile API, OAuth/integration surface, admin panel. Four 2026 advisories are the same shape, one route that missed the guard its siblings had: flowise `PUT /api/v1/executions/:id` "lacks the `checkAnyPermission()` middleware that protects all other execution endpoints" (CVE-2026-70475), flowise `/api/v1/files` (CVE-2026-69252), `@budibase/server` `GET /api/global/groups` missing the `auth.builderOrAdmin` its sibling had (CVE-2026-73301). Directus <12.0.0 (CVE-2026-61836, CVSS 8.6) is the cache-key form: `share`, `role`, `admin` and `policies` omitted from the key.

**Lane B note — this is the gate Lane B cannot express.**

| Gate 4 line | Lane B status | What replaces it |
|---|---|---|
| Route-handler auth sweep (`find app -name route.ts`) | **Inexpressible** — no route handlers exist | The Gate 3 external curl block, re-run per table after every feature |
| Zod `safeParse` at the trust boundary | **Cosmetic** — it runs in the attacker's browser | `CHECK` constraints, `DOMAIN` types, `NOT NULL`, RLS `WITH CHECK`, Firestore `request.resource.data` predicates |
| `AUTH_SECRET` unset fail-open leg | **Inexpressible** — no server to boot | pgTAP negative cases plus the Supabase Security Advisor lints |
| Server-only DAL, `import 'server-only'` | **Impossible** — no server module | RLS policies / Security Rules *are* the DAL, written in SQL/CEL |
| Cache-leak diff on `/api/me` | **Inexpressible** — no origin routes to cache | Storage bucket ACLs and Realtime channel authorization (`BAAS-10`) |

The differential two-account replay **is** still runnable in Lane B, and it is the single most valuable thing there — point it at the BaaS REST API with two real user JWTs instead of at your own routes. Do not emit any of the "inexpressible" rows as failures. Emit them as a lane statement: *these are checks this architecture cannot run, and here is what stands in for them.*

**Lane B escalation trigger.** If any of the seven observable triggers is a YES, client-only cannot be made *correct*, only improved, and the honest gate outcome is "change lane," not "fix this":

```bash
grep -rniE "api\.(openai|anthropic|resend|stripe|twilio|sendgrid)\.com" src/    # 1. secret-authenticated API
grep -rn 'whsec_' .                                                            # 2. something must POST to you
grep -rniE '\b(credits|plan|role|is_pro|verified)\b' src/                       # 3. a value the user must not set
```

Plus, by inspection: (4) any per-invocation cost — model tokens, email, SMS, image generation, egress; (5) a rule depending on state the caller may not read; (6) email beyond Supabase auth, whose built-in provider is **2 emails/hour project-wide**; (7) per-user rate limits on data writes, not just auth. Triggers 1, 2 and 4 are the ones this population hits within a week of launching.

---

## Stage 5 — Pre-deploy hardening

**What the stage is for.** Everything here is verified against the **built artifact**, not the source. The `.env` grep is a hint; the artifact grep is the ground truth.

**Do**

- Delete `.map` files from the deploy artifact. `build.sourcemap: 'hidden'` only strips the comment — deleting is the real control. Upload to the error tracker first, then `find ./dist -name "*.map" -delete`. `sourcesContent` holds your verbatim original files and is discoverable with `site:example.com filetype:map`.
- Delete `/api/debug`, `/api/seed`, `/api/test-email`.
- Turn off GraphQL introspection and GraphiQL in production and set `allowBatchedHttpRequests: false` — batching puts 1,000 login attempts in one HTTP request, defeating request-rate limits. FastAPI: `docs_url=None, redoc_url=None, openapi_url=None`.
- Ship the header set: `Strict-Transport-Security: max-age=63072000; includeSubDomains` (add `preload` only once every subdomain can serve HTTPS forever — preload is effectively irreversible); `X-Content-Type-Options: nosniff`; `X-Frame-Options: DENY` / `frame-ancestors 'none'`; `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy: geolocation=(), camera=(), microphone=()`; `Cache-Control: no-store` on authenticated responses; `poweredByHeader: false`.
- CSP: nonce + `'strict-dynamic'` + `object-src 'none'` + `base-uri 'none'`. Know the documented cost — nonces force dynamic rendering, disable static optimization and ISR, and are **incompatible with Partial Prerendering**. If that is unacceptable, use `experimental.sri` (hash-based, static-friendly) and record `unsafe-inline` as a *named known gap*, not a solved problem.
- CORS: an exact-string allowlist (a `Set`), never regex, never `endsWith`, never reflection. `origin: true, credentials: true` reflects any Origin, and PortSwigger's PoC exfiltrates the victim's authenticated response with `withCredentials=true`. Anchor any regex with `^…$` — `r"https://myapp.com"` matches `https://myapp.com.evil.com`.

**Never**

- Never run DAST against production or a host you do not own. Run it against **staging you own**, with seeded data and a restorable database.
- Never trust `semgrep ci` as a gate: bare `semgrep ci` exits 0 "regardless of whether there were findings" and is a *reporting* step that will never fail the build. Use `semgrep scan --error`.

### GATE 5 → 6

```bash
# GATE 5 -> 6   requires: npm, curl, docker, semgrep, gitleaks, osv-scanner; export SITE=https://staging.example.com
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

npm run build >/dev/null 2>&1 || F "build failed"

# THE artifact grep — expect NO OUTPUT. This is the ground truth, not the .env grep.
out=$(grep -rEo 'eyJ[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]+|sk-ant-[A-Za-z0-9-]+|AKIA[0-9A-Z]{16}|SG\.[A-Za-z0-9_-]{20,}|sb_secret_[A-Za-z0-9]+|service_role' .next/static/ dist/ build/ 2>/dev/null)
[ -z "$out" ] && P "artifact grep clean" || F "SECRET IN BUILD ARTIFACT: $(echo "$out" | head -3)"

grep -rn 'NEXT_PUBLIC_\|VITE_\|PUBLIC_\|REACT_APP_\|EXPO_PUBLIC_' .env* 2>/dev/null \
  | grep -iE 'secret|service_role|private|token|password|sk_|api_key' \
  && F "SECRET-01: a real secret behind a public env prefix" || P "no secret behind a public prefix"

find .next dist build -name '*.map' 2>/dev/null | grep -q . \
  && F "source maps still in the artifact" || P "no source maps in the artifact"
for r in /api/debug /api/seed /api/test-email /graphql /docs /redoc /openapi.json; do
  c=$(curl -s -o /dev/null -w '%{http_code}' "$SITE$r")
  case "$c" in 404|401|403) P "$r not exposed ($c)";; *) F "$r exposed ($c)";; esac
done

for h in strict-transport-security x-content-type-options x-frame-options referrer-policy permissions-policy content-security-policy; do
  curl -sI "$SITE" | grep -qi "^$h:" && P "header $h" || F "header $h missing"
done
curl -sI "$SITE" | grep -qi '^x-powered-by:' && F "x-powered-by present" || P "no x-powered-by"

# CORS must not reflect an arbitrary origin with credentials
curl -sI -H 'Origin: https://evil.example.net' "$SITE/api/me" | grep -i 'access-control-allow-origin' \
  | grep -q 'evil.example.net' && F "CORS reflects arbitrary origin" || P "CORS does not reflect"

semgrep scan --error && P "semgrep clean" || F "semgrep findings"
gitleaks dir -v . >/dev/null 2>&1 && P "gitleaks dir clean" || F "gitleaks dir findings"
osv-scanner scan -r . >/dev/null 2>&1 && P "osv-scanner clean" || F "osv-scanner findings — check reachability, not count"

docker run -v "$(pwd)":/zap/wrk/:rw -t ghcr.io/zaproxy/zaproxy:stable \
  zap-baseline.py -t "$SITE" -J report.json && P "zap baseline clean" || F "zap baseline findings"

# a map file must 404 even if one slipped into the deploy
c=$(curl -s -o /dev/null -w '%{http_code}' "$SITE/_next/static/chunks/main.js.map"); \
  [ "$c" = "404" ] && P "map 404s ($c)" || F "map reachable ($c)"
```

**Lane C substitution.** Replace the header and debug-route lines with Django's own posture audit, which is the best free automated check in this whole skill:

```bash
python manage.py check --deploy   # expect zero W-series warnings
```

Confirm `SECRET_KEY` comes from `os.environ["SECRET_KEY"]` (bracket form, fail-closed), `ALLOWED_HOSTS` is explicit, `DEBUG` is False, and no ModelForm uses `fields = "__all__"` — Django's own docs say that pattern "has led to serious exploits on major websites (e.g. GitHub)". Flask/Werkzeug `debug=True` is unauthenticated RCE via the debug console, and the PIN is derivable offline from `uuid.getnode()` and `/etc/machine-id` given any file-read or SSRF primitive.

**Lane B note.** The artifact grep, the source-map deletion, the header set and the CORS check all run normally — a Lane B SPA still produces a `dist/`, still gets served with headers, and is *more* dependent on them because its entire codebase ships to the client. Only the `/api/*` debug-route sweep is inexpressible; substitute a sweep of your BaaS Edge Function endpoints if any exist, and otherwise drop those lines.

**False positive to expect.** The artifact grep's `eyJ` pattern matches the anon/publishable JWT, which is *supposed* to be there (`NOTVULN-01`). Decode before reporting: a payload with `"role":"anon"` is expected; `"role":"service_role"` is `BAAS-04`, critical, and an immediate incident. The same discipline applies to `AIza` — a Firebase web key is fine (`NOTVULN-02`), a Gemini Developer API key with the identical prefix is not.

---

## Stage 6 — The first public URL

**What the stage is for.** Everything that is public becomes public *now*, including things you did not intend to publish. The dominant failure is environment scope: `DATABASE_URL` added with all environments ticked is the default path in the dashboard, and it means the public, unauthenticated, half-finished preview build talks to the production database.

**Do**

- Scope every secret **per environment** before the first deploy.
- Enable Vercel Authentication with **Standard Protection** (available on all plans; protects previews, leaves the production domain public), or on Cloudflare Pages, Settings → General → Enable access policy. `X-Robots-Tag: noindex` is not a control — Vercel's own KB: "Anyone with the URL can still open it."
- Move to your own domain and certificate before onboarding real users. `*.lovable.app`, `*.replit.app`, `*.base44.app` and `*.netlify.app` are enumerable namespaces; Red Access scanned ~380,000 vibe-coded assets across those platforms and found ~5,000 leaking sensitive data, and you share reputation with whatever phishing is hosted next door.
- Publish SPF with `-all` and DMARC starting at `p=none` with `rua` reporting, escalating to `p=reject`.
- Publish `/.well-known/security.txt` per RFC 9116 with `Contact` and exactly one `Expires` (RFC 3339, under a year out). Expired files are the most common defect.
- Use the `__Host-` cookie prefix (Secure, Path=/, no Domain) so a subdomain takeover cannot receive your cookies.

**Never**

- Never set `"public": true` in `vercel.json` or run `vercel deploy --public`. That serves `/_src` (source code and build output) and `/_logs` (build logs) to anyone, and toggling protection back on does **not** retroactively protect existing deployments — "the only option is to delete these deployments."

### GATE 6 → 7

```bash
# GATE 6 -> 7   requires: curl, jq, dig, nmap, vercel CLI; export SITE / DOMAIN / IP
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

# any 200 here is an INCIDENT, not a finding
for p in .env .env.local .env.production .env.backup .git/config .git/HEAD docker-compose.yml dump.sql _src _logs; do
  c=$(curl -s -o /dev/null -w '%{http_code}' "$SITE/$p")
  case "$c" in 404|401|403) P "$p not served ($c)";; *) F "INCIDENT: $p served ($c) — rotate every credential in it first";; esac
done

grep -q '"public"[[:space:]]*:[[:space:]]*true' vercel.json 2>/dev/null \
  && F 'vercel.json has "public": true — /_src and /_logs are world-readable' || P "no public:true in vercel.json"

# every historical hostname in CT logs resolves to something you still own, or has had its record deleted
curl -s "https://crt.sh/?q=%25.$DOMAIN&output=json" \
  | jq -r '.[].name_value | split("\n")[]' | sed 's/^\*\.//' | sort -u > /tmp/hosts.txt
while read -r h; do
  t=$(dig +short CNAME "$h"); [ -n "$t" ] && echo "  $h -> $t"; done < /tmp/hosts.txt
P "CT-log CNAME inventory printed — every target must be a resource you still own"

nmap -Pn -p 22,80,443,3000,5432,6379,27017,9200,2375 "$IP"   # run from OUTSIDE the box

vercel env ls 2>/dev/null | grep -iE 'production.*preview.*development' \
  && F "an env var covers Production+Preview+Development" || P "env vars scoped per environment"

curl -s "$SITE/.well-known/security.txt" | grep -q '^Expires:' \
  && P "security.txt has an Expires" || F "security.txt missing or has no Expires"
curl -sI "$SITE" | grep -i 'set-cookie' | grep -q '__Host-' \
  && P "__Host- cookie prefix" || F "__Host- cookie prefix"

dig +short TXT "$DOMAIN" | grep -q 'v=spf1' && P "SPF published" || F "SPF published"
dig +short TXT "_dmarc.$DOMAIN" | grep -q 'v=DMARC1' && P "DMARC published" || F "DMARC published"

# [ATTEST] open a preview URL in a logged-out private window on a different network.
# Seeing the app instead of a login wall is a FAIL.
grep -q 'preview-protection-verified' security/attestations.yaml 2>/dev/null \
  && P "[ATTEST] previews gated" || F "[ATTEST] previews gated"
```

**Why the dotfile sweep is an incident tier of its own.** Unit 42 documented a crew that scanned "more than 230 million unique targets," hit 110,000 domains, harvested 90,000+ env var sets including 7,000 cloud access keys, then exfiltrated *and deleted* S3 objects. A 200 on `.env` means the credentials are already collected. Rotate before you investigate.

**Subdomain takeover.** `can-i-take-over-xyz` catalogues the fingerprints to look for on the CNAME targets printed above — "No such app", "The specified bucket does not exist". A dangling CNAME is a claimable attack surface, and the `__Host-` prefix is the reason a takeover cannot then collect your session cookies.

**Lane B note.** All of it runs. Lane B commonly *is* a static host plus a BaaS, so the dotfile sweep and the CT-log inventory are the highest-value lines here. The `vercel env ls` line has no analogue if you deploy from a drag-and-drop host — substitute a screen attestation that no BaaS secret key was ever pasted into the frontend build config.

**False positive to expect.** `NOTVULN-13`: a public GitHub repo, a discoverable subdomain, an exposed project ID and a present `security.txt` are **not** vulnerabilities. Certificate Transparency makes every hostname public by design. The finding in that neighbourhood is a *dangling* record, or a `security.txt` whose `Expires` has passed, or a repo that ever contained a secret (`SECRET-05`).

---

## Stage 7 — First real users

**What the stage is for.** Now one user can hurt another, and strangers can hurt your bill.

**Do**

- **Dual-key rate limiting on shared state**: per-IP (10/60s) **and** per-account (5/15min) via `@upstash/ratelimit` on Redis, plus a WAF rule on `^/api/auth/(register|signup|login|signin)$`. Per-IP alone fails against credential-stuffing kits, which distribute requests across large numbers of unique IPs. In-memory counters do not limit anything on serverless — the real limit becomes limit × concurrency, and Vercel counts rate limits *per region*.
- **Ship every new rule in Log / DRY_RUN first**, read what it would have blocked, then flip to enforce. Same discipline for a new authorization check: run it report-only (evaluate, log `authz_would_deny`, allow) for a day before enforcing.
- **Four alerts, and only four**, or you will mute them: a spend threshold with an *automatic action* on every billed provider; auth-failure rate from structured log lines; a 429/5xx spike from firewall observability; an uptime check that touches the database.
- **Spend caps must have actions.** Vercel Spend Management does not stop usage unless you separately enable project pausing, checks only every few minutes, and pausing is not instantaneous. AWS has no true hard cap — configure Budgets **Actions** (a Deny IAM policy or SCP), not just alerts. Supabase's Spend Cap excludes Compute, PITR and IPv4.
- **Turn on every account-security notification.** All seven Supabase `GOTRUE_MAILER_NOTIFICATIONS_*_ENABLED` settings default to **false** — password changed, email changed, phone changed, identity linked/unlinked, MFA factor enrolled/unenrolled. The templates exist and ship default bodies; they are simply not sent. That default is why the entire account-lifecycle attack class goes undetected.
- **Own your logs.** Vercel Hobby retains runtime logs for **one hour** (Pro 1 day; Observability Plus 30 days), and log drains are Pro/Enterprise only — a Hobby project cannot configure one at all. A weekend IDOR discovered on Tuesday has zero forensic evidence. Write an append-only `security_events` table (`revoke update, delete from` the app role) with 90-day minimum retention, logging the OWASP must-log set: auth successes and failures, authorization failures, session failures, admin access, sensitive data access, and file uploads. Never log session identifiers, access tokens, passwords, connection strings or keys.

**Never**

- Never send a stack trace or `err.message` to the client. Return a generic message plus a random ref id; log the detail server-side.
- Never leave an unauthenticated email or SMS trigger. One unauthenticated POST per send lets an attacker bomb a victim's inbox and push your domain past Gmail's spam thresholds. Enable Twilio's SMS Fraud Guard and disable every country you do not send to under Geo Permissions.
- Never pass `?limit` straight into `take:`. Clamp to a `MAX_PAGE` and switch to cursor pagination — an unthrottled list API is a bulk-export API.
- Never run a public "try to hack this" challenge on the same account as your product. Separate account/team on every provider, separate registrable domain, synthetic data only, hard caps with automatic actions, a rehearsed sub-60-second kill switch, and published rules of engagement putting DoS and provider-targeted testing out of scope.

### GATE 7 → 8

```bash
# GATE 7 -> 8   requires: curl, websocat (if you have sockets); export SITE
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

# rate limiting actually engages under concurrency
seq 1 30 | xargs -P 30 -I{} curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST "$SITE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"a@b.c","password":"x"}' | sort | uniq -c | tee /tmp/rl.txt
grep -q ' 429' /tmp/rl.txt && P "429s observed under 30-way concurrency" || F "no 429 — limiter absent or per-instance"

# per-instance counters are not limits on serverless
grep -rn 'express-rate-limit\|new Map()\|storage:\s*.memory.' --include='*.ts' . \
  && F "in-memory rate limiter (limit becomes limit x concurrency)" || P "limiter uses shared state"

# user enumeration: identical status, body AND timing for existing vs non-existing accounts
for e in "known@example.com" "definitely-not-a-user@example.com"; do
  curl -s -o /tmp/body.$$ -w "$e %{http_code} %{time_total}\n" -X POST "$SITE/api/auth/login" \
    -H 'Content-Type: application/json' -d "{\"email\":\"$e\",\"password\":\"wrong\"}"
  wc -c < /tmp/body.$$
done
P "enumeration triad printed — status, body size and time must match across both rows"

# credit / quota race: the read-check-write shape fails here
seq 1 50 | xargs -P 50 -I{} curl -s -o /dev/null -w '%{http_code}\n' -X POST "$SITE/api/spend-one-credit" \
  | sort | uniq -c
P "race printed — exactly one 2xx per available credit, or the update is not atomic"

# unauthenticated send triggers
for r in /api/email /api/send /api/sms /api/invite /api/contact; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SITE$r" -H 'Content-Type: application/json' -d '{}')
  case "$c" in 401|403|404) P "$r requires auth ($c)";; *) F "$r accepts unauthenticated POST ($c)";; esac
done

# pagination clamp
n=$(curl -s "$SITE/api/items?limit=100000" | grep -o '"id"' | wc -l)
[ "$n" -le 100 ] && P "limit clamped ($n rows)" || F "limit unclamped ($n rows) — this is a bulk-export API"

# [ATTEST] the provider-side facts
for a in spend-cap-with-automatic-action supabase-mailer-notifications-all-enabled \
         security-events-table-append-only log-export-offprovider; do
  grep -q "^$a:" security/attestations.yaml 2>/dev/null && P "[ATTEST] $a" || F "[ATTEST] $a"
done

# realtime revocation, if you have sockets: hold the socket, log out, see if events still arrive
# websocat "wss://$SITE/socket" &   then POST /api/auth/logout   then watch the socket
```

**The socket test matters because nothing that revokes an HTTP session touches an open socket** unless you built `killSession(sessionId)`. Socket.IO middleware "runs only once per connection."

**Lane B note.**

| Gate 7 line | Lane B status | What replaces it |
|---|---|---|
| `@upstash/ratelimit` / shared-state limiter | **Impossible** for data traffic | Platform auth rate limits (fixed, not tunable), Firebase App Check, a DB counter table plus a `BEFORE INSERT` trigger, and spend caps |
| Turnstile / hCaptcha `siteverify` | **Impossible** for your own writes | Supabase Auth CAPTCHA, which covers auth endpoints only |
| Unauthenticated send-trigger sweep | Partly inexpressible | The Supabase built-in email provider is capped at **2 emails/hour project-wide**, which is a hard ceiling but also trigger #6 for leaving the lane |
| Server-side stack-trace suppression | **Inexpressible** — errors surface in the client | Do not put anything in a DB error message you would not publish; the PostgREST error body is the client's |

The rate-limit line above still runs against a BaaS endpoint and still tells you something true: if you get no 429s, you have no limit. Report it as a lane constraint (`BAAS-10` neighbourhood) rather than as a fixable defect, and pair it with the escalation trigger list from Stage 4.

**False positive to expect.** A CORS error in the console is `NOTVULN-11` — the browser doing its job, and CORS is not an authorization mechanism at all since `curl` ignores it. The real findings are origin *reflection* with credentials, substring matching, and a whitelisted `null`.

---

## Stage 8 — Money

**What the stage is for.** Deciding whether someone can get the product for free. Every control here reduces to one sentence: **entitlement comes only from a signature-verified webhook.**

**Do**

- Read the **raw** body and call `stripe.webhooks.constructEvent(raw, sig, whsec)` before touching the DB; return 400 on failure; keep the default 5-minute tolerance ("Don't use a tolerance value of 0"). In Express, mount `express.raw({type:'application/json'})` on the webhook route **before** `app.use(express.json())`; in the Next.js App Router use `await req.text()`. A parsed-and-re-serialized body is the top cause of the "verification kept failing so I removed it" cascade.

```ts
// app/api/webhooks/stripe/route.ts
import Stripe from 'stripe';
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(req: Request) {
  const raw = await req.text();                        // RAW, never req.json()
  const sig = req.headers.get('stripe-signature')!;
  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(raw, sig, process.env.STRIPE_WEBHOOK_SECRET!);
  } catch {
    return new Response('bad signature', { status: 400 });
  }
  await db.transaction(async (tx) => {
    // idempotency and the grant in ONE transaction
    const ins = await tx.insert(processedWebhookEvents)
      .values({ id: event.id }).onConflictDoNothing().returning();
    if (ins.length === 0) return;                       // already processed, skip
    await grantFromEvent(tx, event);                    // re-resolve plan from the purchased price.id
  });
  return new Response('ok', { status: 200 });
}
```

- The client sends only an **opaque plan key**. The server resolves it against a hardcoded catalog to a Stripe Price ID and passes `line_items[{price}]` — never `price_data`. Treat `metadata` as a key into the catalog, never as a value, and re-resolve it in the webhook against the purchased `price.id`.
- **Idempotency**: `INSERT event.id` into a `processed_webhook_events` table with a primary key, inside the same transaction as the grant; a unique violation means skip. Stripe retries for up to 3 days, allows manual Resend for 15 days, and can emit two distinct Event objects for one change.
- **Store Stripe's `status` verbatim plus `current_period_end`, not a boolean.** Handle `customer.subscription.created/updated/deleted/paused/resumed`, `invoice.paid`, `invoice.payment_failed`, `charge.refunded`, `charge.dispute.*`. Stripe gives no ordering guarantee, so guard writes with `AND updated_from_event_created <= event.created`.
- One `requireEntitlement(feature)` helper reading the entitlement row from your DB — status active, `current_period_end` in the future, feature in list — as the first line of every gated handler. Audit with `grep -L requireEntitlement` across all gated route files.
- Money as integers or `NUMERIC`, never float. `zod .int().positive().max()` on every amount. `CHECK (credits >= 0)`. Reject self-transfer.

**Never**

- **Never grant on the success redirect.** Stripe: "You can't rely on triggering fulfillment only from your checkout landing page." The success page may call the *same* idempotent `fulfillCheckout(sessionId)` the webhook calls — that function retrieves the session server-side and checks `payment_status !== 'unpaid'` — but the webhook remains mandatory.
- **Never let the client write entitlement columns.** `supabase.from('profiles').update({is_pro:true, credits:999999})` from the browser console works whenever RLS is off or the UPDATE policy has no `WITH CHECK` pinning those columns. Use the `entitlements` table from Stage 3, with a SELECT-only owner policy and `REVOKE INSERT/UPDATE/DELETE from anon, authenticated`; only the service-role webhook writes it.
- Never take card details on your own page unless you have decided to. Stripe-hosted fields = SAQ A ("card information never touches your servers"); your own form with Stripe.js = SAQ A-EP; raw PAN to the API = SAQ D, which Stripe warns can mean "more than 300 security controls."

### GATE 8 → 9

```bash
# GATE 8 -> 9   requires: curl, stripe CLI, psql
# export SITE DB_URL COOKIE_A
# export TEST_USER=<uuid of a test account>  EVT=<evt_id of a genuine past event>  SUB=<sub_id to cancel>
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

before=$(psql "$DB_URL" -Atc "select status||':'||credits from entitlements where user_id='$TEST_USER'")

# 1. forge an event — expect 400, and NO entitlement change
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SITE/api/webhooks/stripe" \
  -H 'Content-Type: application/json' \
  -d '{"type":"checkout.session.completed","data":{"object":{"metadata":{"userId":"me","plan":"pro"}}}}')
[ "$c" = "400" ] && P "forged webhook rejected (400)" || F "forged webhook returned $c — expected 400"
after=$(psql "$DB_URL" -Atc "select status||':'||credits from entitlements where user_id='$TEST_USER'")
[ "$before" = "$after" ] && P "no entitlement change from the forgery" || F "FORGED EVENT GRANTED ENTITLEMENT: $before -> $after"

# 2. replay a GENUINE event twice — expect exactly one grant
g0=$(psql "$DB_URL" -Atc "select count(*) from entitlement_grants where user_id='$TEST_USER'")
stripe events resend "$EVT"; sleep 5
stripe events resend "$EVT"; sleep 5
g1=$(psql "$DB_URL" -Atc "select count(*) from entitlement_grants where user_id='$TEST_USER'")
[ $((g1-g0)) -eq 1 ] && P "replay granted exactly once" || F "replay granted $((g1-g0)) times — idempotency broken"

# 3. code-level invariants
grep -rn 'constructEvent' app/ api/ >/dev/null 2>&1 \
  && P "signature verification present" || F "no signature verification"
grep -rn 'req.json()' app/api/webhooks/ 2>/dev/null \
  && F "webhook parses JSON before verifying — verification will fail and get deleted" \
  || P "webhook reads the raw body"
grep -rn 'price_data' app/ api/ 2>/dev/null \
  && F "server accepting client-priced line items" || P "prices resolved server-side from a catalog"
grep -rn 'processed_webhook_events\|onConflictDoNothing' app/ api/ >/dev/null 2>&1 \
  && P "idempotency table used" || F "no idempotency table"
grep -rlE 'checkout/success|/success' app/ 2>/dev/null | xargs grep -ln 'grant\|is_pro\|credits' 2>/dev/null \
  && F "entitlement granted on the success redirect" || P "no grant on the success redirect"

# 4. secrets
grep -rn 'whsec_\|sk_live_\|rk_live_' . --exclude-dir=node_modules --exclude-dir=.git \
  && F "live Stripe secret in the repo" || P "no live Stripe secrets in the repo"

# 5. lifecycle: cancel then refund a test subscription and confirm access is revoked both times
stripe subscriptions cancel "$SUB"; sleep 5
curl -s -o /dev/null -w 'gated route after cancel: %{http_code}\n' -H "Cookie: $COOKIE_A" "$SITE/api/pro-feature"
# expect 402/403 — a 200 here is the whole gate failing
```

Plus one browser check that no command can replace: with DevTools open through a full checkout, **no request to your own origin ever contains card digits**. If one does, you are in SAQ A-EP or SAQ D and the PCI decision was made accidentally.

**Lane B note — Gate 8 is the hard stop.** A browser cannot receive an inbound POST, so **Stripe webhook signature verification is impossible in Lane B**. There is no workaround, no client-side substitute, and no configuration that makes it safe. The gate outcome for a Lane B project taking money is not "fix these lines" — it is: *add one Edge Function or Worker for the webhook, at which point you are no longer client-only.* Trigger #2 from the Stage 4 list (`grep -rn 'whsec_' .`) fires the moment payments appear.

The only Gate 8 lines a Lane B project can run before that migration are the repo secret scan and the entitlement-column check — and the entitlement-column check is the one that saves it, because `supabase.from('profiles').update({is_pro:true})` from the browser console is exactly how a Lane B app gets its product taken for free:

```bash
# export MY_ID=<your own user uuid>  MY_JWT=<your own session access token>
curl -s -o /dev/null -w '%{http_code}\n' -X PATCH "$URL/rest/v1/profiles?id=eq.$MY_ID" \
  -H "apikey: $ANON" -H "Authorization: Bearer $MY_JWT" \
  -H 'Content-Type: application/json' -d '{"is_pro":true,"credits":999999}'   # expect 401/403
```

**False positive to expect.** `pk_test_` and `pk_live_` in the bundle are `NOTVULN-03` — Stripe's own table marks publishable keys "Safe to expose: Yes… you can put in front-end code." The control that must exist is that all authorization and every amount and price is decided server-side. `sk_`, `rk_` and `sk_org_` are the opposite: "Only publishable keys are safe to expose outside your application's backend."

---

## Stage 9 — Steady state: the continuous gate

**What the stage is for.** An app that was correct on launch day becomes incorrect on its own. Every framework CVE is a clock event — a day on which a working, unmodified app became exploitable. The failure mode for this audience is not refusing to patch; it is that the loop was **never installed**. Gate 9 is different from every other gate in this file: it is not an exit gate, it is an **invariant**. It must be true at any moment you check it, forever.

**The cadence, sized so it actually happens**

| When | Budget | What |
|---|---|---|
| **Immediate** | 1–4 h | A CVE in your stack that is network-reachable pre-auth, **or** in the CISA KEV feed, **or** has a public PoC. Also any "your package was compromised" notice, any secret-scanning alert, any anomalous spend alert. |
| **Weekly** (Mon) | 20 min | Merge the one grouped patch/minor PR after CI is green. Skim one digest. Glance at the four alerts and last week's auth-failure count. |
| **Monthly** | 1 h | Major-version PRs, one at a time. Run `osv-scanner scan -r .` and `gitleaks dir .` and **read the output**, not the exit code. Check provider audit logs for logins you do not recognise. Confirm the backup ran. |
| **Quarterly** | 4 h | **Restore a backup into a scratch database and query it**; record elapsed time as your RTO. Rotate credentials. Re-run the re-audit checklist. Delete unused deployments, keys, OAuth apps, subdomains. Check your `security.txt` expiry. |

**Do**

- **Four feeds, no more.** Dependabot alerts; the CISA KEV JSON feed via a weekly `kev-watch.sh` that curls it, filters `.vulnerabilities` by `dateAdded` within 14 days and greps for your stack's vendor/product names (a KEV hit is the IMMEDIATE tier, not the weekly one); the `nodejs-sec` Google Group plus nodejs.org's vulnerability blog; and your framework's release channel. Route everything to one Discord/Slack channel.
- **Prepare the cooldown escape hatch before you need it.** When `npm audit fix` is blocked by the release-age filter, "npm retains the vulnerable version and exits with a non-zero code"; pnpm's `minimumReleaseAgeStrict` fails the install outright. Under a real 9.8 RCE the panic response is deleting the cooldown permanently. Instead, exclude by **exact version** — `minimumReleaseAgeExclude: ['next@16.2.11']`, or a one-off `npm install pkg@ver --min-release-age=0 --ignore-scripts`. Rehearse the failure once. Dependabot's default 3-day cooldown does **not** apply to security updates.
- **Rotation with overlap windows.** Accept an ordered comma-separated list of secrets and try each, because models write `process.env.STRIPE_WEBHOOK_SECRET` as a singular value. Stripe rolls with delayed expiry "for up to 24 hours… multiple secrets are active… one signature per secret." Supabase signing keys move standby → current → previously used → revoked with **no forced sign-out**, and you must "wait at least 1 hour and 15 minutes before revoking" at a 1-hour token expiry (JWKS is edge-cached 10 minutes). Legacy Supabase JWT-secret rotation is the opposite: "Currently active users get immediately signed out." **Rotation without revocation is not rotation.** Never rotate user passwords on a schedule.
- **Backups that survive you.** Encrypt with `age` (public key only in CI), push to a **separate cloud account** with a PutObject-only identity, enable S3 Versioning plus Object Lock in compliance mode (undeletable "by any user, including the root user"). Supabase retention: Pro 7d / Team 14d / Enterprise up to 30d; PITR WAL every 2 minutes; Free-plan backups are not downloadable at all.

**Never**

- Never auto-merge with no cooldown, and never auto-merge majors.
- Never use `pull_request_target` in a dependency-PR workflow. Writing it that way — the model's standard fix for "the workflow can't see my secrets" — dismantles GitHub's boundary that "Your secrets are available in Dependabot secrets rather than as GitHub Actions secrets" and reproduces the Nx s1ngularity primitive. Use `on: pull_request`, `permissions: contents: read`, `npm ci --ignore-scripts` with an explicit `npm rebuild`, and no production `DATABASE_URL` in tests.

### GATE 9 (continuous — must be true at any moment)

```bash
# GATE 9   run this on any day, at any time. Every line must PASS every time.
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

# the lockfile has moved since launch day
first=$(git log --reverse --format=%at -1)
lock=$(git log -1 --format=%at -- package-lock.json pnpm-lock.yaml yarn.lock)
[ -n "$lock" ] && [ "$lock" -gt "$first" ] && P "lockfile updated since first commit" \
  || F "lockfile mtime == launch day — the maintenance clock was never installed"

# and within the last 60 days
[ $(( ($(date +%s) - lock) / 86400 )) -le 60 ] && P "lockfile updated in the last 60 days" \
  || F "lockfile untouched for $(( ($(date +%s) - lock) / 86400 )) days"

ls .github/dependabot.yml renovate.json >/dev/null 2>&1 && P "update automation exists" || F "no update automation"
grep -rqn 'minimumReleaseAge\|cooldown' .npmrc pnpm-workspace.yaml .github/dependabot.yml 2>/dev/null \
  && P "release-age cooldown configured" || F "no cooldown — a 2.5-hour compromise window reaches you"

# read the alerts nobody notified you about
n=$(gh api '/repos/:owner/:repo/dependabot/alerts?state=auto_dismissed' --paginate --jq 'length' 2>/dev/null | paste -sd+ - | bc)
[ "${n:-0}" -eq 0 ] && P "no silently auto-dismissed alerts" || F "$n auto-dismissed alerts unread"

# security.txt still valid
exp=$(curl -s "$SITE/.well-known/security.txt" | grep -i '^Expires:' | cut -d' ' -f2)
[ -n "$exp" ] && [ "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \< "$exp" ] \
  && P "security.txt Expires is in the future ($exp)" || F "security.txt expired or missing ($exp)"

# KEV watch: anything added in the last 14 days that names your stack
curl -s https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json \
 | jq -r --arg d "$(date -u -v-14d +%Y-%m-%d 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%d)" \
   '.vulnerabilities[] | select(.dateAdded >= $d) | "\(.cveID) \(.vendorProject) \(.product)"' \
 | grep -iE 'next\.?js|vercel|node|postgres|supabase|firebase|django|fastapi|flask|stripe' \
 && F "KEV entry naming your stack in the last 14 days — this is the IMMEDIATE tier" \
 || P "no fresh KEV entry naming your stack"

# the refactor tripwire: authz lines deleted in the working diff
git diff --unified=0 | grep -E '^-.*(auth|session|user_id|owner|role|can[A-Z])' \
  && F "authorization lines deleted in this diff — re-audit before merge" || P "no authz lines deleted"
files=$(git diff --name-only | wc -l); [ "$files" -le 30 ] && P "diff is $files files" \
  || echo "WARN  diff touches $files files — treat as a refactor, re-audit"

# [ATTEST] quarterly and monthly facts, with dates
for a in restore-drill-completed audit-logs-exported-offprovider credentials-rotated; do
  d=$(grep -A1 "^${a}:" security/attestations.yaml 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
  age=$([ -n "$d" ] && echo $(( ($(date +%s) - $(date -j -f %Y-%m-%d "$d" +%s 2>/dev/null || date -d "$d" +%s)) / 86400 )))
  case "$a" in
    restore-drill-completed)          lim=92;;
    audit-logs-exported-offprovider)  lim=31;;
    credentials-rotated)              lim=92;;
  esac
  [ -n "$d" ] && [ "$age" -le "$lim" ] && P "[ATTEST] $a ($d, ${age}d)" || F "[ATTEST] $a stale or missing"
done
```

**The restore drill is the only line here that cannot be faked.** "A backup exists" is not the claim; "I restored it into a scratch database and queried it, and it took N minutes" is. N is your RTO. The two failure modes are never-restored (silent zero-byte dumps, missing schemas, plan-tier restore gates) and sharing a blast radius with production — the Unit 42 crew exfiltrated *and deleted* the S3 objects.

**The seven re-audit triggers** that invalidate your last review: a change to auth; a change to payments; adding file upload; adding an AI feature; **a large AI-driven refactor**; a dependency compromise in the news; and a fixed quarterly interval. Wire the refactor one as the `git diff` tripwire above, plus a CI gate that fails the PR when auth/payments/upload/AI/policy paths change without `SECURITY-AUDIT.md` being touched. *(**REPORTED**, not CONFIRMED: the mechanism is sound and the individual triggers are each sourced, but this specific seven-item taxonomy is the dossier researcher's construction, not a cited standard. Present it as a working checklist, not as an industry rule.)*

**Log-retention arithmetic worth stating to the user.** GitHub's personal security log keeps 90 days, the org audit log 180, and Supabase platform audit logs are Team/Enterprise only. Vercel Hobby keeps runtime logs for one hour. If you have not exported off-provider within those windows, the evidence for an incident you have not yet noticed is already gone — which is why the export attestation has a 31-day limit.

**Lane B note.** Every Gate 9 line runs. Two additions specific to Lane B: the BaaS platform is now also your database vendor, so the restore drill must cover *their* export path (Free-plan Supabase backups are not downloadable at all — that is a FAIL on the restore-drill line, not a warning), and the Gate 3 external curl block should be re-run as part of the monthly slot, because a new table added by a prompt arrives with RLS off by default.

**False positive to expect.** `osv-scanner` and `npm audit` will both produce a count that looks alarming and mostly is not (`NOTVULN-12`). Report **reachability**, not count. The monthly instruction is to read the output, not the exit code, precisely because the exit code cannot tell the difference between `brace-expansion` CVE-2025-5889 (CVSS 1.3 LOW) and `path-to-regexp` CVE-2026-4867 (CVSS 7.5, all versions before 0.1.13, reachable from any request to a matching route).

---

## Stage 10 — Decommissioning

**What the stage is for.** Shutting down in the wrong order **creates** an attack surface. Deleting the platform resource before the DNS record opens a claimable window instantly. An abandoned app whose DNS still resolves is a live attack surface, not a finished project — treat "no commits in over 6 months, DNS still resolving" as a Stage 10 candidate rather than as "done."

**The order, and it is not negotiable**

1. **DNS records first.** Enumerate every historical host via crt.sh, then delete the records — *before* deleting the resources they point at.
2. **Platform resources** second.
3. **Export, then delete, the data.** GDPR Art. 17(2) requires telling processors to delete too.
4. **Revoke** — not regenerate — API keys, webhook endpoints, OAuth apps, deploy keys. Revoke credentials **before** starting any soft-delete clock; GCP soft-deleted projects persist 30 days.
5. **MX/SPF/DKIM/DMARC last.**

**Never** let the domain lapse silently: it enters a 30-day Redemption Grace Period and then releases to anyone, who then receives your reset emails.

### GATE 10 (final)

```bash
# GATE 10   requires: curl, jq, dig; export DOMAIN
P(){ echo "PASS  $1"; }; F(){ echo "FAIL  $1"; }

curl -s "https://crt.sh/?q=%25.$DOMAIN&output=json" \
  | jq -r '.[].name_value | split("\n")[]' | sed 's/^\*\.//' | sort -u > /tmp/old-hosts.txt
while read -r h; do
  a=$(dig +short "$h")
  [ -z "$a" ] && P "no resolution: $h" || F "still resolves: $h -> $a"
done < /tmp/old-hosts.txt

while read -r h; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$h/" 2>/dev/null)
  [ "$c" = "000" ] && P "no response: $h" || F "still serving: $h ($c)"
done < /tmp/old-hosts.txt

# ordering check: DNS must already be gone before you touch platform resources
grep -q 'dns-records-deleted' security/attestations.yaml 2>/dev/null \
  && P "[ATTEST] DNS deleted first" || F "[ATTEST] DNS deleted first — do NOT delete resources yet"

# Provider-side screen checks, each recorded as an attestation:
for a in zero-active-api-keys zero-webhook-endpoints zero-authorized-oauth-apps \
         zero-deploy-keys zero-running-projects data-exported processors-instructed-to-delete \
         domain-renewal-or-deliberate-release mx-spf-dkim-dmarc-removed-last; do
  grep -q "^$a:" security/attestations.yaml 2>/dev/null && P "[ATTEST] $a" || F "[ATTEST] $a"
done
```

The final artifact is a **dated note recording what data existed, that it was exported, and that processors were instructed to delete.** That note is the only thing that survives the shutdown, and it is what answers a subject-access or regulator question a year later.

**Revoke, do not regenerate.** Regenerating a key issues a new one; it does not always invalidate sessions or tokens minted with the old one. Revocation is the operation that ends the old credential's authority, and it must happen before any soft-delete clock starts, because a soft-deleted project's credentials can remain live inside the grace window.

**Lane B note.** Gate 10 is lane-independent, with one addition: in Lane B the BaaS project *is* the application, so "zero running projects" includes the Supabase/Firebase project itself, and the data export step is the only copy you will ever have. Export before you delete the project, not after — and remember Free-plan Supabase backups are not downloadable, so the export must be a manual `pg_dump` while the project is still alive.

---

## Running gates as a set

The skill should run exactly one gate — the one for the stage the audit placed the project in — and report per-line. Two rules keep the output trustworthy:

1. **Never report an inexpressible line as a failure.** In Lane B, the Gate 4 route-handler sweep, the `AUTH_SECRET` fail-open leg, the shared-state rate limiter and the entire Gate 8 signature-verification block are *architecturally impossible*, not neglected. Print them under a separate heading — "cannot run in this architecture; here is what stands in" — and attach the escalation trigger list. Emitting them as FAIL trains the user to ignore the whole report.
2. **Never report a `NOTVULN-*` item as a finding.** Flag the missing control, not the visible key. If the anon key is in the bundle, the question the gate answers is what the anon curl returned, and only that.

A project may be cleared to the next stage when its gate prints zero `FAIL` lines and every `[ATTEST]` line is within its freshness limit. Gate 9 is not a clearance — it is re-run at every subsequent audit regardless of stage, and a FAIL there sends a live project back into the fix plan no matter how many gates it passed on the way up.

