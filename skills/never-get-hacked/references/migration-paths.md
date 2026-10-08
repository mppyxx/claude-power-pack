# Four starting points and their non-destructive migration paths

Load this file when the project already exists in some shape and the shape is part of the problem: a folder of static HTML with a key in a `<script>` tag, a Lovable/Bolt/Replit export, or an app already live with real users and a hole you just found. It is the counterpart to `stack-and-lanes.md`, which assumes you get to choose the foundation; this file assumes you didn't. The distinguishing property of a migration is that **the insecure behaviour is currently load-bearing** — the table has no RLS *because* turning RLS on makes the app go blank, and the key is in the bundle *because* the browser is the only thing that ever calls the API. So every fix here is a cutover, and cutovers have their own failure class: not "the attacker got in" but "I fixed it, took the product down, panicked, and reverted to the insecure version." That revert is the real risk this file exists to prevent.

---

## The four universal rules

These hold across all four starting points. If you remember nothing else:

1. **One revertible change at a time.** Schema change, backfill, constraint, policy, code, and key rotation are **six deployments, not one** — because reverting code does not un-rotate a key, and reverting a migration does not un-leak data.
2. **Rotate at the provider *before* deploying the code fix.** The exposed key is in every archived copy of the page. Shipping the fix does not un-publish it.
3. **Preserve evidence before touching anything** if there is any chance you were already hit. Patching first destroys the only signal that drives the notification decision.
4. **Never let one agent turn do all of it.** A large mechanical refactor is where authorization checks disappear without a visible diff. See §5.

---

## Which starting point am I in?

Run this from the project root. It is read-only.

```bash
# 1. Greenfield?
ls package.json index.html *.html 2>/dev/null | head
git -C . log --oneline 2>/dev/null | wc -l

# 2. Static pile? (HTML, no build step, no framework)
test -f package.json || echo "STATIC: no package.json"
grep -rnoE "sk-[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}" --include="*.html" --include="*.js" . 2>/dev/null

# 3. No-code / AI-builder export?
git log --format='%an %ae' 2>/dev/null | sort -u          # bot as sole author = generated
grep -rn "supabase.co\|firebaseio.com\|\.lovable\.app\|\.bolt\.host\|\.replit\.app\|\.base44\.app" \
  --include="*.ts" --include="*.tsx" --include="*.json" . 2>/dev/null | head

# 4. Live with users? — the only question that matters is whether real people have real rows
#    Ask, and then verify: a production domain in the deploy config, and non-seed rows.
ls vercel.json netlify.toml wrangler.toml firebase.json 2>/dev/null
find . -name CNAME -not -path "./node_modules/*" 2>/dev/null | head
```

Starting points 2 and 3 can also be live with users. When they are, §4 outranks §2 and §3 — do §4's Step 0 first, then come back.

---

## 1. Greenfield

Nothing exists yet. There is no migration to perform, and this is the only path where the leverage is free.

Run the stages in order (`gates.md`). The single special instruction:

> **Do Stage 0 before the first migration.** Deleting a column is a one-line edit today and a data-subject-rights problem in three months.

Stage 0 is a real stage, not paperwork. The schema *is* the blast radius, and it gets decided by a prompt phrased in product language — ask an AI for a "user profile" and the statistically common schema arrives (`full_name`, `dob`, `gender`, `phone`, `street_address`, `lat`, `lng`, `id_document_url`), none of which any screen reads. Because any read primitive returns whole rows, harvest value is a pure function of the column list. GDPR Art. 5(1)(c) and Art. 25(2) make data minimisation a duty, not a preference.

The exit artifacts, which the rest of the skill checks for:

```bash
test -f security/data-map.yaml    || echo "FAIL: no data map"
test -f security/processors.yaml  || echo "FAIL: no processor inventory"
test -f security/retention.md     || echo "FAIL: no retention policy"
test -f docs/breach-runbook.md    || echo "FAIL: no breach runbook"
```

Every field in `data-map.yaml` names a purpose, a lawful basis, a retention period, **and a screen or job that reads it**. Any field with an empty reader gets deleted now.

Greenfield is also where the stack choice deletes whole bug classes for free — that decision lives in `stack-and-lanes.md`, and it is gone by the time you reach §2, §3 or §4 of this file.

---

## 2. A pile of static HTML with a key in a `<script>` tag

### The judgement call — read this before you propose anything

The instinct, reinforced by nearly every AI assistant, is "migrate to Next.js." That is usually wrong, and saying it costs you the user's trust in everything else you found.

> **Decision rule: if the only reason you need a server is to hold a secret and call one API, add one serverless function and keep the static site.**

A full framework port has **zero security benefit until data fetching actually moves server-side**, and it *adds* attack surface the static site never had — Server Actions as public POST endpoints, middleware-as-auth, an image optimizer. Static HTML with no backend and no secrets is a legitimate and safe architecture.

| The real finding | Not the finding |
|---|---|
| `const KEY = "sk-proj-…"` in a `<script>` tag — a live credential to *your provider account*, with your billing attached | "You should be using a framework" |
| A contact form POSTing to a third party that never validates | "There's no build step" |
| A client-side "admin" mode — a JS flag that hides a UI | "You're not using TypeScript" |
| An unauthenticated `/api/*` you just added — a denial-of-wallet funnel | "The HTML isn't semantic" |

A framework is genuinely the right answer when you need **per-user data, sessions, or server-rendered private pages**. Nothing less.

### 2a. The four-step plan (static plus one function)

Each step is independently revertible.

**Step 1 — Rotate the exposed key at the provider first.** Before any code changes. It is public, and the fix does not un-publish it.

**Step 2 — Deploy one function, at a path the HTML does not yet call.**

```ts
// netlify/functions/ask.mts     (Vercel equivalent: api/ask.ts — same shape)
import type { Config, Context } from "@netlify/functions"

export default async (req: Request, ctx: Context) => {
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 })
  const { q } = await req.json()
  if (typeof q !== "string" || q.length > 2000) return new Response("Bad input", { status: 400 })
  const r = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
               "Content-Type": "application/json" },
    body: JSON.stringify({ model: "gpt-4.1", input: q, max_output_tokens: 500 }),
  })
  return new Response(JSON.stringify({ text: (await r.json()).output_text }), {
    headers: { "Content-Type": "application/json" },
  })
}
export const config: Config = { path: "/api/ask" }
```

Verify it before touching the client:

```bash
curl -s -X POST https://yoursite.com/api/ask \
  -H 'Content-Type: application/json' -d '{"q":"hello"}' -i | head -20
curl -s -X GET https://yoursite.com/api/ask -o /dev/null -w '%{http_code}\n'   # expect 405
```

**Step 3 — Then change the one client line.** A separate commit, separately revertible.

