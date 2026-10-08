# Incident response, and running a public "try to hack this" challenge safely

Load this file in three situations. **First**, when the user suspects or has confirmed a breach, a leaked credential with real privilege, or an active abuse event — go straight to Part 1 and do not let them patch first. **Second**, when the user is planning to publicly invite attack on something they built — Part 2, *before* it goes live, not after the first report arrives. **Third**, when an audit has cleared the app itself and the remaining exposure is the layer above it: the builder's own email, registrar, GitHub, hosting and payment accounts, the laptop those accounts are logged into, and whether anyone is watching the app at all six months from now — Part 3. Parts 1 and 3 are also the correct load when the honest answer to "were we already hacked?" is "there is no way to know," which is `OPS-01` and is the most common answer.

---

# Part 1 — The incident runbook

## The framing: `OPS-01`, nobody can tell whether it already happened

This is the meta-killer, and it changes what every other finding means.

Documented log retention on the platforms this population uses:

| Platform / plan | Runtime log retention |
|---|---|
| Vercel Hobby | **1 hour** |
| Vercel Pro | 1 day |
| Vercel Pro + Observability Plus | 30 days |
| Vercel Enterprise | 3 days |
| Supabase | *"Log retention is based on your project's pricing plan"* — Supabase publishes no number on the logging docs page. Check your own pricing page; do not assume one. |
| GitHub personal security log | 90 days |
| GitHub org audit log | 180 days |

Log **drains** — the mechanism for shipping logs somewhere durable — are *"available to all users on the Pro and Enterprise plans."* A Hobby project cannot configure one at all.

Consequence: **an IDOR exploited over a weekend and discovered on Tuesday has zero forensic evidence.** Meanwhile the attack clock is not generous — Unit 42's honeypots were compromised within 24 hours, Postgres instances within 30 seconds, and a GitHub-leaked AWS key is abused in about a minute. There is no grace period between deploy and scan, and no evidence afterwards.

The operational rule that follows: **if you cannot reconstruct the exposure window, treat the finding as maximum scope.** Absence of evidence of access is not evidence of no access, and every notification decision downstream has to be made on that basis.

**The correct order is: preserve evidence → contain → rotate → revoke sessions → scope → patch → notify → post-mortem.** Almost everyone patches first, which destroys the only signal that drives the notification decision. Rotating before containing lets the attacker re-harvest the new credential. Revoking sessions before rotating the signing key lets already-issued tokens survive.

> **Confidence note, carried from the dossier:** the provider mechanics quoted throughout Part 1 are CONFIRMED from vendor documentation. **The ordering itself is synthesized best practice `[R]`**, not a single cited standard. Nothing here is legal advice; the notification deadlines are quoted from the regulations, but whether they apply to you is a question for counsel.

---

## Step 0 — Preserve evidence. Five minutes, before you touch anything.

Rotation destroys the audit trail. Patching destroys the reproduction. Retention is measured in hours. Do this first.

Export, **off-platform**, for the widest window you can still reach:

- platform request logs (Vercel, Netlify, Cloudflare)
- database / API logs (Supabase edge logs, Postgres logs)
- auth logs (sign-ins, password resets, MFA changes)
- email-provider send logs
- your billing and usage timeline (the spend curve often dates the incident better than the logs do)
- provider audit logs for the accounts themselves (GitHub security log, team member changes)

**Supabase edge logs carry exactly the fields you need**: `request.headers.cf_connecting_ip`, `request.path`, `request.method`, `request.headers.user_agent`, `response.status_code`. In the Logs Explorer:

```sql
select timestamp,
       log_attributes['request.path']                     as path,
       log_attributes['request.method']                   as method,
       log_attributes['request.headers.cf_connecting_ip']  as ip,
       log_attributes['request.headers.user_agent']       as ua,
       log_attributes['response.status_code']             as status
from edge_logs
where timestamp > now() - interval '7 days'
  and log_attributes['request.path'] like '/rest/v1/orders%'
  and log_attributes['response.status_code'] = '200'
order by timestamp desc;
```

Signals that mean **assume breach**:

- bulk `GET /rest/v1/<table>?select=*` with no `limit`, from an IP that is not your app's egress
- any `PATCH` or `DELETE` on a table your UI never writes to
- a burst of `/auth/v1/signup` followed immediately by reads
- a `user_agent` of `python-requests`, `curl`, `Go-http-client`, or a raw Postgres client string

Save the exports somewhere the compromised credentials cannot reach — a local directory, a different cloud account. Then start a plain-text timeline file in the same directory and append to it as you work. That file is not bureaucracy; see Step 6.

**If the logs for the exposure window are already gone, that absence IS the finding.** Write it down in those words. It drives the notification decision, and it is the single strongest argument for fixing `OPS-01` before the next incident.

---

## Step 1 — Contain, without a full outage if you can

Containment is about stopping re-harvest during the minutes you spend rotating. Cheapest effective control first.

| Situation | Containment action | Notes |
|---|---|---|
| Any live abuse on Vercel | **Attack Mode** | Free on all plans. Challenges all traffic, auto-allows verified bots and your own Functions/Cron. Blocked requests do not count toward usage. (Docs used to call it "Attack Challenge Mode"; the URL now redirects to `attack-mode`.) |
| A specific path or IP is being hit | WAF **Deny** rule for that path/IP | Narrow, fast, revertible. |
| A Supabase table is readable through PostgREST that should not be | `revoke all on public.<table> from anon;` | The one-line tourniquet. Stops PostgREST reads **without a deploy and without logging anyone out**. Instantly revertible. |
| A paid integration is being abused (Twilio channel, LLM key) | Disable the integration or the key at the provider | Do this before anything slower — it is the meter that is running. |
| The application build itself is the leak | Pause the production deployment | Accepts `503 DEPLOYMENT_PAUSED`. Remember that paused projects must be resumed **manually, one by one**. |

```sql
-- Tourniquet, and its exact reversal. Run the revoke now; keep the grant handy.
revoke all on public.orders from anon;
-- reverse once the real policy is in place:
-- grant select on public.orders to anon;
```

---

## Step 2 — Rotate in dependency order

Rotate the thing that grants access to other things **first**. A compromised deploy token re-poisons everything downstream, so rotating the database password while the attacker still holds the CI token accomplishes nothing.

1. **Cloud/platform account credentials and any CI/CD token that can redeploy.**
2. **Database credentials and service-role / admin API keys.**
3. **Paid third-party keys** (LLM, Twilio, email) — **revoke**, do not merely create-new.
4. **Webhook signing secrets.**
5. **Session / JWT signing material — last**, because it is the step that logs everyone out and you want that to be the final cut.

### Rotation is not complete without revocation

This is the most common half-done step in the entire runbook.

- **Supabase:** *"If you do not Revoke the key, older keys will still be valid."*
- **GitHub:** *"It is not sufficient to simply remove the secret from your codebase. The most important remediation step is revoking the secret with the secret's provider."*
- **AWS:** create-new → deploy → verify the old key is idle in CloudTrail → **deactivate** → **delete**. Never delete first.

### Provider-specific rotation

| Provider | What to do | The trap |
|---|---|---|
| **Stripe** | Roll the secret key in the dashboard. Rotation keeps old and new working for **up to 7 days** — that is a feature, use it rather than a hard cutover. For webhook endpoint secrets, Stripe documents rolling with a delayed expiry of *"up to 24 hours… During this time, multiple secrets are active for the endpoint. Stripe generates one signature per secret."* | Publishable keys cannot be expired. If your remediation is "rotate `pk_live_`," you have fixed nothing. |
| **Supabase — API keys** | Create new `sb_secret_` key, deploy, then **Revoke** the old one explicitly. | Legacy `anon`/`service_role`/JWT-secret keys can no longer be rotated at all — the path is migration to publishable/secret keys. Regenerating the legacy JWT secret severs every existing connection. |
| **Supabase — signing keys** | Staged lifecycle: `standby → current → previously used → revoked`. *"Each action on a key is reversible (except permanent deletion)."* | See Step 3 — rotating alone does not sign anyone out. |
| **AWS** | create-new → deploy → verify idle via CloudTrail → deactivate → delete. | Deleting first breaks production and gives you no rollback. |
| **OpenAI / Anthropic** | Create new key, deploy, delete old. Keys are unrecoverable once deleted. | Set per-key spend limits while you are in there. |
| **Vercel tokens** | Delete the token; recreate scoped: `vercel tokens add --project <PROJECT_ID>` with an expiration. | A default Vercel token is **Full Account** scope — see Part 3. |
| **GitHub** | Revoke the PAT at `github.com/settings/tokens`; delete deploy keys; rotate every Actions secret. | Deploy keys have no expiry and are tied to the repo, not to you. |

### Rotate a webhook secret without downtime

Models write `process.env.STRIPE_WEBHOOK_SECRET` — singular — which makes rotation structurally a hard cutover. Accept a list instead:

```ts
// app/api/webhooks/stripe/route.ts
const SECRETS = (process.env.STRIPE_WEBHOOK_SECRETS ?? '').split(',').filter(Boolean);

function constructEventAnySecret(body: string, sig: string) {
  for (const secret of SECRETS) {
    try { return stripe.webhooks.constructEvent(body, sig, secret); } catch { /* try next */ }
  }
  throw new Error('no configured webhook secret verified this signature');
}
```

Then: (1) roll the secret in the Stripe dashboard choosing the delayed expiry; (2) set `STRIPE_WEBHOOK_SECRETS=<new>,<old>` and deploy; (3) confirm events verify against the new secret; (4) set `STRIPE_WEBHOOK_SECRETS=<new>` and deploy again. Keep the signature tolerance at the library default of 5 minutes — Stripe warns explicitly, *"Don't use a tolerance value of `0`. Using a tolerance value of `0` disables the recency check entirely."*

### The false positive that wastes an incident

**Do not "rotate" a publishable key as remediation.** `NOTVULN-01` (Supabase publishable/anon key), `NOTVULN-02` (Firebase web config), `NOTVULN-03` (`pk_live_`, Clerk publishable) and `NOTVULN-04` (PostHog `phc_`, Sentry DSN, Mapbox `pk.`) were never secret and rotating them changes nothing about the attack.

| Looks like | True positive | Common false positive |
|---|---|---|
| Supabase key in the bundle | `sb_secret_` / `service_role` key in client code or in git history → real, rotate and revoke | `sb_publishable_` / anon key in the bundle → by design. The finding is the missing RLS (`BAAS-01`), and rotating the key leaves it unfixed |
| Firebase `apiKey` in the bundle | A Gemini API key, which shares the `AIza` prefix and must never ship to a client | The Firebase web config, which is public by design. Check what the key is *restricted to*, not its prefix |
| `sb_secret_` returning 401 to a browser | Nothing — the 401 is a guardrail against accidents, not a mitigation, and equally not evidence the key is safe (`NOTVULN-06`) | Reporting the 401 as "protected" |

The dossier's own fact-checkers caught this exact error in the research: the **Moltbook** incident was filed under leaked secrets when the exposed credential was an `sb_publishable_` key that was supposed to be there. It is a **missing-RLS case, not a leaked-secret case.** In a live incident this misclassification is expensive, because the visible key looks like the answer and the real hole stays open.

---

## Step 3 — Invalidate sessions and refresh tokens correctly

The nuance that makes rotate-and-stop wrong, in Supabase's words: *"Rotation only changes the key used by Supabase Auth to create new JWTs, but the trust relationship with both keys remains. Non-expired access tokens will remain to be accepted."*

**Safe order for a routine rotation (no users signed out):**

1. Create the new key as **standby**.
2. Rotate standby → **current**. Per the docs, *"no users will be forcefully signed out."*
3. Deploy code that verifies against JWKS (`/auth/v1/.well-known/jwks.json`).
4. Wait the access-token TTL **plus buffer**. Supabase's own example: *"If your access token expiry time is configured to be 1 hour, wait at least 1 hour and 15 minutes."* The JWKS endpoint is cached at Supabase's edge for 10 minutes — add that too.
5. **Revoke** the previous key. This is the step that actually ends trust in it.

**For a confirmed compromise, do a global sign-out *before* the revoke** rather than shortening the wait. Accept that everyone is logged out; that is the point.

Two contrasts worth holding in your head:

- Rotating the **legacy** JWT secret *does* sign everyone out immediately (*"Currently active users get immediately signed out"*).
- Rotating an **asymmetric signing key** does not. Same word, opposite behaviour.

Supabase session mechanics you can rely on while scoping: access tokens are short-lived JWTs (5 minutes to 1 hour) and *"already-distributed access tokens remain technically valid until expiration"*; sign-out *"removes the sessions affected by the logout from the database entirely"*; refresh tokens are single-use with a 10-second reuse interval, and unauthorized reuse means *"the whole session is regarded as terminated and all refresh tokens belonging to it are marked as revoked."* For security-sensitive actions the docs advise verifying the `session_id` claim against the `auth.sessions` table.

**Stolen session cookies bypass MFA entirely and are untouched by key rotation.** If the incident involved a stealer or an AiTM proxy on *your* machine (Part 3), rotating app keys does not evict the attacker from your provider dashboards — you must kill those sessions at each provider too.

---

## Step 4 — Determine scope from the preserved logs

From the Step 0 exports, answer in writing:

- **Which rows** were read or modified — table, columns, approximate row count.
- **Which accounts** were accessed.
- **Over what window** — first and last observed request.
- **From which IPs and user agents.**

Quantify before you write anything public. A public statement that later needs a correction upward is worse than a slower, accurate one.

Remember `OPS-03`: the schema is the blast radius. Harvest value is a pure function of the column list, so scope the *columns* the read primitive returned, not just the table name. A `select('*')` on a profiles table with `dob`, `phone` and `street_address` is a materially different notification than one on a table holding only display names.

---

## Step 5 — Patch, verify against the exact request that worked, and retire the vulnerable build

Do not un-pause before the fix is deployed and verified against **the same attacker request that worked**, not a similar one.

Then the step almost everyone misses: **shipping the patch is not the same as retiring the vulnerable build.**

Vercel Skew Protection pins already-loaded clients to the deployment that served them, via `?dpl=` / `x-deployment-id` / `__vdpl`. It is **on by default for projects created after 2024-11-19** on Next/SvelteKit/Nuxt/Astro/Qwik, defaults to a one-day maximum age, and Vercel *"automatically adjusts the maximum age to 60 days for requests from Googlebot and Bingbot."* An attacker who saved the old deployment ID keeps reaching the unpatched build after you deploy the fix. Vercel's own words: *"If a deployment has a bug or security issue, you can either set a Custom Skew Protection Threshold or delete the deployment to stop clients from reaching it."*

```bash
# Prove the vulnerable deployment is unreachable. Expect 404.
curl -s -o /dev/null -w '%{http_code}\n' "https://yourapp.com/?dpl=<OLD_DEPLOYMENT_ID>"
```

Adjacent traps in the same area:

- Instant Rollback does **not** roll back environment variables.
- Instant Rollback **does** revert cron jobs.
- After a rollback, *"Vercel turns off auto-assignment of production domains"* — so your next fix push silently never goes live. Check this before you announce that the patch is out.
- On Hobby, Skew Protection custom thresholds and rolling releases are not available, and Instant Rollback is limited to the immediately previous deployment. The Hobby-tier answer is: **delete the vulnerable deployment.**

Verification artifacts that actually close the finding (from the remediation table): an external `curl` with only the publishable key returning `42501` or an empty set on both `GET` and `PATCH`; a failing-then-passing deny row in the authz matrix naming method + path + attacker role; a hand-crafted webhook POST with an invalid signature returning **400**. "The app still works" is a false pass whenever a server route holds the service key — that route has `BYPASSRLS`, so pages render while nothing about the attack path changed.

---

## Step 6 — Notify. The clock starts at "aware," not at "confirmed."

This is `OPS-02`, and the framing is the whole point: the 72 hours are already running while you investigate.

**GDPR Article 33** — notify the supervisory authority *"without undue delay and, where feasible, not later than 72 hours after having become aware of"* a personal data breach, unless it is *"unlikely to result in a risk to the rights and freedoms of natural persons."* A late notification must carry *"reasons for the delay."* **Phased notification is explicitly permitted**: *"where and in so far as it is not possible to provide the information at the same time, the information may be provided in phases without undue further delay."* The notification states the nature of the breach, categories and approximate number of data subjects and records, a contact point, likely consequences, and measures taken.

**Article 33(5)** — **every** breach must be documented internally, *including the ones you decide not to report*, "to enable the supervisory authority to verify compliance."

**GDPR Article 34** — direct notification of affected individuals when the breach is *"likely to result in a high risk,"* in *"clear and plain language."* Three exceptions: the data was rendered *"unintelligible to any person who is not authorised to access it, such as encryption"*; you took subsequent measures so the high risk *"is no longer likely to materialise"*; or individual notification *"would involve disproportionate effort,"* in which case a public communication of equal effectiveness substitutes.

**United States** — no single federal analogue for a generic app, and no jurisdiction where you are off the hook. Per NCSL, *"All 50 states, the District of Columbia, Guam, Puerto Rico and the Virgin Islands"* have breach-notification laws: **54 jurisdictions**, each with its own definition of personal information, timing, and encryption safe harbour.