```js
// index.html — the only client change
const r = await fetch("/api/ask", { method: "POST", body: JSON.stringify({ q }) })
```

**Step 4 — Add auth and a rate limit to the new endpoint.** An unauthenticated `/api/ask` is a denial-of-wallet funnel: this is `AI-01`, the default-generated unauthenticated LLM proxy, and it ranks #11 in the catalog precisely because it converts directly into money leaving your account while the app keeps working perfectly.

**Verify the key is actually gone**, from the deployed site rather than the repo:

```bash
curl -s https://yoursite.com | grep -ioE "sk-[A-Za-z0-9_-]{20,}"          # expect no output
grep -rnoE "sk-[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}" \
  --include="*.html" --include="*.js" .
```

### 2b. If a framework really is justified: what breaks in the port

Migrate incrementally, never big-bang. **Next.js multi-zones** exist for exactly this: give the new app an `assetPrefix` and route only the paths it owns to it, keeping everything else on the old site.

```js
// next.config.js on the router zone — only /account/* goes to the new hardened app
async rewrites() {
  return [
    { source: '/account',        destination: `${process.env.APP_DOMAIN}/account` },
    { source: '/account/:p+',    destination: `${process.env.APP_DOMAIN}/account/:p+` },
    { source: '/app-static/:p+', destination: `${process.env.APP_DOMAIN}/app-static/:p+` },
  ]
}
```

For a per-path kill switch that needs no redeploy, route the decision through `proxy.js` behind a feature flag: `if (pathname === '/your-path' && myFeatureFlag.isEnabled()) return NextResponse.rewrite(...)`.

What actually breaks, and what to do about it:

| What breaks | Why | What to do |
|---|---|---|
| **Cross-zone routing** | Next.js prefetches and soft-navigates any relative path in `<Link>`, "which will not work across zones" | Cross-zone links must be plain `<a>` tags |
| **Server Actions on a multi-zone domain** | The action's own CSRF/Origin check rejects the user-facing origin | Set `experimental.serverActions.allowedOrigins` to list the user-facing domain |
| **URLs / SEO** | Vite hash routes or `/about.html` become `/about` | Google's site-move guidance: permanent redirects (`301` and `308`), submit an updated sitemap, and "keep the redirects for as long as possible, generally at least 1 year." For small and medium sites, "move all URLs on your site simultaneously instead of moving one section at a time" |
| **Inline `<script>` blocks** | They stop working the moment you add a real CSP | Next.js's nonce approach has a documented cost: nonces require dynamic rendering, so "Static optimization and Incremental Static Regeneration (ISR) are disabled," "Partial Prerendering (PPR) is incompatible," and pages "cannot be cached by CDNs without additional configuration." The escape hatch for a mostly-static site is the experimental `experimental.sri.algorithm` hash-based CSP, which keeps static generation |
| **Analytics tags** | Silently blocked by the CSP | Pass `nonce={(await headers()).get('x-nonce')}` through `<Script>` |
| **Forms** | A form that POSTed to a third-party endpoint now looks like it has a server, so validation "moves" — but usually nobody moves it | Validate on the server side of the new endpoint, and keep the old third-party endpoint working until the new one is verified |

**Keep the site live throughout:** deploy the new zone first at a path nothing links to, verify with `curl`, then flip the rewrite. Two independently revertible steps; a combined change is not.

### 2c. The Vite → Next.js trap that renames the leak instead of fixing it

Next.js's official Vite migration guide instructs: *"Change all environment variables with the `VITE_` prefix to `NEXT_PUBLIC_`."* Mechanically correct, and a trap. If the original had `VITE_SUPABASE_SERVICE_ROLE_KEY` — a real and common mistake — the migration faithfully renames it to `NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY` and **re-inlines a `BYPASSRLS` key into the new bundle**, laundered past review by looking idiomatic.

The larger misconception is about what the migration buys you. The guide's own strategy is to *"keep it as a purely client-side application (SPA) without migrating your existing router"* — an optional catch-all `app/[[...slug]]/page.tsx` rendering `dynamic(() => import('../../App'), { ssr: false })`. That is a good, low-risk migration strategy, and it means **every data fetch is still happening in the browser with the anon key after the migration completes.**

```
# .env — the correct end state
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_…   # browser-safe, RLS enforced
SUPABASE_SECRET_KEY=sb_secret_…                          # no public prefix; server only
```

Migration order that actually improves security:

1. Port to Next.js as an SPA per the guide; confirm nothing broke.
2. Inventory which browser fetches touch data that RLS does not already protect.
3. Move those, **one route at a time**, into Route Handlers or Server Components behind an `import 'server-only'` DAL — each its own deployable commit.
4. **Only then** delete the client-side query. Steps 3 and 4 are separate so you can revert one without the other.

Verify:

```bash
grep -rn "NEXT_PUBLIC_" .env* | grep -iE "secret|service|private|sk_|password|sb_secret"
npm run build && grep -roE "sb_secret_[A-Za-z0-9]+|service_role" .next/static/ | head
```

### 2d. False positives specific to this path

| Looks alarming | Actually fine when… | The finding to report instead |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` / `sb_publishable_…` in the bundle (`NOTVULN-01`) | RLS is on for every table in an exposed schema, with least-privilege grants | `BAAS-01` if `curl` with that key returns rows. **Do not rotate it as remediation** |
| Firebase web config `apiKey` (`NOTVULN-02`) | Security Rules plus App Check are real | `BAAS-03`. But note: a **Gemini Developer API key has the identical `AIza` prefix** and must never ship to a client — check what the key is *restricted to*, not its prefix |
| `pk_test_` / `pk_live_` (`NOTVULN-03`) | Every amount and price is decided server-side | `PAY-03` |
| PostHog `phc_`, Sentry DSN, Mapbox `pk.` (`NOTVULN-04`) | Restrictions are configured — Mapbox public tokens need URL restrictions, Google Maps keys need HTTP referrer plus API restrictions | The missing restriction, which is a billing liability, not the key's presence |

---

## 3. A no-code / AI-builder export

### What you actually have

The exports are not equivalent to each other, and the differences determine the cleanup. Establish these facts before writing a plan:

| Platform | The mechanic that changes your plan |
|---|---|
| **Lovable** | The GitHub integration is a **live two-way sync, not an export**: "Changes made in Lovable sync to GitHub" and "Changes pushed to the active GitHub branch sync back into Lovable," on one branch at a time. **Hardening the repo does not detach the platform agent from the branch you just audited.** Deleting/renaming/transferring the repo "breaks the sync" |
| **Bolt** | The Supabase integration is Vite-only — "Supabase connections are available with Vite projects. Next.js projects are not supported at this time." So the export is a client-side SPA whose entire security model is RLS, with no server tier to fall back on. This is Lane B; most standard defenses are *inexpressible*, not merely missing |
| **Any platform scanner** | A lint, not a pentest. Lovable's Basic scan covers "RLS policy linting," "Database schema review," "Dependency audit"; Deep adds "Access control review," "Backend endpoint protection," "Code-level vulnerabilities." The docs are explicit about where liability lands: "You are responsible for ensuring that your app meets the security requirements appropriate for its use case" |

Confirm the shape from the repo itself:

```bash
git log --format='%an %ae' | sort -u        # platform bot as sole author = no human review history
grep -rn "supabase.co\|firebaseio.com\|\.lovable\.app\|\.bolt\.host\|\.replit\.app\|\.base44\.app" \
  --include="*.ts" --include="*.tsx" --include="*.json" .   # backend AND still-live preview hostnames