**Sector clocks that catch apps whose builders assume they are unregulated** (`OPS-04`): the FTC Health Breach Notification Rule sets a hard **60-calendar-day** deadline (16 CFR 318.4), and 16 CFR 318.2 expressly includes fitness, fertility, sexual health, sleep, mental health, genetic information and diet. Washington's My Health My Data adds a **45-day** deletion duty reaching *"archived or backup systems."* Under-13 users bring COPPA.

**Also notify the provider whose service was abused.** Twilio asks for reports at `fraud@twilio.com`.

> **The single cheapest thing you can do in an incident.** The moment personal-data exposure is confirmed, start a written timeline: discovery time, exposure window, tables and columns, row counts, evidence preserved or missing. That document **is** the Article 33(5) record, it is what makes phased notification defensible, and it costs ten minutes if started immediately — and is unrecoverable if started a week later.

---

## Step 7 — Post-mortem

Four sections, one page:

1. **Timeline** — from the file you started in Step 0.
2. **Root cause** — the actual defect, with its stable ID (`BAAS-01`, `AUTHZ-03`, `SECRET-01`…).
3. **What detection would have caught it sooner** — this is where `OPS-01` gets fixed, by name.
4. **One concrete control added.** One. A post-mortem that produces nine action items produces zero.

Publish it if you invited the attack (Part 2).

---

## The readiness test — run this before you need it

**Can you name, right now, every place a secret lives?** Platform env vars, `.env.local`, CI secrets, the AI coding tool's config (`~/.claude.json`, `.mcp.json`), a Discord message, a screenshot in `~/Downloads`. If you cannot, you cannot complete Step 2, and the incident will run long at exactly the wrong moment.

```bash
# Was a secret ever committed? History is compromised even if the file is gone.
git log -p | grep -iE "sk-|sk_live_|api[_-]?key|service_role|secret" | head -40

# Where do credentials sit on this machine?
grep -rnE 'ghp_|gho_|ghs_|github_pat_|vcp_|sbp_|cfut_|npm_|sk-ant-|sk_live_' \
  ~/.npmrc ~/.gitconfig ~/.git-credentials ~/.claude.json ~/.zshrc ~/.bashrc 2>/dev/null
ls -la ~/.aws/credentials ~/.npmrc ~/.git-credentials ~/.docker/config.json ~/.kube/config 2>/dev/null
```

And install the thing that makes the *next* incident investigable: your own append-only `security_events` table, where retention is yours to decide rather than the free tier's.

```sql
-- migrations/xxxx_security_events.sql
create table security_events (
  id            bigserial primary key,
  occurred_at   timestamptz not null default now(),
  event         text not null,        -- auth.login.failure | authz.denied | admin.action | payment.refund
  actor_id      uuid,                 -- null for unauthenticated
  actor_ip      inet,
  actor_country text,
  target_type   text,
  target_id     text,
  outcome       text not null,        -- allow | deny | error
  detail        jsonb                 -- NEVER tokens, passwords, cookies, PANs
);
create index on security_events (occurred_at desc);
create index on security_events (event, occurred_at desc);
-- append-only: the application role must not be able to rewrite history
revoke update, delete on security_events from app_role;
```

Keep it **90 days minimum**, 12 months if you process payments, and set a retention job so you are not holding IP addresses (personal data under GDPR) forever:

```sql
delete from security_events where occurred_at < now() - interval '400 days';
```

---

# Part 2 — Running a public "try to hack this" challenge safely

A public hack invite is **a deliberate abuse event**. It will attract every finding in this dossier, plus load testing you did not authorize, plus people who will try to pivot from the target into your real properties. Design for all three.

> **Confidence note:** the provider policies quoted below are CONFIRMED from vendor documentation. **The operational plan is synthesized `[R]`.** And the safe-harbour language in §2.8 is an offer of authorization with real legal effect — have counsel read it before it goes live. This file is not legal advice.

## 2.1 Total blast-radius isolation — a separate *account*, not a separate project

A separate project inside the same account shares billing, tokens, team membership, OAuth apps and your session cookie. That is not isolation.

| Layer | Requirement |
|---|---|
| Hosting | Separate Vercel **team** (or separate Netlify/Cloudflare account) |
| Database | Separate Supabase **organization** — new project, new keys, new database password |
| CDN / DNS | Separate Cloudflare zone where you can |
| LLM | Separate **Workspace** with its own key and its own spend limit |
| Email | Separate sending domain. A bombed sending domain must not be the one your real product uses |
| Payment | Separate payment method with a low limit — a virtual card if your bank issues them |
| Repo | Separate repository, no shared deploy keys, no shared Actions secrets |
| Identity | No shared OAuth app, no shared "Continue with GitHub" that reaches your real team |

**No shared password manager entries.** Not "a copy of the entry" — a different credential entirely. If an entry appears in both the challenge vault item and the production one, the isolation is theatre.

## 2.2 A separate registrable domain, not a subdomain

`hackme-<something>.com`. **Not** `hack.yourproduct.com`.

Cookies, CORS relaxations and Safe Browsing reputation all follow the **registrable domain**. MDN's wording on the cookie `Domain` attribute is the reason: *"if a domain is specified, then subdomains are always included."* A compromise on a subdomain of your real site is a compromise of your real site's cookie scope, and a Safe Browsing flag on the challenge host can taint the parent.

Same rule for DNS: the challenge domain should not share a zone, an API token, or a registrar account with your product. And when it is over, remove the DNS records **before** deleting the platform resources — deleting the platform resource first opens a claimable dangling-CNAME window immediately.

## 2.3 No real data, ever

Synthetic records only. Do not import production data even "anonymized."

Then **publish an explicit statement that all data is fake.** Without it, half your reports will be "PII exposure" for seed rows, and you will spend the launch week triaging noise.

## 2.4 Hard caps at the provider — caps with actions, not alerts

An alert tells you it happened. A cap with an automatic action stops it. Set the number **well under** your real pain threshold.

| Provider | The control | The documented gotcha |
|---|---|---|
| **Vercel** | Settings → Billing → **Spend Management**, with **Pause production deployment enabled** | *"Setting a spend amount does not automatically stop usage. If you want to pause all your projects at a certain amount, you must enable the option."* Also: Vercel *"checks your metered resource usage… every few minutes"* and pausing *"is not instantaneous."* Paused projects must be resumed **manually, one by one** |
| **Supabase** | Org Billing → Cost Control → **Spend Cap** (Pro plan) | The cap **does not cover** Compute, Branching Compute, Read Replica Compute, Custom Domain, provisioned Disk IOPS/Throughput, IPv4, Log Drain Hours/Events, MFA Phone, or PITR |
| **LLM vendor** | Workspace spend limit, set to a small number | Set it below the tier cap, not at it |
| **AWS** (if present) | A Budget **action** — an IAM policy or SCP deny | A notification-only budget is not a cap |

An unset spend cap is a security defect, not a billing preference — OWASP API4:2023 lists *"Third-party service providers' spending limit"* alongside execution timeouts and max upload size.

## 2.5 A rehearsed sub-60-second kill switch

Know which **single** control takes the site off the internet, and practise it **before** launch. Write the exact click path into the runbook — during an incident you will not be reading docs.

| Goal | Control | Time |
|---|---|---|
| Make it survivable | Vercel **Attack Mode** — one toggle, free on all plans | seconds |
| Make it stop | Pause the production deployment (`503 DEPLOYMENT_PAUSED`) | seconds, then manual resume |
| Make it stop, harder | Remove the DNS record | seconds to toggle, minutes to propagate |
| Cut the data layer only | `revoke all on public.<table> from anon;` | one statement |

"Tested before launch" means you actually pulled it, watched the site go down, and put it back. An untested kill switch is a plan, not a control.

## 2.6 Logging turned up, retention extended — and budgeted

You want full request logs for the whole window, firewall observability, and per-request user/IP correlation. Turn Observability up on the challenge project specifically.

Then budget for it. Vercel's own warning: *"monitoring and observability tools can accrue significant cost during high traffic events, such as load testing. You will be responsible for any accrued cost."* This is the line item that surprises people — the challenge itself was cheap and the telemetry was not.

## 2.7 Read the provider's load-testing policy before you announce

You are inviting traffic to someone else's infrastructure. Vercel's documented position:

- Tests generating **50k+ RPS** require opening a ticket with their engineering team.
- Target *your* application — dynamic routes, APIs, DB, middleware — and do not *"benchmark Vercel's CDN throughput or edge network capacity."*
- Use staged ramps, not *"instant 0→100k RPS bursts."*
- Coordinate with third-party vendors before load testing their endpoints.
- A **System Bypass rule** (Pro/Enterprise) lets authorized test IPs through — and note *"System Bypass Rules will not bypass Attack Challenge Mode."*

**Do not use "Pause System Mitigations."** It sits in the Firewall tab's overflow menu, *"pause[s] all automatic mitigations for that project for the next 24 hours,"* and carries the explicit warning: *"You are responsible for all usage fees incurred when using this feature, including illegitimate traffic that may otherwise have been blocked."* Its documented use case is the opposite one — a business-critical event where a legitimate shared proxy is being false-positived.