```

### The security debt these exports characteristically carry

- Secrets committed to the repo, because the platform's env panel and the repo were the same surface during generation.
- Tables created by **raw SQL migrations**, which is the path where Supabase does *not* enable RLS by default — RLS is on by default only for tables created in the Table Editor. This is `BAAS-01`, the defining vulnerability of the whole build method.
- Every table in `public` published as an endpoint, because Supabase grants `SELECT/INSERT/UPDATE/DELETE` to `anon`, `authenticated` and `service_role` on new `public` tables by default (`BAAS-07`). An agent that added a `debug_logs` or `webhook_payloads` table during iteration published an endpoint nobody decided to publish.
- A still-live platform preview subdomain on a predictable hostname, defaulting to public.
- A `package.json` resolved by the platform at generation time, with lifecycle scripts unaudited (`SUPPLY-01`).
- Privileged operations sitting in the browser because there was no route file to put them in.

### The ordered cleanup

1. **Before touching code, treat every secret in the repo as public.** Rotate at the provider first; deploy the new value second. Git-history purge is step *n*, never step 1.
2. **Detach or fence the sync.** Decide explicitly whether the platform keeps write access to the branch. If yes, your hardening lives on a branch the platform does not edit, and merges are reviewed. Skipping this means an agent round silently reverts step 4.
3. **Enumerate the data layer from outside**, with the anon key from the bundle — one `curl` per table per verb. This is ground truth; the platform's scanner is not.
   ```bash
   # every table and RPC the Data API publishes
   curl -s "https://<ref>.supabase.co/rest/v1/" -H "apikey: $ANON" | jq -r '.definitions | keys[]'
   # then, per table, all four verbs — expect [] or 401, never rows
   curl -s "https://<ref>.supabase.co/rest/v1/profiles?select=*&limit=3" -H "apikey: $ANON"
   curl -s -X PATCH "https://<ref>.supabase.co/rest/v1/profiles?id=eq.1" -H "apikey: $ANON" \
     -H "Content-Type: application/json" -d '{"role":"admin"}' -i | head -5
   ```
4. **RLS / rules migration**, using the policies-first sequence in §4 (policies → shadow preflight → enable → index → verify by curl). Do not enable RLS and then reach for `using (true)` when the pages go blank — that is `BAAS-02`, and it is *worse* than no RLS because it passes a naive "is RLS enabled?" audit.
5. **Move every privileged call out of the browser** — an Edge Function or Route Handler **per privileged operation**, each with its own authn/authz. Not one generic proxy.
6. **Delete or auth-gate the still-live platform preview subdomain.** An app you "migrated off" is still serving at its old address until you delete it.
7. **Lock dependencies and re-audit.** `npm install --ignore-scripts`, then diff the lockfile.
8. **Add the negative authz test suite** (`authz-verification.md`) so the next agent round cannot silently undo step 4.

### If the app never leaves the platform

For platform-hosted apps with no code export, the equivalents are:

| Platform | The control that actually enforces |
|---|---|
| **Wix** | Collection permissions set to "Item's creator" / Admin-only. If "Everyone" is set for View, "anyone can use the Data API to view the collection content" regardless of what the live site shows |
| **Glide** | **Row Owners** on every per-user table — the only mechanism that stops the sync. Tab filters are cosmetic |
| **Bubble** | Privacy rules with "Everyone else" fully unchecked. Never combine "Ignore privacy rules" with an unauthenticated backend workflow |

Test as a **second real user in a logged-out private window with DevTools open**. The editor preview runs as the owner and proves nothing.

### What the dossier could not verify about exports

State these as unknowns rather than guessing:

- **Base44 and Bubble export contents** were not verifiable. `docs.base44.com` does not state whether apps can be exported to code, nor the default visibility of published apps. (The confirmed Base44 material elsewhere in the dossier is a platform-wide auth bypass — do not extrapolate export behaviour from it.)
- **Whether Replit Secrets are copied to a fork/remix** is unresolved; the docs URL 404s. If you are migrating off Replit, check it manually before assuming either answer.

---

## 4. An app already live with real users

The highest-stakes path. **The order below exists because the obvious order breaks the site or destroys the evidence.** Do not reorder it to get to the fix faster.

### Step 0 — If there is any chance you were already hit, preserve evidence before you patch

Most people patch first and destroy the only signal that drives the notification decision. Rotation and redeployment both erase audit trail, and retention on these plans is short.

Supabase edge logs carry exactly the fields you need — `request.headers.cf_connecting_ip`, `request.path`, `request.method`, `request.headers.user_agent`, `response.status_code` — but the docs state only that *"Log retention is based on your project's pricing plan."* (The per-plan numbers are not stated on that page; do not quote a number you have not checked against the pricing page.)

Run this in the Supabase Logs Explorer, and **export the result off-platform**, before anything else:

```sql
select timestamp,
       log_attributes['request.path']                     as path,
       log_attributes['request.method']                   as method,
       log_attributes['request.headers.cf_connecting_ip'] as ip,
       log_attributes['request.headers.user_agent']       as ua,
       log_attributes['response.status_code']             as status
from edge_logs
where timestamp > now() - interval '7 days'
  and log_attributes['request.path'] like '/rest/v1/orders%'
  and log_attributes['response.status_code'] = '200'
order by timestamp desc;
```

Signals that mean **assume breach**:

- Bulk `GET /rest/v1/<table>?select=*` with no `limit`, from an IP that is not your app's egress.
- Any `PATCH` or `DELETE` on a table your UI never writes.
- A burst of `/auth/v1/signup` followed immediately by reads.
- A `user_agent` of `python-requests`, `curl`, `Go-http-client`, or a Postgres client string.

Export the same window from every other provider too, before retention eats it (GitHub's personal security log keeps 90 days; the org audit log 180). For storage, list the bucket anonymously the way an attacker would and compare object counts to what your app believes it holds.

> **If the logs for the exposure window are already gone, you cannot demonstrate that no data was accessed — and that absence IS the finding.** It drives the notification decision. This is `OPS-01`, the meta-killer: it is what converts a bug into an unbounded, undatable, un-notifiable breach.

### Step 1 — Contain without a deploy

Containment must not depend on shipping code, and must not log anyone out.

```sql
-- The one-line tourniquet: stops PostgREST reads immediately, no deploy, nobody signed out.
revoke all on public.orders from anon;
```

Alternatives at the edge: a WAF deny rule on the specific path and IPs; Vercel **Attack Mode** (free on all plans; challenges all traffic, auto-allows verified bots and your own Functions/Cron, and blocked requests do not count toward usage); disabling the abused integration. If the app itself is the leak, pause the production deployment and accept `503 DEPLOYMENT_PAUSED`.

Containment comes **before** rotation. Rotating first lets an attacker who is sitting in your dashboard simply copy the new key.

### Step 2 — Shadow-mode preflight, before you enable RLS on anything

**No vendor ships an RLS dry-run.** What follows is an engineered procedure built from confirmed primitives — present it as a technique, never imply Supabase or PostgreSQL provides it.

Why it is needed: PostgreSQL's manual is unambiguous — *"If no policy exists for the table, a default-deny policy is used, meaning that no rows are visible or can be modified."* Supabase says the same for the API layer. So `alter table orders enable row level security;` on its own turns every list, detail and dashboard page blank, instantly, for every logged-in user. The documented next move — and Supabase ships a linter rule for it, `0024_permissive_rls_policy` — is to add `using (true)` to make the pages come back. The table is then *worse* than before.

Two things break silently, and both are invisible in a dev database:

- **Orphan rows.** Rows written before the ownership column existed have `user_id IS NULL`. `(select auth.uid()) = user_id` evaluates to `NULL`, which is not `true`, so they vanish permanently — from the user's view *and* from your aggregates. No error is raised.
- **Read paths the policy never anticipated.** Team sharing, admin export, a 3am cron job, a public profile page.

Run these three **read-only** queries against production first:

```sql
-- 1. Orphan check: how many rows become invisible forever?
select count(*) as would_disappear from public.orders where user_id is null;

-- 2. Shadow evaluation: apply the policy predicate as a plain filter, per user,
--    and diff against what the app returns today. Zero rows lost = safe.
select u.id as user_id,
       count(*) filter (where o.user_id = u.id) as visible_after_rls,
       count(*)                                  as visible_today
from auth.users u cross join public.orders o
group by u.id
having count(*) filter (where o.user_id = u.id) <> count(*) and u.id is not null;

-- 3. Distinct access paths actually in use — find the ones your policy forgot.
select distinct on (1) regexp_replace(query, '\d+', 'N', 'g') as shape, calls
from pg_stat_statements where query ilike '%orders%' order by 1, calls desc;
```

**Supabase preview branches cannot substitute for this.** The branching docs state plainly that *"New branches do not start with any data from your main project."* A branch validates that the migration *applies*; it cannot tell you how many production rows the policy will hide.

Then lock the answers in as tests, so the policy cannot silently regress:

```sql
-- supabase/tests/database/002-rls-orders.sql   →  run with `supabase test db`
begin;
select plan(3);

select tests.create_supabase_user('a@test.com');
select tests.create_supabase_user('b@test.com');
insert into public.orders (task, user_id) values
  ('A order', tests.get_supabase_uid('a@test.com')),
  ('B order', tests.get_supabase_uid('b@test.com'));

select tests.authenticate_as('a@test.com');
select results_eq('select count(*) from public.orders', ARRAY[1::bigint],
                  'A sees only their own order');
select is_empty($$select * from public.orders where user_id <> auth.uid()$$,
                'A cannot see B''s rows');
select tests.rls_enabled('public');

select * from finish();
rollback;
```

On Firebase the equivalent is `@firebase/rules-unit-testing`: `initializeTestEnvironment({ projectId, firestore: { rules: fs.readFileSync('firestore.rules','utf8') } })`, seed with `testEnv.withSecurityRulesDisabled(...)`, then `assertSucceeds(getDoc(alice.firestore().doc('/orders/a1')))` and `assertFails(getDoc(alice.firestore().doc('/orders/b1')))`.

```bash
ls supabase/tests/database/ 2>/dev/null || echo "no RLS tests"
grep -rl "rules-unit-testing" --include=package.json --include="*.test.*" .
```

### Step 3 — If you need an ownership column, that is five migrations, not one

You cannot write `using (auth.uid() = user_id)` against a table with no `user_id`. The obvious one-liner is a full-table rewrite plus two blocking validations, and on managed Postgres behind a pooler a multi-second `ACCESS EXCLUSIVE` lock is not a slow page — it is a queue of held connections and a full outage.

`strong_migrations` documents the specifics: a **volatile** default (`gen_random_uuid()`, `now()`) "causes the entire table to be rewritten. During this time, reads and writes are blocked" (a *constant* default is fine on Postgres 11+); `NOT NULL` on an existing column "blocks reads and writes while every row is checked"; "adding a foreign key blocks writes on both tables"; "adding an index non-concurrently blocks writes"; and backfilling in the same transaction that alters a table "keeps the table locked for the duration."

```sql
-- DO NOT: one statement, four blocking operations, on a live table.
alter table public.orders
  add column user_id uuid not null default gen_random_uuid()
    references auth.users(id);
create index orders_user_id_idx on public.orders (user_id);
```

```sql
-- 001: nullable, no default, no FK. Instant.
alter table public.orders add column user_id uuid;

-- 002: application deploy — start writing user_id on every new row (dual-write).
--      Old rows still NULL; nothing reads user_id yet.

-- 003: backfill in batches, OUTSIDE any DDL transaction, throttled.
--      Run repeatedly until 0 rows updated:
with batch as (
  select id from public.orders where user_id is null limit 5000 for update skip locked
)
update public.orders o set user_id = l.legacy_owner
from batch b join legacy_owner_map l on l.order_id = b.id
where o.id = b.id;

-- 004: constraints validated without a blocking scan.
alter table public.orders
  add constraint orders_user_id_fk foreign key (user_id) references auth.users(id) not valid;
alter table public.orders validate constraint orders_user_id_fk;   -- separate migration

alter table public.orders
  add constraint orders_user_id_not_null check (user_id is not null) not valid;
alter table public.orders validate constraint orders_user_id_not_null;
alter table public.orders alter column user_id set not null;
alter table public.orders drop constraint orders_user_id_not_null;