## 2.8 Published rules of engagement, with real safe harbour

### `security.txt`, per RFC 9116

The file **MUST** live at `https://example.com/.well-known/security.txt`, **MUST** be served over `https`, **MUST** be `text/plain; charset=utf-8`, and **MUST** contain a `Contact` field and **exactly one** `Expires` field in RFC 3339 format. RFC 9116: *"It is RECOMMENDED that the value of this field be less than a year into the future to avoid staleness."*

**An expired `security.txt` is the single most common defect in deployed ones, and it is worse than none** — it tells a finder the channel is abandoned.

```
# public/.well-known/security.txt
Contact: mailto:security@example.com
Policy: https://example.com/security
Acknowledgments: https://example.com/security/thanks
Preferred-Languages: en
Canonical: https://example.com/.well-known/security.txt
Expires: 2027-01-01T00:00:00z
```

If your framework does not serve dot-folders from `public/`, add a rewrite. Verify:

```bash
curl -sI https://hackme-example.com/.well-known/security.txt
# expect: 200, and content-type: text/plain; charset=utf-8

# and check the Expires date is actually in the future
curl -s https://hackme-example.com/.well-known/security.txt | grep -i '^Expires:'
```

Note for triage: a `.well-known/security.txt` is **`NOTVULN-13`** — publishing one is good practice, never a finding. The only defect is a stale `Expires`.

### Safe harbour

The `Policy` page needs the disclose.io `dioterms` **Full Safe Harbor** four tenets (CC0 licensed, so you can lift them, and if you modify them you are asked to preserve all four):

1. Authorisation against anti-hacking laws (CFAA, CMA, equivalent).
2. Exemption from anti-circumvention laws (DMCA, equivalent).
3. Exemption from violation of the organisation's own ToS/AUP during security testing.
4. A statement acknowledging good-faith research.

A policy missing one of those but promising not to pursue good-faith researchers is **Partial Safe Harbor** — which is a real and honest thing to publish, as long as you call it that. Be explicit that authorization extends **only to the named hosts**.

### Out of scope — write it down, and say the first one twice

- **Denial of service, volumetric load testing, and resource-exhaustion testing of any kind.** This is the one people will try.
- Automated scanners against production.
- Social engineering of you, your users, or your vendors; physical attacks.
- **Anything targeting your third-party providers** — Vercel, Supabase, Cloudflare, Stripe, Twilio, your LLM vendor. You cannot grant safe harbour for someone else's systems, and their ToS governs.
- Sending real email or SMS to real people; testing against accounts you do not own.
- Reports that are pure scanner output with no proof of impact; missing-header and best-practice findings.
- Exfiltrating more data than needed to prove the bug. State the hard rule in one sentence: **stop at one record, report it, do not download the table.**

Also state, on the same page: how to report (one channel), what you commit to (acknowledge within N days), whether there is a bounty — say *"no bounty, credit only"* if that is true, because ambiguity generates hostile finders — and a **required test-account signup path** so researchers never touch anything they should not.

## 2.9 Instrument the weak surface as a honeypot

Because there is no real data, you can afford to leave one deliberately weak-looking surface up and log everything that touches it. That is where the interesting reports come from — you learn what people try first, in what order, and with what tooling. Feed it into the `security_events` table from Part 1 so the artifact survives the event.

## 2.10 What to do when someone reports something

1. **Acknowledge fast**, within the window you published. Silence is what turns a finder into a discloser.
2. **Reproduce before you believe or disbelieve it.** A report you cannot reproduce is not a finding, and a report you dismissed without trying is a public embarrassment waiting.
3. **Triage against the false-positive list before responding.** The publishable key, the CORS console error (`NOTVULN-11`), the `dangerouslySetInnerHTML` grep hit (`NOTVULN-09`), the `npm audit` count (`NOTVULN-12`), the discoverable subdomain (`NOTVULN-13`). Say clearly and without condescension why it is not a finding, and what *would* make it one.
4. **If it is real, treat it as an incident** — Part 1, from Step 0. Yes, on the challenge site. The evidence-preservation habit is exactly what you are trying to build.
5. **Check whether the same bug exists in your real app.** This is the single highest-value output of the whole exercise, and it is easy to forget while enjoying the challenge.
6. **Credit them** on the Acknowledgments page you promised in `security.txt`.
7. **Publish the post-mortem** — timeline, what worked, what did not, what the caps caught. That artifact is what makes the exercise worth having run.

## 2.11 Pre-launch readiness checklist

Run every line. Do not announce until all of them pass.

```bash
# 1. No credential is shared between the challenge project and anything real.
#    Diff the VALUES, not just the names.
cd ~/challenge-app && vercel env pull .env.challenge
cd ~/real-app      && vercel env pull .env.real
# then compare the values, e.g.:
grep -h '=' ~/challenge-app/.env.challenge | cut -d= -f2- | sort > /tmp/c.txt
grep -h '=' ~/real-app/.env.real           | cut -d= -f2- | sort > /tmp/r.txt
comm -12 /tmp/c.txt /tmp/r.txt        # MUST be empty
rm -f /tmp/c.txt /tmp/r.txt ~/challenge-app/.env.challenge ~/real-app/.env.real

# 2. security.txt resolves on the challenge domain with a future Expires.
curl -sI https://hackme-example.com/.well-known/security.txt
curl -s  https://hackme-example.com/.well-known/security.txt | grep -i '^Expires:'

# 3. The data layer holds up from outside, with only the publishable key.
curl -s "https://<ref>.supabase.co/rest/v1/<table>?select=*" \
  -H "apikey: $PUBLISHABLE_KEY" -H "Authorization: Bearer $PUBLISHABLE_KEY" | head -c 400
#    Rows coming back is fine ONLY if you intended that table to be the honeypot.
```

- [ ] Separate account/team/organization on **every** provider, verified by logging in as the challenge identity and seeing nothing real.
- [ ] Separate registrable domain; no shared DNS zone, no shared registrar account, no shared API token.
- [ ] Zero production data. Statement published saying the data is synthetic.
- [ ] Spend cap **and its automatic action** confirmed enabled on every billed provider.
- [ ] Kill switch **exercised once** — site down, site back up, elapsed time recorded.
- [ ] Observability turned up, and its cost accepted in writing.
- [ ] `security.txt` live, correct content type, future `Expires`.
- [ ] Policy page live with the four safe-harbour tenets and the out-of-scope list.
- [ ] Reporting channel monitored by a human, with a published response window.
- [ ] Post-mortem template ready before the first report arrives.

## 2.12 The honest warning: what a public challenge can and cannot prove

**It can prove:** that a specific, named set of attacks was attempted against a specific build over a specific window and did not succeed; that your caps, kill switch and logging work under real load; that you can run an incident. Those are genuine, publishable claims.

**It cannot prove that the app is secure.** Three reasons, all structural:

1. **Nobody may have looked properly.** Absence of a successful report is absence of *evidence*, not evidence of absence — and a challenge attracts opportunists running scanners far more than it attracts people doing patient authorization testing. Static analysis structurally cannot find broken access control, and neither can a crowd that spends four minutes each.
2. **Broken access control is the most likely thing to sink the app and the least likely thing to be found by a stranger.** OWASP Top 10:2025 A01 reports **100% of applications tested had some form of broken access control**. A finder without two accounts in two tenants cannot see it.
3. **The challenge build is not the production build.** Different data, different scale, different integrations, different environment variables. A clean challenge says nothing about the app that holds real users' data — unless you deliberately re-tested the same finding classes there, which is §2.10 step 5.

So the claim to make publicly is *"N people attacked this for M days; here is everything that was found and everything that was fixed"* — with the post-mortem attached. The claim **not** to make is *"my app is unhackable."* The dossier's governing rule applies to you as much as to the people you advise: you can say a specific gate passed, or that a specific check found nothing. You cannot say secure.

Before making any whole-app assurance claim at all, the closing artifact is a saved Autorize table with **zero red and zero unresolved yellow** over a session that touched every feature — not a quiet inbox.

---

# Part 3 — Builder account hardening: the root of trust above the app

`OPS-08`, rated CRITICAL. Every control in this skill assumes the attacker is *outside* your accounts. This part is the layer where that assumption is established, and it is the layer solo builders skip.

Your production system is not really "a Next.js app on Vercel with a Supabase database." It is **one consumer email inbox**, plus a browser profile holding live session cookies for six dashboards, plus a laptop with a dozen long-lived credentials in plaintext dotfiles, plus an AI coding agent holding its own OAuth token and a pile of third-party API keys in `~/.claude.json`. No SSO, no MDM, no IdP, no help desk, no second pair of eyes. The attacker does not need a framework CVE if they can read your cookie jar.