-- 005: index without blocking writes. CANNOT run inside a transaction block,
--      so this is its own migration file with the DDL transaction disabled.
create index concurrently if not exists orders_user_id_idx on public.orders (user_id);
```

Put `set lock_timeout = '3s';` at the top of **every** DDL migration so a bad one fails fast instead of holding the table. Watch for pileups while it runs:

```sql
select pid, state, wait_event_type, left(query,80), now()-query_start as age
from pg_stat_activity where wait_event_type = 'Lock' order by age desc;
```

If step 003 leaves orphan rows with no derivable owner, that is a **product decision** — assign to a system account, soft-delete, or accept that they become invisible. Make it explicitly, before enabling RLS, not after users report missing data.

Pre-flight grep for the blocking forms:

```bash
grep -rniE "not null|references|default (now|gen_random_uuid|uuid_generate)" supabase/migrations/
grep -rni "create index" supabase/migrations/ | grep -vi concurrently
```

### Step 4 — Ship the policies-first transaction

Policies **before** the switch, so there is never a default-deny window. All in one transaction, per command, with the role named.

```sql
begin;

create policy "orders_select_own" on public.orders
  for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "orders_insert_own" on public.orders
  for insert to authenticated
  with check ((select auth.uid()) = user_id);

create policy "orders_update_own" on public.orders
  for update to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "orders_delete_own" on public.orders
  for delete to authenticated
  using ((select auth.uid()) = user_id);

alter table public.orders enable row level security;

-- Grants are a separate gate from policies. Supabase, verbatim: "Adding policies
-- doesn't take those grants back."
revoke all on public.orders from anon;

commit;
```

RLS is **per-command**: a `for select` policy alone leaves `INSERT`/`UPDATE`/`DELETE` governed by nothing but grants — which is why the three write policies and the `revoke` are not optional (`BAAS-07`).

`(select auth.uid())` rather than `auth.uid()` is a **performance requirement, not style**, and performance is a security control here because a timeout is what causes the fix to be reverted. Supabase's own benchmarks: unwrapped `auth.uid() = user_id` at 179 ms drops to 9 ms wrapped; a role check inside a policy goes from **11,000 ms to 7 ms**; a policy joining a `team_user` table goes from **178,000 ms to 12 ms** when the join moves into a `security definer` helper; a btree index on the policy column moves 171 ms to under 0.1 ms, "Improvement seen over 100x on large tables."

```sql
-- The joined-policy form, rewritten so it does not time out.
create index if not exists documents_team_id_idx on public.documents using btree (team_id);
create index if not exists team_user_user_id_idx  on public.team_user using btree (user_id);

create or replace function public.current_user_team_ids()
returns setof uuid language sql stable security definer set search_path = '' as $$
  select team_id from public.team_user where user_id = auth.uid()
$$;

create policy "team members can read" on public.documents
  for select to authenticated              -- skips evaluation for anon entirely
  using ( team_id in (select public.current_user_team_ids()) );
```

Caveat that must ship with that fix: a `security definer` function **bypasses RLS on the tables it reads**, so it must not accept caller-controlled arguments and must pin `search_path` (`BAAS-06`).

Measure before you cut over:

```sql
alter role authenticator set pgrst.db_plan_enabled to true;
notify pgrst, 'reload config';
```
```js
await supabase.from('documents').select('*').explain({ analyze: true })
```
```bash
grep -rnE "auth\.(uid|jwt|role)\(\)" supabase/migrations/ | grep -v "select auth\."
```

### Step 5 — Verify from outside, with the anon key

**"The app still works after I enabled RLS" is a false pass.** The page may render because the route fetching it uses the service-role / `sb_secret_` key, which carries `BYPASSRLS` (`AUTHZ-08`).

The inverse bites too, and it is documented: a service key bypasses RLS **only when the request carries no user access token**. *"If the request carries one, it runs under the RLS policies of that signed-in user."* So admin, export and cron routes that forward the end user's `Authorization` header silently become user-scoped mid-migration and start returning empty.

```bash
# Should return [] or 401, never rows.
curl -s "$SUPABASE_URL/rest/v1/orders?select=*" -H "apikey: $ANON_KEY" | head -c 300
# Write path is a separate gate — check it too.
curl -s -X PATCH "$SUPABASE_URL/rest/v1/orders?id=eq.1" -H "apikey: $ANON_KEY" \
  -H "Content-Type: application/json" -d '{"status":"paid"}' -i | head -5
# Inventory every service-key call site before you trust a green dashboard.
grep -rn "SERVICE_ROLE\|sb_secret_\|SUPABASE_SECRET" --include="*.ts" --include="*.js" app/ lib/ api/
```

And the correct end-state shape, two clients deliberately kept separate:

```ts
// lib/db.ts — server-only DAL
import 'server-only'
import { createServerClient } from '@supabase/ssr'
import { createClient } from '@supabase/supabase-js'
import { cookies } from 'next/headers'

export async function userDb() {                       // RLS applies
  const cookieStore = await cookies()
  return createServerClient(process.env.SUPABASE_URL!, process.env.SUPABASE_PUBLISHABLE_KEY!, {
    cookies: { getAll: () => cookieStore.getAll() },
  })
}

// Admin client: no user token attached, and every query re-adds the filter by hand,
// because RLS is switched off for this connection.
const admin = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY!,
  { auth: { persistSession: false } })

export async function listOrdersForUser(userId: string) {
  const { data } = await admin.from('orders').select('*').eq('user_id', userId)  // manual authz
  return data
}
```

### Step 5b — The Firebase variant of this cutover

Two documented hazards, both specific to live rules changes:

1. **The CLI is destructive by design.** *"When you deploy security rules using the Firebase CLI, the rules defined in your project directory overwrite any existing rules in the Firebase console."* If anyone hot-fixed a rule in the console — very common on a vibe-coded app — the next `firebase deploy` reverts that fix without warning.
2. **There is no staged rollout for rules.** *"Firebase Security Rules releases take a period of several minutes to fully propagate."* During those minutes some requests are still evaluated against the old ruleset. If your plan is "tighten rules, then immediately delete the client-side workaround," you have created a window where neither control is in place.

```
// The fix shape — per-operation, comparing against resource, not just checking login.
match /orders/{orderId} {
  allow read, delete: if request.auth.uid == resource.data.owner_uid;
  allow create:       if request.auth.uid == request.resource.data.owner_uid;
  allow update:       if request.auth.uid == resource.data.owner_uid
                      && request.resource.data.owner_uid == resource.data.owner_uid;
}
```

Cutover procedure: (1) commit the **current live ruleset** to git first, so you have a known-good artifact to redeploy as rollback; (2) prove the new rules with `@firebase/rules-unit-testing` against the emulator; (3) sanity-check in the console Rules Playground, which "allows testing semantic Security Rules behavior, using your project's database"; (4) deploy at your lowest-traffic hour and **wait out the propagation window** before removing any client-side compensating control; (5) rollback = redeploy the saved ruleset, budgeting the same several minutes.

```bash
grep -nE "if true|allow read, write: if request\.auth(\.uid)? != null|request\.time <" \
  firestore.rules storage.rules database.rules.json
curl -s "https://<project>-default-rtdb.firebaseio.com/orders.json"   # rows = not migrated
```

Note the asymmetry that catches people in both directions: **RTDB rules cascade downward and a child rule cannot revoke a parent grant**, while **Firestore rules do not cascade to subcollections at all**. And Firebase Storage's canonical starting ruleset (`allow read, write: if request.auth != null` over `{allPaths=**}`) *is* the anti-pattern (`BAAS-03`) — teams harden `firestore.rules` and leave every uploaded ID document world-writable.

### Step 6 — Credential rotation while live, without logging everyone out

Rotate in dependency order — the thing that grants access to other things first:

1. Cloud/platform account credentials and any CI/CD token that can redeploy (a compromised deploy token re-poisons everything downstream).
2. Database credentials and service-role / admin API keys.
3. Paid third-party keys (LLM, Twilio, email) — **revoke**, do not merely create-new.
4. Webhook signing secrets.
5. **Session / JWT signing material last**, because that is the step with user-visible consequences.

The JWT step is where live apps get hurt. Supabase contrasts the two systems directly: rotating the **legacy** JWT secret means *"Currently active users get immediately signed out"* — on a live consumer app that logs out your entire user base mid-session, including anyone in checkout. With the asymmetric **signing keys** system, *"No users get signed out."*

But rotating the new-style key and stopping contains nothing: *"Rotation only changes the key used by Supabase Auth to create new JWTs, but the trust relationship with both keys remains. Non-expired access tokens will remain to be accepted."*

```
1. Create a new key → it enters `standby`.
2. Rotate → new key becomes `current`, old becomes `previously used`. Nobody is signed out.
3. Deploy any code that verifies via JWKS, so it picks up both keys.
4. Wait out the access-token TTL plus a buffer. Supabase's own example: "If your access
   token expiry time is configured to be 1 hour, wait at least 1 hour and 15 minutes
   before revoking the legacy JWT secret."
5. Revoke the previously-used key. Only now are stolen tokens dead.
6. If you need IMMEDIATE eviction (confirmed compromise), do a global sign-out BEFORE
   step 5 — do not shorten the wait instead.
```

This is safe to do live because it is reversible: *"At any point you can move a key from the previously used or revoked states back to being a standby key, and rotate to it."*

**Rotating keys is not the same as revoking sessions.** An attacker sitting in your Supabase or Stripe dashboard simply copies the new key. Revoke delegated access explicitly: GitHub Settings → Access → Sessions, and Settings → Integrations → Applications (**both** the OAuth and GitHub Apps tabs); Supabase account security (enabling MFA force-logs-out all sessions); `vercel.com/account/tokens`; Stripe API keys → Expire key, or Rotate with Expiration = Now (Stripe's default rotation keeps the old key working up to 7 days); Google device sign-out plus `myaccount.google.com/linkedapps`. On Supabase, *"If you do not Revoke the key, older keys will still be valid."*

```bash
# Confirm which key a live token was actually signed by.
echo "$JWT" | cut -d. -f2 | base64 -d 2>/dev/null
```

### Step 7 — Retire the vulnerable build. Shipping the patch does not stop the old one serving

Vercel **Skew Protection** pins already-loaded clients to the deployment that served them, via a `?dpl=` query parameter, an `x-deployment-id` header, or a `__vdpl` cookie. It is **on by default** for projects created after 19 November 2024 on Next.js, SvelteKit, Nuxt, Astro and Qwik; the default Maximum Age is one day from deployment creation; and *"Vercel automatically adjusts the maximum age to 60 days for requests from Googlebot and Bingbot."*

Consequence: after you deploy the fix for an IDOR in a route handler, **an attacker who saved the old deployment ID keeps reaching the unpatched build.** Vercel documents the remedy in one sentence most people never read: *"If a deployment has a bug or security issue, you can set a threshold to stop it and any deployments older than it from serving requests to active clients."*

```bash
# 1. Ship the fix.
vercel deploy --prod

# 2. THEN retire every older build, in the dashboard:
#    Deployments → select the fixing deployment → (…) → "Skew Protection Threshold" → Set

# 3. Verify the old build is dead.
curl -s -o /dev/null -w '%{http_code}\n' "https://yourapp.com/api/orders/2?dpl=<OLD_DEPLOYMENT_ID>"
#    expect 404, not 200

# 4. For a severe hole, delete the vulnerable deployments outright —
#    deleted deployments are not reachable through Skew Protection at all.
```

Three adjacent traps before you rely on rollback as containment:

- **Instant Rollback does not restore environment variables** — "Vercel won't update environment variables if you change them in the project settings and will roll back to a previous build." Rolling back after a key rotation gives you old code plus a new key, which usually means a hard outage.
- It **does** revert cron jobs to the rolled-back deployment's state.
- Afterwards, *"Vercel turns off auto-assignment of production domains"* — so your **next** push, the real fix, silently does not go live until you `vercel promote` or click Undo Rollback. Roll back at 2am, push a fix at 3am, spend an hour wondering why it has no effect.

On the **Hobby** plan there is no custom skew maximum age and Instant Rollback is limited to the immediately previous deployment. The fallback is: delete the vulnerable deployments, and redeploy manually.

### Step 8 — Canary the authorization change on the right signal

For an authz change the meaningful canary signal is **not the error rate**. It is the **403/404 rate on routes that previously returned 200**, because a policy that is too tight looks identical to a policy that is working.

Ship the new check in **report-only mode first**: evaluate it, log `authz_would_deny`, allow the request. Read a day of output, then flip to enforce. That is the only way to discover the access patterns your policy forgot without discovering them via support tickets.

Where the platform offers a traffic split (Vercel rolling releases, Pro/Enterprise):

```bash
vercel rolling-release configure --cfg '{"enabled":true,"advancementType":"manual-approval",
  "stages":[{"targetPercentage":10,"duration":5},{"targetPercentage":50,"duration":10},{"targetPercentage":100}]}'