## 3.1 The chain, and the circular dependency at the root

```
personal Gmail  ──►  password manager (recovery email)
      │                     │
      ├──► GitHub (reset link + recovery codes stored in the manager)
      ├──► Vercel / Netlify (reset link)  ──► env vars ──► DB + Stripe keys
      ├──► Supabase (reset link)          ──► service key ──► all rows
      ├──► Stripe (reset link)            ──► payouts, restricted-key creation
      └──► domain registrar (reset link)  ──► DNS ──► MX ──► every reset link above
```

Two nodes are load-bearing. **Email** terminates every reset flow. **The registrar can *become* email** — repoint MX, then request resets on everything else, and satisfy an ACME `dns-01` challenge to mint a publicly trusted certificate so nothing in the browser looks wrong.

**The registrar is the true root.** Blast radius, stated plainly, because it decides where the two hardware keys go:

| Compromised | You lose |
|---|---|
| GitHub | Source and CI — but not necessarily production secrets |
| Hosting (Vercel/Netlify) | Production environment variables, which is every downstream key at once |
| **Registrar** | **Everything, including the ability to receive your own recovery mail** |

**The loop almost everyone ships:** the registrar account's contact address is `me@theproductdomain.com`, and that domain's MX is controlled by the registrar account. Lose the registrar and you cannot receive the registrar's own recovery mail. The same loop exists if the password manager's recovery email is the Gmail whose password lives *in* the password manager.

**Second trap:** "Continue with GitHub" on both Vercel *and* Supabase means GitHub is not one node among six — it is single sign-on for your entire production plane.

The registrar failure is not hypothetical. KrebsOnSecurity reported that migrated Google Domains accounts without a password set could be claimed by anyone who knew the associated email — *"Since there's no password on the account, it just shoots them to the create password for your new account flow"* — with Squarespace not requiring email verification for new accounts created with a password, and MFA not carrying over. Hijacks ran **9–12 July 2024** against *"at least a dozen organizations"* including Celer Network, Compound Finance, Pendle Finance and Unstoppable Domains. **Squarespace disputed the account on 23 July 2024**, attributing it to *"a weakness related to OAuth logins."* Carry the dispute — the outcome (DNS changed hands) is the same either way.

### The corrected inventory

```
Google Workspace: aj@aj-ops.dev   (a domain you do NOT ship the product on)
                  Advanced Protection ON → security keys/passkeys required for sign-in,
                  app passwords blocked, recovery hardened
                  2 × FIDO2 keys registered; recovery phone REMOVED
Password manager: recovery email = aj@aj-ops.dev; Emergency Kit printed, stored offline
GitHub:           aj@aj-ops.dev + 2 × passkeys; org 2FA requirement ON
Vercel/Supabase:  EMAIL+PASSWORD identity with its own passkey — NOT "Continue with GitHub"
Stripe:           aj@aj-ops.dev + hardware key; SMS removed
Registrar:        aj@aj-ops.dev, product domain at registrar A
                  aj-ops.dev registered at registrar B   ← loop broken
                  transfer lock ON, DNSSEC ON, CAA published
```

### Detect

```bash
# The circular-dependency check. If the registrant/contact email is @YOURDOMAIN.com,
# and that domain's MX is managed by the same registrar account, you have the loop.
whois yourdomain.com | grep -iE 'registrar:|status:|registrant email'
dig +short MX yourdomain.com
dig +short NS yourdomain.com

# Registrar lock present? You want clientTransferProhibited.
whois yourdomain.com | grep -i status

# Which providers are social logins rather than independent identities?
#   github.com/settings/applications   and   myaccount.google.com/connections
```

Publish DMARC `p=reject`. Publish CAA pinning your CA with `issuewild ";"`. Monitor crt.sh for certificates you did not request. And **delete dangling CNAMEs quarterly** — an abandoned CNAME to a deleted resource is claimable, and `can-i-take-over-xyz` catalogues the fingerprints (*"No such app"*, *"The specified bucket does not exist"*, *"There isn't a GitHub Pages site here."*).

## 3.2 Passkeys protect login — not enrollment, recovery, or downgrade

This is the most important correction to the standard "turn on 2FA" advice, and the attackers are already using the advice itself as the lure.

Google Threat Intelligence Group documents **UNC6671 (2026)**:

1. The operator calls the target **on their personal mobile number**, "circumventing corporate security controls," in recent cases having *"spoofed the legitimate helpdesk phone number adding an air of legitimacy."*
2. The pretext is an *"urgent helpdesk mandate to enable FIDO2 passkeys or update multi-factor authentication enrollment"* — the attacker impersonates the exact security advice this skill gives.
3. Lookalike domains built for that pretext: `passkeyhelpdesk[.]com`, `addssopasskey[.]com`, `createssopasskey[.]com`, `oskeysync[.]com`, usually with a victim-specific subdomain.
4. Those pages are **AiTM proxies** — *"spoofed login portals where Adversary-in-the-Middle (AiTM) infrastructure intercepts credentials and multi-factor authentication (MFA) tokens."* The victim types a password and approves a push or TOTP; the proxy relays both and keeps the session cookie.
5. Post-compromise, operators *"used compromised email accounts to initiate unauthorized password resets for non-SSO enterprise applications,"* then *"systematically deleted password-reset confirmations, secondary security notifications, company-wide security alerts, and any alerts generated during modifications to account security or MFA configurations."*

**Step 5 is the part solo builders never plan for: your entire detection strategy is "I'll see the email," and the attacker's first act is to delete that email.**

The doctrine: a passkey defends the **authentication** step, because WebAuthn binds the assertion to the origin — GitHub's wording is that *"the web browser will refuse to authenticate to a lookalike phishing website."* It does **not** defend the **enrollment** step, the **recovery** step, or a **downgrade** to password + TOTP that the account still allows. NIST SP 800-63B is explicit about why the fallback is fatal: *"Authenticators that involve the manual entry of an authenticator output (e.g., out-of-band and OTP authenticators) SHALL NOT be considered phishing-resistant because the manual entry does not bind the authenticator output to the specific session being authenticated."*

**A passkey plus a retained TOTP fallback is a TOTP account with extra steps.**

### Fix

```
GitHub 2FA settings — the target state:
  ✔ Passkey (hardware key #1, carried)
  ✔ Passkey (hardware key #2, in a safe)   ← the second key is what lets you delete the weak ones
  ✘ TOTP        removed
  ✘ SMS         removed   (GitHub lists "fallback SMS number" as an account-recovery path;
                           removing it removes that path for the attacker too)
  Recovery codes: printed, offline, never photographed
```

**Register two passkeys first, then delete TOTP and SMS.** Doing it in the other order is how people lock themselves out.

**Write the standing rule down, in these words:**

> No person, email, chat message, or phone call will ever ask me to enroll, re-enroll, reset, or verify an MFA factor. Any such request is fraud, 100% of the time. I only ever start MFA changes myself, by typing the provider's domain into the address bar. And no CAPTCHA, download, or verification step ever requires pasting a command into Terminal.

### Account recovery is the real authentication boundary

An account's security equals the **weakest path to a valid session**, and that path is almost always recovery, not login.

- **GitHub** documents these automatic recovery paths: a recovery code; a passkey (*"Passkeys satisfy both password and 2FA requirements, so you don't need to know your password in order to recover your account"*); a security key; **a code sent to a fallback SMS number**; and a one-time password to a verified email followed by *"a recovery authentication factor, such as an SSH key or previously verified device."* There is also *"unlink an email address tied to the locked account,"* after which that address can be linked elsewhere. The compensating fact is that GitHub genuinely fails closed at the end: *"GitHub Support will not be able to restore access to accounts with two-factor authentication enabled if you lose your two-factor authentication credentials or lose access to your account recovery methods."*
- **Google app passwords are a legal MFA bypass.** An app password is *"a 16-digit passcode that gives a less secure app or device permission to access your Google Account."* It requires 2-Step Verification to *create*, but using it presents **no second factor** — that is its entire purpose. Google's own position: *"App passwords aren't recommended and are unnecessary in most cases."* They cannot be created under Advanced Protection or with security-key-only 2SV, and *"we revoke your app passwords when you change your Google Account password."*
- **npm** (June 2026) now applies friction where recovery meets publishing: *"High-impact npm accounts are now put into a read-only mode for 72 hours when they change their email or use a 2FA recovery code."*

### Detect — out of band, because alert emails get deleted