vercel deploy --prod
vercel rolling-release start --dpl <url>
vercel logs --environment production --level error --since 5m
vercel rolling-release abort --dpl <url>        # reverts all traffic
```

`?vcrrForceStable=true` and `?vcrrForceCanary=true` let you hit either build deliberately — which is how you verify that the *canary* denies the cross-tenant request while the stable one still allows it. On Hobby, the fallback is a manual staged deploy plus report-only enforcement.

### Step 9 — Notification obligations start at "aware", not at "confirmed"

**GDPR Art. 33:** notify the supervisory authority *"without undue delay and, where feasible, not later than 72 hours after having become aware of"* a personal data breach, unless it is unlikely to result in a risk to rights and freedoms. Late notice must carry *"reasons for the delay."* **Phased notification is explicitly permitted** — *"where and in so far as it is not possible to provide the information at the same time, the information may be provided in phases without undue further delay."* And **every** breach must be documented internally, including ones you decide not to report, "to enable the supervisory authority to verify compliance."

**GDPR Art. 34:** direct notification of affected individuals when the breach is *"likely to result in a high risk,"* in *"clear and plain language,"* with three exceptions — data rendered *"unintelligible to any person who is not authorised to access it, such as encryption"*; subsequent measures ensuring the high risk *"is no longer likely to materialise"*; or *"disproportionate effort,"* in which case a public communication of equal effectiveness is substituted.

**United States:** no single federal analogue for a generic app, and no jurisdiction where you are off the hook. Per NCSL, *"All 50 states, the District of Columbia, Guam, Puerto Rico and the Virgin Islands have laws requiring private businesses, and in most states, governmental entities as well, to notify individuals of security breaches"* — **54 jurisdictions**, each with its own definition of personal information, timing, and encryption safe harbour.

> **The cheapest thing you can do in an incident:** the moment personal-data exposure is confirmed, start a written timeline — discovery time, exposure window, tables and columns, row counts, evidence preserved or missing. That document **is** the Art. 33(5) record, it is what makes phased notification defensible, and it costs ten minutes if started immediately and is unrecoverable if started a week later.

Also notify the provider whose service was abused (Twilio asks for reports at fraud@twilio.com).

The operator usually does not think of themselves as a data controller. A hobby project with 300 signups and EU users is one — GDPR has no size threshold.

---

## 5. Sequencing, and the specific danger of an agent-driven refactor

### Six deployments, not one

The fixes in this file are individually safe and collectively lethal if shipped as one commit. The rule: **each step must be revertible without undoing the previous step.**

| # | Deployment | Reverting it does NOT undo |
|---|---|---|
| 1 | Schema change (nullable column) | — |
| 2 | Backfill | the column |
| 3 | Constraints (`NOT VALID` → `VALIDATE`) | the data |
| 4 | Policies + `enable row level security` + `revoke` | the constraints |
| 5 | Application code | the policies |
| 6 | Key rotation | anything — and code revert does not un-rotate a key |

Two asymmetries worth stating to the user in plain language: **reverting code does not un-rotate a key, and reverting a migration does not un-leak data.** That is why the sequence is fixed and why "just ship it all at once and see" is the failure mode, not the shortcut.

### Feature flags and staged rollout

- Put the *new* behaviour behind a flag, not the old one, so the default on failure is the state you already know works.
- On Next.js, a per-path kill switch that needs no redeploy: route the decision through `proxy.js` — `if (pathname === '/your-path' && myFeatureFlag.isEnabled()) return NextResponse.rewrite(...)`.
- Report-only before enforce, always, for anything authorization-shaped (§4 Step 8).
- Watch 403/404 on previously-200 routes, not the error rate.

### Letting an AI agent do a large refactor on a live app

This is the single most dangerous actor in the migration, and it fails in three distinct ways.

**(a) Data loss.** Prisma states it flatly, twice: *"`migrate dev` is a development command and should never be used in a production environment,"* and the same sentence for *`migrate reset`*. `migrate dev` drops and recreates the database when it detects drift — and an insecure legacy app **always** has drift, because its schema was clicked into a dashboard rather than migrated. Supabase draws the matching line: *"Never change the remote database directly. Once you're using migrations, all schema changes — even small ones — should go through migration files."*

```bash
grep -rn "migrate dev\|migrate reset\|db push\|db reset\|--force-reset" \
  package.json .github/ scripts/ Makefile