```bash
# GitHub personal security log: UI surface, 90-day retention. Read these categories by eye:
#   two_factor_authentication, public_key, oauth_authorization, oauth_access,
#   personal_access_token, repo
open https://github.com/settings/security-log

# If your repos live in an org, the audit log IS an API and can be archived (180 days):
gh api /orgs/ORG/audit-log --paginate \
  | jq -r '.[] | "\(.created_at)\t\(.action)\t\(.actor)"' \
  | grep -E 'two_factor|oauth|personal_access_token|public_key'
# Anything you did not do — especially two_factor_authentication.* — is an incident.

# Revoke Google app passwords and review third-party access:
open https://myaccount.google.com/apppasswords
open https://myaccount.google.com/connections

# Recovery codes lying around in cleartext:
grep -rlE '^[a-z0-9]{5}-[a-z0-9]{5}$' ~/Downloads ~/Documents ~/Desktop 2>/dev/null
```

**The provider UI is ground truth. Your inbox is not.**

## 3.3 Token blast radius, in the vendors' own words

A leaked token is worth exactly its scope. Vendor defaults are generous; the scoped alternatives are free but opt-in. When a scoped token 403s, the fastest way to make the error go away is to widen the scope — which is exactly what an assistant debugging a CI failure will suggest.

| Credential | Default blast radius (vendor's words) | The scoped alternative |
|---|---|---|
| **Vercel access token** | Scope **Full Account** — *"Acts on your personal account and every team you belong to."* Full-account tokens are also the only kind that can mint more tokens | `vercel tokens add --project <PROJECT_ID>` with an expiration. Project scope *"denies any request to another project, to a user-level resource, or to a team-level resource"* |
| **Supabase PAT (`sbp_`)** | *"PATs carry the same privileges as your user account"* — the whole Management API, every org, every project | **No finer scope exists.** Treat as a root credential: short custom expiry, never in CI, never in an MCP config |
| **GitHub classic PAT, `repo`** | *"Full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks,"* plus *"organization-owned resources including projects, invitations, team memberships and webhooks"* | Fine-grained PAT: selected repositories, per-permission read/write, `expires_in` *"Integer between 1 and 366"* days. Or a GitHub App — installation tokens expire in **1 hour** |
| **GitHub deploy key** | *"Credentials that don't have an expiry date,"* tied to the repository not the user; with write access *"can perform the same actions as an organization member with admin access"* | Avoid. If unavoidable: read-only, inventoried, reviewed quarterly |
| **Cloudflare Global API Key** | Full account control | Scoped API token: permission groups, per-zone resources, **TTL**, and **client-IP filtering** |
| **npm publish token** | Long-lived write credential sitting in `~/.npmrc` | **Trusted publishing (OIDC)** from CI — *"eliminating the need for long-lived npm tokens."* For installs: *"Always use read-only granular access tokens for installing dependencies"* |

```yaml
# .github/workflows/deploy.yml — the fixed shape
permissions:
  contents: read                       # narrow the ambient GITHUB_TOKEN first
env:
  # created with: vercel tokens add --project prj_xxx   (with an expiration set)
  VERCEL_TOKEN: ${{ secrets.VERCEL_PROJECT_TOKEN }}
  # fine-grained PAT, selected repositories, expiry <= 90 days —
  # or better, a GitHub App installation token minted per run (1-hour lifetime)
  GH_TOKEN:     ${{ secrets.FINE_GRAINED_PAT }}
# Supabase: no scoped PAT exists. Do not put one in CI. Use the CLI locally, or a
# project-scoped service credential for the one operation you actually need.
```

```bash
# Verify a "project-scoped" Vercel token really is scoped:
curl -s -H "Authorization: Bearer $VERCEL_TOKEN" https://api.vercel.com/v9/projects | jq '.projects | length'
# ↑ if it returns more than one project, it is not project-scoped.
```

### "Read-only" roles are not secret-safe

- **Vercel Project Viewer** — described as read-only — has as a listed responsibility: *"Examine environment variables across all environments."* And team **Billing** and **Viewer** members *"automatically act as project viewers for every project."* Your viewer reads `DATABASE_URL` and `STRIPE_SECRET_KEY`.
- **Vercel Developer** cannot edit production env vars, but *"Developers can deploy to production by merging to the production branch in Git-based workflows."* Restricting env-var edits does not restrict shipping code — **so protect `main`.**
- **Vercel Contributor**, the only role that can be scoped to individual projects, **is not available on Pro**: *"the Pro plan does not support the Security and Contributor roles."* On Hobby/Pro your realistic least-privilege options are Developer or Viewer, both team-wide. *(This corrects an earlier claim in the research that scoped project roles are free — they are Enterprise-only.)*
- **Supabase Developer** is *"read-only access to organization resources and content access to project resources"* — content access means the data.
- **GitHub org 2FA enforcement** removes non-2FA **outside collaborators**, but members *"will retain membership even without 2FA, including consuming seats."*

Whether a Vercel Viewer sees env-var **values** or masked placeholders was not verifiable from the docs — test it on a throwaway team before relying on either answer.

### Uninstall is not revocation

**Installation** (repo access) and **authorization** (acting as you) are independent grants. GitHub: suspending an app means *"your authorization of the app… will not be affected"*; full revocation requires de-authorizing in the **Authorized GitHub Apps** tab, which *"will fully deactivate any tokens issued to the app on your behalf."*

**Four surfaces to audit quarterly**, not one:

1. Settings → Applications → **Installed GitHub Apps**
2. Settings → Applications → **Authorized GitHub Apps**
3. Settings → Applications → **Authorized OAuth Apps**
4. Org Settings → **Third-party Access** → GitHub Apps

And enable the org OAuth access restriction — without it, *"Any OAuth app authorized by an organization member can also access the organization's private resources."* Even with restrictions on, GitHub warns *"Users can still authorize privileged OAuth apps and use them to access data from the organization."*

```bash
gh api /user/installations --paginate | jq -r '.installations[] | "\(.app_slug)\t\(.repository_selection)\t\(.updated_at)"'
gh api /orgs/ORG/installations --paginate | jq -r '.installations[] | "\(.app_slug)\t\(.repository_selection)"'
# The two "Authorized …" lists are UI-only for personal accounts:
open https://github.com/settings/applications
```

### Protect `main` with an empty bypass list

GitHub rulesets ship with a bypass mechanism, and a solo builder's first ruleset almost always adds *"Repository admin"* to it to stop the rule being annoying — which exempts the account most likely to be phished. Note also that *"Anyone with read access to a repository can view its active rulesets"*: your bypass list is reconnaissance.

```jsonc
{ "target": "branch", "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"] } },
  "bypass_actors": [],                                   // ← nobody, including you
  "rules": [
    { "type": "deletion" }, { "type": "non_fast_forward" },
    { "type": "required_signatures" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true } },
    { "type": "required_status_checks", "parameters": { "required_status_checks": [
        { "context": "secret-scan" }, { "context": "build" } ] } }
  ] }
```

Solo with no reviewer? Set `required_approving_review_count` to 0 and rely on the required status checks. The value is the enforced pause and the automated gate, not a second human.

```bash
gh api /repos/OWNER/REPO/rulesets | jq -r '.[] | "\(.id)\t\(.name)\t\(.enforcement)"'
gh api /repos/OWNER/REPO/rulesets/RULESET_ID | jq '{bypass_actors, rules: [.rules[].type]}'
gh api /repos/OWNER/REPO/keys | jq -r '.[] | "\(.title)\tread_only=\(.read_only)\tcreated=\(.created_at)"'
```

## 3.4 The laptop

### Infostealers, and what they actually target

Microsoft's August 2026 analysis of a macOS **ClickFix** campaign describes a lure page presenting *"a counterfeit interface mimicking legitimate software distribution… a forged 'Verified Publisher' badge with GitHub-themed branding and… a one-click copy function for executing an obfuscated command."* The victim clicks copy, opens Terminal, pastes, and runs *"an obfuscated curl one-liner."* The campaign *"shifted tactics from openly serving infostealer lures to hiding them behind a browser-fingerprinting gate"* — so a researcher visiting the URL sees nothing.

The payload is **Atomic Stealer (AMOS)** / **MacSync Stealer**, and the collection list reads like a developer's file inventory:

- *"macOS Keychain material and browser Safe Storage keys"*
- *"browser credentials, cookies, login databases, session data, IndexedDB, LevelDB, and extension storage"*
- **SSH keys and AWS credentials**, Kubernetes configurations
- Apple Notes data, Safari history
- a sweep of `~/Downloads`, `~/Documents`, `~/Desktop` for *"PDF, DOCX, TXT, KEY, PEM, **KDBX**, OVPN, WALLET, and SEED files"*

Staged to `/tmp/osalogging.zip`, chunked, exfiltrated *"via curl with HTTP PUT requests"* carrying `upload_id`, `chunk_index`, `total_chunks`.

Note `KDBX`: **the malware collects your password vault file.** Note the cookies: **stolen session cookies bypass MFA entirely and are untouched by key rotation.**

Vibe coders are the target population by construction — every AI answer, every quickstart, every `curl … | sh` installer conditions exactly the muscle memory ClickFix exploits, and the GitHub-branded "Verified Publisher" badge is aimed at them deliberately.

**The rule, which is a rule and not a flag: no CAPTCHA, download, verification step, or "fix" ever requires pasting into Terminal.** If you must run a vendor installer, do it as three inspectable steps:

```bash
curl -fsSL -o /tmp/install.sh https://vendor.example/install.sh
shasum -a 256 /tmp/install.sh          # compare to the hash on the vendor's docs page
less /tmp/install.sh                   # actually read it
sh /tmp/install.sh                     # no sudo unless the docs justify it
```

macOS 26.4+ shows a warning on suspicious Terminal pastes. Do not click through it.

```bash
# Did a shell recently fetch-and-execute?
grep -nE 'curl[^|]*\|\s*(sudo\s+)?(ba|z)?sh|base64\s+-d|osascript' ~/.zsh_history ~/.bash_history 2>/dev/null
# Staging/exfil artefacts named in Microsoft's reporting
ls -la /tmp/osalogging.zip /tmp/sync* 2>/dev/null
# Persistence added recently
ls -la ~/Library/LaunchAgents /Library/LaunchAgents 2>/dev/null
# Sensitive files sitting exactly where the sweep looks
find ~/Downloads ~/Documents ~/Desktop -maxdepth 2 \
  \( -name '*.pem' -o -name '*.key' -o -name '*.kdbx' -o -name '*.ovpn' \) 2>/dev/null
```

**If any of those hit: assume total credential compromise, rotate from a different, clean machine, and rebuild.** Then run Part 1 from Step 0.

### The AI tool config files, which the standard advice omits

Microsoft's ChainDrop analysis found the shell collector *"attempts to obtain the GitHub CLI token and captures the values of all process environment variables,"* searching npmrc, *"shell histories, cloud configuration, Secure Shell (SSH) keys,"* cloud provider credentials, Kubernetes configs and Vault secrets — and it targeted **`.claude/settings.json` and `.vscode` configuration paths for persistence injection**. Agent config is both a credential store and a persistence mechanism.

Outbound, the agent holds more than people realise. From Anthropic's own docs:

- macOS: credentials in the encrypted Keychain. **Linux: `~/.claude/.credentials.json`, mode `0600`. Windows: `%USERPROFILE%\.claude\.credentials.json`.** (Or under `CLAUDE_CONFIG_DIR`.)
- `claude setup-token` mints a **one-year OAuth token** which you then export as `CLAUDE_CODE_OAUTH_TOKEN`. It *"does not save the token anywhere"* — meaning *you* save it, usually into a shell profile or CI secret, for a year.
- MCP server configuration — including `--env KEY=value` API keys and `--header "Authorization: Bearer …"` — is stored in **`~/.claude.json`** for local/user scope, and in **`.mcp.json` at the project root, which the docs tell you to check into version control.**
- `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `apiKeyHelper` sit in the environment; `apiKeyHelper` is re-invoked *"after 5 minutes or on HTTP 401."*
- Organization pinning has gaps: *"`claude setup-token` and `/install-github-app`: enforce only `forceLoginMethod`, so they can mint a token in a different organization."*

```jsonc
// .mcp.json — the fixed shape: no secrets, values resolved from the environment
{
  "mcpServers": {
    "db":     { "command": "npx", "args": ["--no", "--", "@vendor/pg-mcp"],
                "env": { "DATABASE_URL": "${DEV_DATABASE_URL}" } },
    "github": { "type": "http", "url": "https://api.githubcopilot.com/mcp/" }
    // ↑ authenticate with `claude mcp login github` (OAuth, refreshable, revocable)
    //   instead of pasting a bearer token. Clear it later with `claude mcp logout github`.
  }
}
```

```gitignore
.mcp.json
.claude/settings.local.json
.env*
!.env.example
```

```bash
# What credentials does the agent hold on this machine?
ls -l ~/.claude/.credentials.json 2>/dev/null                          # expect mode 0600
security find-generic-password -s 'Claude Code' 2>/dev/null | head -1  # macOS Keychain entry
jq -r '.. | objects | select(has("env")) | .env' ~/.claude.json 2>/dev/null
grep -rnE 'sk-ant-|sk_live_|ghp_|github_pat_|sbp_|vcp_|postgres://' ~/.claude.json .mcp.json 2>/dev/null
env | grep -E 'ANTHROPIC_|CLAUDE_CODE_OAUTH_TOKEN'
git ls-files | grep -E '^\.mcp\.json$|^\.claude/settings\.local\.json$'   # committed by accident?
```

### The password manager is a downstream target, not a root of trust

LastPass is the canonical worked example, and it is the reason "put everything in the vault" is not a complete answer. LastPass's own update: *"The threat actor targeted a senior DevOps engineer by exploiting vulnerable third-party software,"* obtaining *"DevOps Secrets – restricted secrets that were used to gain access to our cloud-based backup storage."* That storage held *"configuration data, API secrets, third-party integration secrets, customer metadata, and backups of all customer vault data,"* plus a *"Backup of LastPass MFA/Federation Database"* containing *"copies of LastPass Authenticator seeds, telephone numbers used for the MFA backup option"* — and the decryption key for that database was compromised too.

**One endpoint compromise of one privileged human produced offline copies of vaults and TOTP seeds.** MacSync sweeps for `.KDBX` and Atomic takes Keychain material, so the same class of outcome is available against your laptop directly, by a different route.

The laptop's target state:

- Vault master password unique, typed into nothing but the vault client; **auto-lock in minutes, not hours; unlock requires the hardware key.**
- **The second factors guarding the vault live on hardware, not in the vault.**
- A separate, extension-free browser profile for provider dashboards.
- `npm logout` on the laptop; publish via trusted publishing (OIDC) from CI.
- The coding agent runs in a devcontainer or VM with only the repo mounted and **no cloud credentials in its environment**.
- Emergency Kit and recovery codes printed and stored physically, off the machine.

```bash
ls -la ~/.aws/credentials ~/.npmrc ~/.git-credentials ~/.docker/config.json ~/.kube/config 2>/dev/null
grep -c authToken ~/.npmrc 2>/dev/null                 # 0 is the target on a laptop
npm whoami 2>/dev/null && echo "still logged in to npm — run: npm logout"
find ~ -maxdepth 3 -name '*.kdbx' -o -maxdepth 3 -name '*.opvault' 2>/dev/null
ls ~/Library/Application\ Support/Google/Chrome/ 2>/dev/null | grep -i profile
```

## 3.5 Offboarding — including the AI tier the standard checklist omits

Removing a person from an org does not remove their access. A deploy key *"will still be active as it isn't tied to the specific user, but rather to the repository,"* has no expiry, and with write access *"can perform the same actions as an organization member with admin access."* Local clones, forks, cached tokens, live browser sessions and every integration they installed all survive. The same is true when you stop using a **tool**: deleting an MCP server from config does not revoke the API key it held, and uninstalling a GitHub App does not de-authorize it.

**Revoke before you rotate**, or the departing party (or the attacker) simply reads the new value.

```bash
### 1. REVOKE ACTIVE ACCESS FIRST
gh api -X DELETE /orgs/ORG/members/USERNAME
gh api -X DELETE /repos/OWNER/REPO/collaborators/USERNAME
gh api /repos/OWNER/REPO/keys  | jq -r '.[] | "\(.id)\t\(.title)"'        # then delete stale keys:
gh api -X DELETE /repos/OWNER/REPO/keys/KEY_ID
gh api /repos/OWNER/REPO/hooks | jq -r '.[] | "\(.id)\t\(.config.url)"'   # delete unknown webhooks
# Vercel / Supabase / Stripe: remove the member in each dashboard, individually.
# If this is a compromise rather than a departure, also kill YOUR sessions and grants:
#   github.com/settings/sessions ; github.com/settings/applications (ALL THREE tabs)

### 2. ROTATE EVERYTHING THEY COULD HAVE READ
#   Supabase: service/secret key AND database password — revoke old keys explicitly
#   Stripe:   roll sk_live_ and any rk_live_ they held
#   Every value in any .env they ever received; every CI secret
#   OAuth client secrets for any provider they configured

### 3. THE AI TIER — the part everyone forgets
claude mcp list                       # inventory
claude mcp logout <server>            # clear stored OAuth credentials for a remote server
claude mcp remove <server>            # then remove the config entry
jq -r '.. | objects | select(has("env")) | .env' ~/.claude.json   # find keys you forgot
#   ...and SEPARATELY revoke each key that server held, at the vendor:
#   Stripe key, Postgres role, Linear/Notion/Slack token, GitHub PAT in the header.
#   If the machine is suspect, revoke the agent's own credentials too:
#     /logout in Claude Code; revoke any CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`;
#     uninstall AND de-authorize any GitHub App added by /install-github-app.

### 4. VERIFY VIA API, NOT VIA THE UI
gh api /orgs/ORG/members --paginate | jq -r '.[].login'
gh api /orgs/ORG/audit-log --paginate | jq -r '.[] | "\(.created_at)\t\(.action)\t\(.actor)"' | head -50
open https://github.com/settings/security-log      # personal-account equivalent, UI only
```

Run the same inventory **quarterly**, not only at departure.

## 3.6 The maintenance clock (`OPS-10`)

An app that was correct on launch day becomes incorrect on its own, without anyone touching it, because the world moves underneath it. Every framework CVE in this dossier is a clock event: **a day on which a working, unmodified app became exploitable.**

The failure mode here is not "they refuse to patch." It is that **the maintenance loop was never installed.** A shipped vibe-coded repo typically has no `dependabot.yml`, no scheduled scan, and no person who has ever read a release note — the lockfile's mtime is launch day.

### Four tiers, sized so they actually happen

| When | Budget | What |
|---|---|---|
| **IMMEDIATE** — drop what you are doing | 1–4 h | A CVE in your stack that is network-reachable pre-auth, **or** present in the **CISA KEV** catalogue, **or** has a public PoC. Also: any "your package was compromised" notice, any secret-scanning alert, any anomalous spend alert. |
| **WEEKLY** — Monday | **20 min** | Merge the one grouped patch/minor dependency PR after CI is green. Skim one digest. Glance at the four alerts and last week's auth-failure count. |
| **MONTHLY** | 1 h | Major-version PRs, one at a time. Run `osv-scanner scan -r .` and `gitleaks dir .` and **read the output**, not the exit code. Check provider audit logs for logins you do not recognise. Confirm the backup job ran. |
| **QUARTERLY** | 4 h | **Restore a backup into a scratch database and query it.** Rotate the credentials on the rotation list. Re-run the re-audit checklist. Delete unused preview deployments, API keys, OAuth apps, subdomains, dangling CNAMEs. Re-read your own `security.txt` expiry. |

**A KEV hit is the trigger for the IMMEDIATE tier, not the weekly one.**

### Make the weekly slot arrive as one PR, not nine

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    open-pull-requests-limit: 5
    cooldown:
      default-days: 7
      semver-major-days: 14
      semver-patch-days: 3
    groups:
      weekly-patch-and-minor:
        applies-to: version-updates
        update-types: ["patch", "minor"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

The cooldown matters: **auto-merge with no cooldown turns a compromised npm release into a production deploy.** GitHub applies a default 3-day cooldown to version updates even with no cooldown block — but **not** to security updates, which is the correct exemption.

### Exactly four feeds, three of them machine-filtered to your stack

The failure is drowning, then unsubscribing, then missing the one that mattered. Four inputs, routed to **one** Discord/Slack channel:

1. **Dependabot alerts** — filtered to your lockfile by construction. Enable repo → Watch → Custom → Security alerts. Read `state=auto_dismissed` too; the default auto-dismiss rule silences alerts without notifying you.
2. **The CISA KEV catalog**, matched against your stack. Plain HTTPS JSON, no auth. Fetched live during the research: `catalogVersion` **2026.08.19**, `count` **1671**; each entry carries `cveID`, `vendorProject`, `product`, `vulnerabilityName`, `dateAdded`, `shortDescription`, `requiredAction`, `dueDate` and `knownRansomwareCampaignUse`.
3. **The Node.js security channel.** Node's `SECURITY.md` names it: the `nodejs-sec` Google Group at `https://groups.google.com/group/nodejs-sec`, notified ahead of public disclosure with an embargo typically around 72 hours from CVE issuance, and within 6 hours of the mailing-list notice an advisory at `https://nodejs.org/en/blog/vulnerability`.
4. **Your framework's release channel, and only yours.** One channel per framework you actually ship.

Deliberately **not** on the list: general CVE firehoses, NVD's full feed, vendor marketing blogs, and Twitter/X. The GitHub Advisory Database is infrastructure you consume *through* Dependabot and `osv-scanner`, not by reading.

```bash
#!/usr/bin/env bash
# scripts/kev-watch.sh — run weekly from cron or a GitHub Action.
set -euo pipefail
KEV=https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
WATCH='Next.js|React|Node.js|Nginx|PostgreSQL|Cloudflare|Vercel|Supabase|Express'

curl -sS "$KEV" \
| jq -r --arg since "$(date -u -v-14d +%Y-%m-%d 2>/dev/null || date -u -d '14 days ago' +%Y-%m-%d)" '
    .vulnerabilities[]
    | select(.dateAdded >= $since)
    | [.dateAdded, .cveID, .vendorProject, .product, .knownRansomwareCampaignUse, .dueDate]
    | @tsv' \
| grep -Ei "$WATCH" || echo "No KEV additions matching your stack in the last 14 days."
```

```bash
# Is the clock installed at all?
git log -1 --format=%cd -- package-lock.json pnpm-lock.yaml yarn.lock   # lockfile mtime
ls -la .github/dependabot.yml renovate.json .github/renovate.json 2>/dev/null
gh api repos/:owner/:repo/subscription --jq '{subscribed, ignored}'      # is anyone receiving alerts?
bash scripts/kev-watch.sh
```

### Rotation cadence

| Credential | Cadence | Trigger-based |
|---|---|---|
| Anything that ever appeared in a repo, log, screenshot or chat | **Immediately, at the provider** | always |
| Webhook signing secrets | every 6–12 months | laptop compromise, contractor offboard |
| Database / service-role keys | every 6–12 months | any exposure or dependency compromise |
| JWT signing keys | every 6–12 months | any session-theft incident |
| CI / platform / registry publish tokens | prefer **OIDC — no long-lived token at all** | every incident |
| End-user passwords | **never on a schedule** | only on evidence of compromise |

That last row is not laziness: OWASP's Secrets Management Cheat Sheet explicitly excludes user passwords from scheduled rotation, and holds that a secret's appropriate lifetime *"could be from minutes… to years"* depending on what it protects. Its operative requirement: *"Keys that were exposed should undergo immediate revocation. The secret must be able to be de-authorized quickly, and systems must be in place to identify the revocation status."*

### Backups (`OPS-09`), because the quarterly slot is where this gets caught

Two failure modes dominate. **Never restored** — silent zero-byte dumps, missing schemas, plan-tier restore gates. And **same blast radius** — the credential that lost you production also deletes the backups; the Unit 42 extortion crew exfiltrated **and deleted** S3 objects. Supabase retention is Pro 7d / Team 14d / Enterprise up to 30d; PITR ships WAL every 2 minutes for 7–28d; **Free-plan backups are not downloadable at all.**

Fix: encrypt with `age` (public key only in CI), push to a **separate cloud account** with a PutObject-only identity, and enable S3 Versioning plus Object Lock in **compliance mode**, which is undeletable *"by any user, including the root user."* Then run a timed restore drill quarterly, execute real queries against the restored database, and **record the elapsed wall-clock time — that number is your RTO.**

```bash
# Has the backup job actually produced a recent, plausibly-sized object?
aws s3 ls "s3://$BACKUP_BUCKET/" --recursive | tail -5
# Is the bucket versioned and locked?
aws s3api get-bucket-versioning        --bucket "$BACKUP_BUCKET"
aws s3api get-object-lock-configuration --bucket "$BACKUP_BUCKET"
# Does the backup credential have delete rights it should not have?
aws iam simulate-principal-policy --policy-source-arn "$BACKUP_ROLE_ARN" \
  --action-names s3:DeleteObject s3:DeleteObjectVersion \
  --resource-arns "arn:aws:s3:::$BACKUP_BUCKET/*"
# When did you last restore? If this file has no entry, the answer is never.
grep -n "restore drill" SECURITY-AUDIT.md
```

Write the date and the elapsed time into `SECURITY-AUDIT.md` every quarter. If the drill did not happen this quarter, you do not have backups — you have hope.

### Re-audit triggers

Seven changes invalidate your last security review. **This list is `[R]` — a proposed control, not a documented finding — but the reasoning behind item 5 is why it is here at all.**

1. Auth added or changed.
2. Payments added.
3. File upload added.
4. An AI feature added.
5. **A large AI-driven refactor.** The highest-risk and least-reviewed of the seven, because an ownership check moved out of a route handler looks like noise in a 4,000-line mechanical diff.
6. A dependency compromise in the news that touches your tree.
7. The fixed quarterly interval, regardless of the above.

### The one-page continuity note

Vercel's own recommendation: *"For continuity, we recommend that at least two individuals have owner permissions."* Where a second owner is not possible, write a one-page "if I'm unavailable" runbook — which accounts exist, where the recovery material is physically stored, what the kill switch is — and store it **outside every account it describes**.