supabase projects list && cat supabase/.temp/project-ref   # confirm what is linked
```

```json
{
  "scripts": {
    "db:migrate:dev":  "prisma migrate dev",
    "db:migrate:prod": "node -e \"if(!process.env.CI) {console.error('CI only'); process.exit(1)}\" && prisma migrate deploy"
  }
}
```

The enforcement a natural-language instruction cannot provide, at the database layer: the application role gets DML only and **no `CREATE`/`DROP`/`TRUNCATE`**; migrations run in CI under a separate role; the agent is pointed at a branch database, never the linked production project.

**(b) Silent behaviour change.** Asked to "enable RLS on my tables," an agent that runs the `alter table` statements and stops has done exactly what was asked. When the user returns with "the app is broken now," the cheapest repair satisfying the new constraint is `using (true)` — `BAAS-02`. Nothing in the conversation carries the invariant *"and it must still deny other users' rows,"* because that invariant was never expressed as a test. **Express it as a test, or it will not survive.**

**(c) Authorization checks dropped during the rewrite.** When an agent refactors twelve route handlers to "use the new DAL," the diff is large, mechanical, and reviewed by nobody. An `.eq('user_id', session.user.id)` that existed in route #9 does not survive — and the test suite still passes, because the test authenticates as the owner.

```ts
// tests/authz.spec.ts — the regression net for any AI refactor
for (const route of PROTECTED_ROUTES) {
  it(`${route} denies a non-owner`, async () => {
    const res = await fetch(route, { headers: { cookie: sessionFor('mallory') } })
    expect([401, 403, 404]).toContain(res.status)
  })
}
```

Diff-review rule for refactor PRs — every removed line matching this needs a stated reason:

```bash
git diff --unified=0 | grep -E "^-.*(auth|session|user_id|owner|role|can[A-Z])"
```

### The Lane A footgun: a matcher change silently drops Server Function coverage

This is the sharpest instance of (c), and it produces an auth bypass **with no visible change to the action itself.**

Next.js's own `proxy.ts` reference states that Server Functions are not separate routes in the chain: they *"are handled as POST requests to the route where they are used, so a Proxy matcher that excludes a path will also skip Server Function calls on that path."* And the warning that follows is the one that bites an agent-refactored codebase: *"A matcher change or a refactor that moves a Server Function to a different route can silently remove Proxy coverage. Always verify authentication and authorization inside each Server Function rather than relying on Proxy alone."*

So: an agent that moves an action file, or tightens a matcher to fix an unrelated performance complaint, can strip the only auth check. No error. No diff in the action. The tests pass. This is why **middleware is not a security boundary** (`AUTHZ-03`) is a structural claim and not just a CVE story — though the CVEs exist too: CVE-2025-29927 (CVSS 9.1, `x-middleware-subrequest` skips middleware entirely), CVE-2026-64642 (App Router built with Turbopack plus exactly one entry in `config.i18n.locales`; fixed 16.2.11 / 15.5.21), and CVE-2026-41248 (Clerk's own documented `createRouteMatcher` pattern).

Two operational consequences:

1. **Authorization is re-checked inside every Server Function and route handler**, not only at the perimeter. Also note that as of Next.js 16.0.0 the convention is renamed `middleware.ts` → `proxy.ts`, so every "grep for middleware" instruction must grep both.
   ```bash
   grep -rn "matcher" middleware.ts proxy.ts src/middleware.ts src/proxy.ts 2>/dev/null
   grep -rln "'use server'" app/ src/ 2>/dev/null   # every one of these is a public POST endpoint
   ```
2. **Route inventory diffed against the test matrix** on every commit, so "one route missed the guard" is a failing build rather than a discovery. An exported `'use server'` function *"is reachable via a direct POST request, not just through your application's UI"* — a page-level `redirect()` guard does not protect the action defined in the same file.

### The re-audit triggers that apply during a migration

Re-run the authz differential test when: an agent refactors routing, middleware or `proxy.ts`, or moves a Server Function; a new table appears in an exposed schema; a service-role call site is added; or a policy is edited. (This trigger taxonomy is the dossier researcher's, not a cited standard.)

---

## 6. Stage E — the live incident path

Reachable from any stage, and out of band from all of them.

> ### The hard rule
>
> **When the user says they think they are being attacked right now, the first thing you do is preserve evidence — not patch, not rotate, not reassure.**
>
> Export platform request logs, database logs, auth logs, email-provider send logs and the billing/usage timeline **off-platform**, for the exposure window, before anything else. Five minutes. Rotation and redeployment both destroy the audit trail that tells you scope, and retention on these plans is measured in hours on some tiers (Vercel Hobby retains runtime logs for **one hour**, and log drains are Pro/Enterprise only). Once it is gone it does not come back, and the absence itself becomes the finding that drives your notification obligations.
>
> The one exception: if money is actively leaving the account or data is actively being deleted, contain first — but contain with a control that *adds* log lines rather than erasing them (a WAF deny rule, Attack Mode, the `revoke` tourniquet), never by tearing down the project.

### The order: preserve → contain → patch → rotate → scope → notify

It is the reverse of what people do.

| # | Step | The failure if you skip or reorder |
|---|---|---|
| 1 | **Preserve** — export logs from every provider for the exposure window | You cannot scope, so you cannot notify accurately, so you must assume worst case |
| 2 | **Contain** — WAF deny rule, Attack Mode, pause the project, disable the abused integration, or `revoke all on public.<table> from anon;` | Rotating before containing lets the attacker re-harvest while you deploy |
| 3 | **Revoke sessions and delegated grants** — *not the same as rotating keys* | An attacker sitting in your dashboard copies the new key the moment you make it |
| 4 | **Rotate in dependency order** — platform and CI tokens → DB and service-role keys → paid third-party keys (revoke, not re-issue) → webhook secrets → **JWT signing material last** | Rotating signing material first logs out every user mid-incident; revoking sessions before rotating the signing key lets issued tokens survive |
| 5 | **Patch, then retire the vulnerable deployments** (§4 Step 7) | The old build keeps serving via `?dpl=` |
| 6 | **Scope from the preserved logs** — which rows, which accounts, what window, which IPs | You quantify after you publish, which is the worst order |
| 7 | **Notify** against the three deadlines in `breach-runbook.md`: **72 hours** (GDPR Art. 33), **60 days** (16 CFR 318.4), **45 days** deletion (RCW 19.373) | The clock started at "aware," not at "confirmed" |
| 8 | **Post-mortem** — timeline, root cause, what detection would have caught it sooner, one concrete control added | — |

Exact click paths for step 3: GitHub Settings → Access → Sessions, and Settings → Integrations → Applications (**both** the OAuth and GitHub Apps tabs); Supabase account security (enabling MFA force-logs-out all sessions); `vercel.com/account/tokens`; Stripe API keys → Expire key, or Rotate with Expiration = Now; Google device sign-out plus `myaccount.google.com/linkedapps`.

**Confidence note to carry forward:** the provider mechanics above are CONFIRMED; **the ordering itself is synthesized best practice rather than a single cited standard.**

### If an AI-CLI-adjacent compromise is suspected

Rotate npm tokens, GitHub PATs, SSH keys and every cloud key, and rebuild the machine. IoC hunt:

```bash
ls -la /tmp/inventory.txt /tmp/CLEANER.LOG /tmp/osalogging.zip 2>/dev/null
find . -name "setup_bun.js" -o -name "bun_environment.js" -o -name "shai-hulud-workflow.yml"
gh repo list --limit 200 --json name --jq '.[].name' | grep -E '^[0-9a-z]{18}$'
gh api repos/:owner/:repo/actions/runners
```

Note which parts of that story are confirmed and which are press reporting: for Amazon Q, **CVE-2025-8217, affected version 1.84.0, fix 1.85.0, and the over-scoped CodeBuild token root cause are confirmed from the AWS bulletin**; the "system cleaner" wording, the `/tmp/CLEANER.LOG` path and the ship/pull dates are **press reporting, not in the advisory**. The Nx s1ngularity affected versions are a **discrete set, not a contiguous range**: nx 20.9.0 / 20.10.0 / 20.11.0 / 20.12.0 / 21.5.0 / 21.6.0 / 21.7.0 / 21.8.0 plus specific `@nx/*` versions.

### Readiness test, to run before you need it

Can you name, right now, every place a secret lives — platform env vars, `.env.local`, CI secrets, the AI coding tool's config, a Discord message, a screenshot? If not, you cannot complete step 4.

---

## What this file deliberately does not claim

- **The "shadow mode" RLS preflight in §4 Step 2 is an engineered procedure, not a product feature.** No vendor ships an RLS dry-run. Present it as a technique.
- **Supabase per-plan log retention numbers** are not stated on the logging docs page. Check the pricing page; do not guess a number.
- **Vercel rolling releases and a custom Skew Protection maximum age are Pro/Enterprise.** Hobby gets neither, and Instant Rollback there is limited to the immediately previous deployment. Always give the Hobby fallback.
- **No postmortems of botched security migrations are cited**, because none could be verified. The failure modes above are derived from vendor documentation and PostgreSQL semantics, not from named incidents.
- **`prisma db push` in production** is a well-known risk but the verbatim warning could not be confirmed the way the `migrate dev` / `migrate reset` ones were. Quote only the two that are confirmed.
- **Firebase test-mode's 30-day expiry** is not confirmed by the page consulted. The `request.time < timestamp.date(...)` pattern is real and greppable; the "30 days" figure is not claimed here.
- **Firestore's `diff().affectedKeys().hasOnly([...])` idiom** is REPORTED, not confirmed. The explicit `request.resource.data.x == resource.data.x` form used above **is** documented — prefer it.
