# Stack selection and the client-only ceiling

Load this file when the audit needs to decide **which architecture lane a project is in**, when a user is at stage zero and has not yet picked a stack, when a finding's fix would require server code the project does not have, or when the user is being told to "move the check server-side" and you need to know whether that is a one-line edit or an architecture change. Everything below traces to `01-ZERO-TO-DEPLOY-PLAYBOOK.md` §3 (3.1–3.4) with supporting detail from the catalog entries named inline.

---

## 1. The framing

The question is not "which stack is most secure."

It is **which stack lets a non-expert be wrong without it being catastrophic**, and which stack turns a routine debugging move into a breach.

Every lane below can be built securely by an expert. The difference that matters for this audience is what happens on the bad day: when the agent generates a migration instead of using the Table Editor, when someone silences a "permission denied" error the fastest way possible, when a matcher gets edited during a refactor, when an SSL warning gets suppressed. In one lane those moves produce a broken build or a confusing outage. In another they produce a full-table export to anyone holding a key that was designed to be public.

Three consequences you should state to the user before recommending anything:

1. **A checklist you cannot run is not a checklist you failed.** If the project has no server module, the standard defenses are not "missing," they are *inexpressible*. Emitting DAL advice into a repo that has no server is worse than useless: the user pastes it into a file that never executes and believes the problem is handled.
2. **Adding a server later does not retroactively secure data that has already been served.** PostgREST has been answering the internet the whole time.
3. **Lane choice is made in the first ten minutes**, usually by accepting an AI's default, and several of its consequences (PCI scope, whether a BAA is needed, whether a password exists) are expensive to reverse.

---

## 2. Which lane is this project in

Run these; the first matching rule wins. This is the architecture subset of the full classifier in `01-ZERO-TO-DEPLOY-PLAYBOOK.md` §2.

```bash
# server surface — does any server module exist at all?
ls -d app/api api/ server/ functions/ supabase/functions netlify/functions 2>/dev/null
grep -rl "'use server'" app/ src/ 2>/dev/null | head
ls next.config.* nuxt.config.* vite.config.* svelte.config.* remix.config.* 2>/dev/null
ls requirements.txt pyproject.toml manage.py 2>/dev/null

# BaaS surface
grep -rl "createClient(" src/ app/ 2>/dev/null | head
ls supabase/migrations firestore.rules storage.rules 2>/dev/null
```

| Observation | Lane |
|---|---|
| `next.config.*` / `nuxt.config.*` / `svelte.config.*` / `remix.config.*` **and** `app/api` or `'use server'` files | **Lane A** |
| `src/` contains `supabase.from(...)` or Firebase Web SDK calls **and** there is no `app/api`, no `supabase/functions`, no `netlify/functions` | **Lane B** — client-only + BaaS |
| `requirements.txt` / `pyproject.toml` / `manage.py` | **Lane C** |
| Lane B files plus exactly one Edge Function | **Lane B in migration** — treat the function as Lane A territory and the rest as Lane B |

A Next.js repo that talks to Supabase *only from client components* is still Lane B for the tables it touches that way. Lane is a property of the data path, not of the framework in `package.json`.

---

## 3. Lane A — server-rendered framework + managed auth + Postgres + a server-only DAL

**The shape.** Next.js App Router, Nuxt, SvelteKit or Remix; a managed auth provider (Clerk, Auth0, Supabase Auth, better-auth); a Postgres you talk to through Prisma or Drizzle; and **all data access inside a server-only Data Access Layer**.

This is Next.js's own official recommendation for new projects. Their data-security guidance describes a DAL that "Only run[s] on the server. Perform[s] authorization checks. Return[s] safe, minimal Data Transfer Objects (DTOs)," and states that "only the Data Access Layer should access `process.env`" (<https://nextjs.org/docs/app/guides/data-security>).

**Why it wins for this audience** — and the reasons are about failure modes, not elegance:

- There is **one place** a reviewer (human or agent) can look. An audit becomes "read the DAL," not "read every file."
- `import 'server-only'` converts "the agent accidentally imported the DB client into a component" from a silent bundle leak into a **build error**.
- `@t3-oss/env-nextjs` makes a missing secret a **loud boot failure** instead of `undefined` flowing quietly into a query.

### 3.1 Lane A's three named footguns

These are not reasons to avoid Lane A. They are the things you must know up front, because each one defeats the intuition a non-expert brings.

**Footgun 1 — middleware is not a security boundary** (`AUTHZ-03`, `FRAME-03`).

Requests route *around* a perimeter. CVE-2025-29927 (CVSS 9.1): send `x-middleware-subrequest: middleware` — repeated 5× for 13.2+, because the header check happens before the recursion-depth check — and middleware is skipped entirely. Affected `>=12.0.0 <12.3.5`, `>=13.0.0 <13.5.9`, `>=14.0.0 <14.2.25`, `>=15.0.0 <15.2.3`. The successor CVE-2026-64642 hits App Router built with **Turbopack** plus exactly one entry in `config.i18n.locales`; fixed 16.2.11 / 15.5.21.

The more instructive case is **CVE-2026-41248** (Clerk `createRouteMatcher`, CVSS 9.1, fixed `@clerk/nextjs` 5.7.6 / 6.39.2 / 7.2.1): the bypassed pattern was *the documented, correct-looking one*. The general rule: any authorization scheme that depends on classifying a URL will eventually be bypassed by a URL you classified wrong.

**Naming note that breaks every grep instruction written before it:** as of **Next.js 16.0.0** the convention is renamed `middleware.ts` → **`proxy.ts`**. Grep for both.

```bash
grep -rn "middleware.ts\|proxy.ts" . --include='*.ts' --include='*.js' -l
curl -s -o /dev/null -w '%{http_code}\n' -H 'x-middleware-subrequest: middleware' "$APP/api/admin/users"
```

Treat middleware as UX. Re-check `auth()` inside every handler, action and server component.

**Footgun 2 — Server Actions are public POST endpoints** (`AUTHZ-04`).

Every referenced `'use server'` function is an addressable HTTP endpoint invoked with a `Next-Action` header. No UI involved. Next's own docs say an exported action "is reachable via a direct POST request, not just through your application's UI," and they explicitly mark layouts and the SPA `return null` pattern as **"not recommended"** guards.

What Next gives you: POST-only, an Origin-vs-Host CSRF check, encrypted closures, rotated action IDs. What it does **not** give you: authentication, authorization, validation, rate limiting, output minimization. CVE-2026-64643 then made action IDs disclosable to unauthenticated attackers, ending the obscurity layer that was never a control anyway.

```bash
# every 'use server' file with no auth call in it
rg -l "'use server'" app/ lib/ | while read f; do
  rg -q "auth\(|getSession|requireUser|Unauthorized" "$f" || echo "NO AUTH: $f"
done
```

**Footgun 3 — the matcher change that silently drops Server Function coverage.** This is the one to lead with when talking to an agent-driven builder.

Next's docs: Server Functions "are handled as POST requests to the route where they are used, so a Proxy matcher that excludes a path will also skip Server Function calls on that path."

So: someone edits the `matcher` in `proxy.ts` to stop middleware running on a static path. Nothing about the action changes. No file the action lives in is touched. The diff looks like a performance tweak. And the action is now reachable without the perimeter check.

**That is an auth bypass produced by a *refactor*** — which is precisely the failure mode of agent-driven development, where the model edits a config file to fix an unrelated symptom and has no model of what else that config was load-bearing for.

The defense is structural, not vigilance: because the check lives inside the action body and inside the DAL, no matcher edit can remove it.

```bash
# read the matcher and ask what it now excludes
rg -n -A6 "export const config" proxy.ts middleware.ts src/proxy.ts src/middleware.ts 2>/dev/null
```

### 3.2 Lane A false positives — do not report these

| Looks alarming | Actual status |
|---|---|
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the bundle | `NOTVULN-01`. Designed to be public. Check whether RLS exists; if rows come back, the finding is `BAAS-01`, **not** a leaked key. Do not rotate it as remediation. |
| `pk_test_` / `pk_live_` Stripe or Clerk key in client code | `NOTVULN-03`. Stripe's own table marks publishable keys "Safe to expose: Yes." The finding is whether amounts and prices are decided server-side (`PAY-03`). |
| A session JWT visible in a request | `NOTVULN-07`. That is how the app works. The findings nearby are different and specific: `localStorage` storage (`CLIENT-04`), a server that decodes without verifying (`AUTHN-02`), a `"role":"service_role"` payload (`BAAS-04`). |
| `dangerouslySetInnerHTML` | `NOTVULN-09` when wrapped in a real sanitizer with an explicit allowlist. The finding is an unsanitized `__html` sink, a regex "sanitizer", or `rehype-raw` without `rehype-sanitize` after it. |
| A CORS error in the console | `NOTVULN-11`. The browser doing its job. CORS is not an authorization mechanism; `curl` ignores it entirely. |

---

## 4. Lane B — client-only SPA + BaaS

The most common vibe-coded output, and **the one lane that cannot execute the standard defenses.** A Vite/React SPA calling `supabase-js` or the Firebase Web SDK directly from the browser, deployed to Vercel or Netlify as static files.

Read this whole section before recommending anything else.

### 4.1 What the attacker actually does

They never run your application. They read `dist/assets/index-*.js`, extract `VITE_SUPABASE_URL` and the publishable key (or the Firebase web config, which is *designed* to be public), and speak to the BaaS REST API directly with `curl`.

Every line of JavaScript you wrote — validation, role checks, disabled buttons, `if (user.isAdmin)` — is **not in the request path**. PostgREST or Firestore sees an anonymous HTTP request bearing a public-by-design key. The complete list of things standing between that request and your data is:

1. the grants on the role,
2. RLS policies / Security Rules,
3. table constraints and triggers.

Nothing else exists. Firebase says this out loud: "Firebase allows clients direct access to your data, and Firebase Security Rules are the only safeguard blocking access for malicious users."

### 4.2 The impossible-vs-substitute table

State this to the user plainly. It is not a list of things they got wrong; it is a list of things this architecture cannot express.

| Standard defense | Status in pure client + BaaS | What actually substitutes |
|---|---|---|
| Server-only DAL with `import 'server-only'` | **Impossible** — no server module | RLS policies / Security Rules **are** the DAL, written in SQL/CEL |
| Zod `safeParse` at the trust boundary | **Cosmetic** — runs in the attacker's browser | `CHECK` constraints, `DOMAIN` types, `NOT NULL`, RLS `WITH CHECK`, `request.resource.data` predicates |
| Authorization re-checked in a route handler | **Impossible** | RLS `USING` + `WITH CHECK` on `(select auth.uid())` |
| `@upstash/ratelimit` on Redis | **Impossible** for data traffic | Platform auth limits (fixed), Firebase App Check, a DB counter table + `BEFORE INSERT` trigger, spend caps |
| Third-party API key kept out of the bundle | **Impossible** — anything the client sends, it possesses | One Edge Function / Worker. You are no longer client-only |
| Stripe webhook signature verification | **Impossible** — a browser cannot receive an inbound POST | A function. No workaround |
| Turnstile / hCaptcha `siteverify` | **Impossible** for your own writes | Supabase Auth CAPTCHA (auth endpoints only) |
| CSRF tokens, `httpOnly` session cookies | **Impossible** — no server to set them | N/A; BaaS uses bearer tokens, so the risk shifts to XSS / token theft |
| Structured server-side audit logging | **Partial** | Append-only Postgres audit table written by a trigger; provider logs |

Two mechanics make this lane sharper than the generic "the client is untrusted."

### 4.3 Mechanic one — PostgREST hands the attacker a query builder

In a REST/Express app the developer writes the query and the client sends parameters. In Supabase **the client writes the query.** PostgREST exposes column selection, arbitrary filters, ordering, pagination and joins as URL parameters. The documented surface includes:

- `select=first_name,age` — arbitrary column projection
- operators `eq, gt, gte, lt, lte, neq, like, ilike, match, in, is, fts, cs, ov`
- `order=age.desc,height.asc`
- `columns=` on insert, choosing which keys persist
- and critically, **resource embedding**: `?select=*,actors(*)` pulls in related tables **through the foreign-key graph** — which is how an attacker pivots out of the one table you did secure into the join target whose RLS you forgot
- an unfiltered `GET /rest/v1/people` returns "the full contents of a table"

```bash
# what the attacker sees first — the whole schema, free, with the public key
curl -s "https://<ref>.supabase.co/rest/v1/" -H "apikey: $ANON" | head -50

# the pivot: does a foreign key reach something unprotected?
curl -s "https://<ref>.supabase.co/rest/v1/posts?select=*,profiles(*)&limit=3" -H "apikey: $ANON"
```

**Firestore made the opposite design choice, and this is why advice does not transfer.** Firestore's docs: **"Rules are not filters — queries are all or nothing."** And: "If a query could potentially return documents that the client does not have permission to read, the entire request fails." Firestore also lets rules constrain the query itself via `request.query` — for example denying any query not limited to 10 or fewer documents.

So the mental models are **not transferable in either direction**:

| | Supabase / PostgREST | Firestore |
|---|---|---|
| Query shape | Attacker composes it via URL params | Client declares it; rules can reject the query itself (`request.query.limit`) |
| Partial permission | Rows the policy allows come back; the rest are silently filtered | The **entire request fails** |
| Pivoting via relations | Resource embedding traverses foreign keys | No join primitive; `get()` in rules is limited (10 document accesses per single-document request, 20 for multi-document reads / transactions / batched writes; exceeding either is a permission-denied error) |
| Danger of "just add a filter in the app" | Total — the app is not in the path | Total — the app is not in the path |

Telling a Firestore user to "restrict the columns in `select=`" is nonsense. Telling a Supabase user "the query will just fail if the rules don't allow it" is dangerously wrong — it will succeed and return the subset the policy allows, which is exactly why an over-broad policy is invisible during development.

`NOTVULN-05` applies here and matters: the Supabase OpenAPI root at `GET /rest/v1/` listing every table and RPC is **inherent to PostgREST, not a bug in your app**, and hiding it is not a fix. Turning off `openapi_mode` — which is set on the `authenticator` role, **not** `anon`, a very common copy-paste error — costs an attacker minutes. Report `BAAS-01`, the missing RLS. For Firebase, the project ID is in every shipped bundle and APK, so discovery is free and continuous: **enumeration returning nothing is the goal, not enumeration being impossible.**

### 4.4 Mechanic two — RLS scopes rows, not columns, and a policy cannot compare OLD to NEW

`BAAS-08`, CRITICAL, CONFIRMED. This is the highest-yield attack against a vibe-coded Supabase app.

RLS answers "may this user touch this row?" It does not answer "may this user touch *this column* of this row?" A perfectly correct, owner-scoped `UPDATE` policy still permits this:

```bash
curl -X PATCH "https://<ref>.supabase.co/rest/v1/profiles?id=eq.$MY_ID" \
  -H "apikey: $ANON" -H "Authorization: Bearer $MY_JWT" \
  -H "Content-Type: application/json" \
  -d '{"role":"admin","credits":999999,"subscription_tier":"enterprise"}'
```

Every field is on **the attacker's own row**, so both `using ((select auth.uid()) = id)` and `with check ((select auth.uid()) = id)` pass. Self-promotion to admin, self-granted credits, self-granted paid tier. No auth bypass required, no stolen token, no other user involved.

**Why the usual advice fails:** an RLS policy **cannot express "this column may not change."** `WITH CHECK` sees only the new row. **There is no `OLD` in a policy expression.** Column immutability is therefore *inexpressible* in RLS, and requires either a column-level privilege or a trigger.

**Why AI-generated apps land here:** agents put every user attribute in one wide `profiles` table (`role`, `credits`, `is_pro`, `plan`, `stripe_customer_id`) because it makes the UI a single query, then write a single `for all using (auth.uid() = id)` policy because it makes every screen work. Both choices are locally reasonable and jointly fatal.

**The vulnerable shape:**

```sql
create table public.profiles (
  id uuid primary key references auth.users(id),
  display_name text,
  role text default 'user',
  credits int default 10,
  is_pro boolean default false
);
alter table public.profiles enable row level security;
create policy "own profile" on public.profiles for all to authenticated
  using ( (select auth.uid()) = id ) with check ( (select auth.uid()) = id );
```

**The fix, three layers, all enforceable with no server:**

```sql
-- 1) Column-level privileges. PostgREST honours Postgres column GRANTs.
revoke update on public.profiles from authenticated;
grant  update (display_name, avatar_url, bio) on public.profiles to authenticated;

-- also stop privileged columns being set at INSERT time
revoke insert on public.profiles from authenticated;
grant  insert (id, display_name, avatar_url, bio) on public.profiles to authenticated;

-- 2) A BEFORE UPDATE trigger as belt-and-braces immutability
create or replace function public.profiles_freeze_privileged()
returns trigger
language plpgsql
security definer
set search_path = ''            -- required on every SECURITY DEFINER function; see BAAS-06
as $$
begin
  new.role    := old.role;
  new.credits := old.credits;
  new.is_pro  := old.is_pro;
  new.id      := old.id;
  return new;
end;
$$;

create trigger profiles_freeze_privileged
before update on public.profiles
for each row execute function public.profiles_freeze_privileged();

-- 3) Best: privileged attributes do not live in a client-writable table at all.
create table public.entitlements (
  user_id uuid primary key references auth.users(id),
  plan    text not null default 'free',
  credits int  not null default 10 check (credits >= 0)
);
alter table public.entitlements enable row level security;
create policy "read own entitlements" on public.entitlements
  for select to authenticated using ( (select auth.uid()) = user_id );
-- no INSERT/UPDATE/DELETE policy at all -> only the service role, inside a function, can write
revoke insert, update, delete on public.entitlements from anon, authenticated;
```

**Firestore equivalent**, using field-level comparison against the stored document:

```
match /profiles/{uid} {
  allow read:   if request.auth.uid == uid;
  allow update: if request.auth.uid == uid
    && request.resource.data.role    == resource.data.role
    && request.resource.data.credits == resource.data.credits
    && request.resource.data.isPro   == resource.data.isPro;
}
```

Firebase's `rules-conditions` guide documents exactly this shape (`request.resource.data.name == resource.data.name` — "prevents name modifications").

> The terser `request.resource.data.diff(resource.data).affectedKeys().hasOnly([...])` idiom is widely used but is marked **REPORTED** in the dossier — the rules-language reference could not be re-fetched to confirm it. Prefer the explicit field-by-field form above, which is documented verbatim.

**Detection:**

```sql
-- which roles can write which columns
select grantee, table_name, privilege_type, column_name
from information_schema.column_privileges
where table_schema = 'public'
  and grantee in ('anon','authenticated')
  and privilege_type in ('UPDATE','INSERT')
order by table_name, column_name;
```

Then the live PATCH probe above against your own row. A `200` with the value changed is the finding.

### 4.5 The other Lane B controls that carry the load

Because the database *is* the DAL, these are the substitutes doing the work the missing server would have done.

**Validation becomes constraints.** Note PostgreSQL's documented three-valued-logic trap: "a check constraint is satisfied if the check expression evaluates to true **or the null value**… they will not prevent null values in the constrained columns." **So every `CHECK` needs a matching `NOT NULL`.** And `GENERATED ALWAYS AS … STORED` is the fix for a client-set total, because "a generated column cannot be written to directly."

**Rate limiting becomes a counter table plus a trigger.** The AI default is a client-side guard, which runs on the attacker's browser using the attacker's clock:

```js
const [lastSent, setLastSent] = useState(0)
if (Date.now() - lastSent < 5000) return    // attacker's browser, attacker's clock
```

The version that actually runs:

```sql
create table public.rate_events (
  user_id uuid not null default auth.uid(),
  action  text not null,
  at      timestamptz not null default now()
);
create index on public.rate_events (user_id, action, at desc);
alter table public.rate_events enable row level security;   -- no policies: clients cannot touch it

create or replace function public.enforce_message_rate()
returns trigger language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  select count(*) into n
  from public.rate_events
  where user_id = auth.uid() and action = 'message'
    and at > now() - interval '1 minute';
  if n >= 10 then
    raise exception 'rate limit exceeded' using errcode = '55000';
  end if;
  insert into public.rate_events(user_id, action) values (auth.uid(), 'message');
  return new;
end;
$$;

create trigger messages_rate_limit
before insert on public.messages
for each row execute function public.enforce_message_rate();
```

Firestore's analogue uses `get()` against a throttle document, but budget it against the 10/20 document-access limits above.

**Back everything with money limits**, because a rate limit you wrote can be wrong and a spend cap cannot be argued with: Supabase Spend Cap, Firebase/GCP budget alerts plus a billing-disable function.

**What Supabase's rate limits do and do not cover.** Verified from the rate-limits table: email sends via `/auth/v1/signup`, `/auth/v1/recover`, `/auth/v1/user` are **2 emails/hour project-wide** with the built-in provider (a single shared budget across those three endpoints, not per endpoint and not per user); OTP sends `/auth/v1/otp` default **30/hour** project-wide; OTP/magic-link, signup-confirmation and password-reset each have a **60-second per-user window**. Not customisable at all: `/auth/v1/verify` 360/hour per IP, token refresh 1800/hour per IP, MFA challenge/verify 15/hour per IP, anonymous sign-ins 30/hour per IP. **Note what is absent from that table: the Data API.** There is no documented per-user rate limit on `/rest/v1/*`.

### 4.6 The Lane B ceiling, stated to the user

Stage 0 of the migration — enable RLS everywhere, per-command policies with both `USING` and `WITH CHECK`, `NOT NULL` + `CHECK` + enums + generated columns, `default auth.uid()` on ownership columns, column grants, `BEFORE UPDATE` immutability triggers, bucket MIME/size limits, and every Supabase Security Advisor lint cleared (`rls_disabled_in_public`, `security_definer_view`, `rls_references_user_metadata`) — gets the app to **as secure as a client + BaaS app can be**.

That is a real ceiling, not a stepping stone. If any trigger in §5 is YES, the ceiling is below where the app needs to be.

Include this verbatim for this audience: **do not skip Stage 0 on the promise of doing a real server later.** Adding a server later does not retroactively secure the data that PostgREST has been serving to the internet the whole time.

---

## 5. The seven observable triggers that end the client-only architecture

A skill can check these mechanically. **Any single YES means client-only is no longer a valid architecture** — not "should be improved," but "cannot be made correct."

| # | Trigger | What it forces |
|---|---|---|
| 1 | Does the app call any API that authenticates with a **bearer secret**? (OpenAI, Anthropic, Resend, SendGrid, Twilio, Stripe secret key, any partner API) | A shim function that holds the key |
| 2 | Does anything **outside your app need to POST to you**? (Stripe/Clerk/Resend/GitHub webhooks, provider callbacks) | A function. A browser cannot receive an inbound request. No workaround |
| 3 | Is there a value **on the user's own row that the user must not set**? (`credits`, `plan`, `role`, `verified`, `score`, `is_pro`) | Entitlements table with no client write policy + a function; or column grants + a trigger, if the value is derived purely from other columns |
| 4 | Does any action **cost money per invocation**? (model tokens, email, SMS, image generation, egress) | Metering that survives a hostile client. The DB-trigger counter is the floor; a function with a spend cap is the real answer |
| 5 | Does any rule depend on **state the requester cannot be shown**? (answer keys, other users' data, "visible only after the deadline," pricing you do not want scraped) | RLS can hide rows but cannot compute over rows the caller cannot read without a `SECURITY DEFINER` function — at which point you are writing server code in SQL anyway |
| 6 | Do you need to **email anything that is not a Supabase auth email**? | The built-in provider is **2 emails/hour project-wide**. Anything else needs an API key, which needs a shim |
| 7 | Do you need **per-user rate limits on data writes**, not just auth? | No platform control exists for `/rest/v1/*`. A DB trigger or a function |

Detection, in order. Triggers 1 and 2 are the dossier's own commands; 3, 4, 6 and 7 are constructed from the same signals. Trigger 5 is a design question, not a grep — ask whether any check reads a row the caller must not read.

```bash
# 1 — third-party API authenticated by a bearer secret
grep -rniE "api\.(openai|anthropic|resend|stripe|twilio|sendgrid)\.com" src/

# 2 — something outside your app must POST to you
grep -rn "whsec_" .
grep -rniE "webhook" src/ supabase/ 2>/dev/null
#    plus: check the provider dashboard for a configured endpoint

# 3 — a value on the user's own row the user must not set
grep -rnEi "\b(credits|plan|role|is_pro|is_admin|verified|tier|balance)\b" supabase/migrations/*.sql

# 4 — per-invocation cost (overlaps trigger 1)
grep -rniE "chat/completions|messages\.create|images/generations|/v1/audio" src/

# 6 — email that is not a Supabase auth email
grep -rniE "resend|sendgrid|postmark|nodemailer|mailgun" package.json src/

# 7 — a client-side "rate limit", which is the finding, not the control
grep -rniE "ratelimit|rate_limit|debounce|cooldown" src/
```

**Triggers 1, 2 and 4 are the ones this population hits within a week of launching.** The sequence is almost always the same: ship the SPA, add an AI feature (trigger 1 and 4 together), then add payments (trigger 2). Three weeks after launch the architecture is already invalid and nobody noticed, because nothing broke.

When you report a trigger, report it as an architecture finding, not a bug. The user has not made a mistake yet — they are about to, and the mistake will be putting the secret key in the bundle or the service-role key in a function that reads `user_id` from the request body.

---

## 6. Lane C — Python backend (FastAPI / Django / Flask)

Viable, with a genuinely different footgun set — not a translation of the JavaScript list.

The two moves that close most of it: `SECRET_KEY` read with the **bracket form** from the environment so it fails closed, and an explicit `ALLOWED_HOSTS`.

```python
# fail closed. os.environ.get(..., "dev-secret-key") fails OPEN.
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG", "") == "1"
ALLOWED_HOSTS = ["app.example.com", "www.example.com"]
```

### 6.1 `manage.py check --deploy`

**Django's own automated deploy audit is the best free automated posture audit in this whole document.** Run it before anything else on a Django project.

```bash
python manage.py check --deploy
```

There is no equivalent for Flask or FastAPI. On those, the greps in §6.6 are the substitute.

### 6.2 Flask `debug=True` is unauthenticated RCE (`PY-01`)

`debug=True` renders an interactive Python REPL in every stack frame. Flask's own docs call it "a major security risk."

The PIN is not protection. It is `sha3_256` over the username, module name, app class name, module file path, `uuid.getnode()` (the MAC address) and `get_machine_id()` (`/etc/machine-id`, boot_id, `/proc/self/cgroup`), plus a hardcoded salt — **so any file-read or SSRF primitive lets an attacker compute it offline.** A directory-traversal bug you would otherwise rate medium becomes remote code execution.

```bash
grep -rn "debug=True\|app.run(" . --include=*.py
curl -s -o /dev/null -w '%{http_code}\n' "$APP/console"        # Werkzeug console
curl -s "$APP/this-path-does-not-exist" | grep -i werkzeug     # traceback = debug on
```

Fix: no `app.run()` in the production entrypoint; run under gunicorn or uvicorn; never set `FLASK_DEBUG` or `debug=True` in a deployed environment.

### 6.3 Django `DEBUG=True` prints your database credentials (`PY-04`)

Django's settings docs state the traceback includes **"all the currently defined Django settings (from settings.py)"** — which is your `DATABASES` block, with the password, plus every custom-named API key. It also "will remember every SQL query it executes" (a memory DoS), and turns 404s into a full URL-pattern listing.

Why it matters more than it looks: the `SECRET_KEY` that read primitive exposes underwrites **all sessions** (non-cache backends), all `CookieStorage` messages, **all `PasswordResetView` tokens**, and any use of `django.core.signing`. One DEBUG page is superuser.

`ALLOWED_HOSTS = ['*']` is the agent's standard fix for the deploy 400, and it removes the guardrail that would have prompted a DEBUG review. It exists "to prevent HTTP Host header attacks," so with `['*']` a password-reset link built from the request host points at the attacker. Build absolute URLs from a `SITE_URL` constant, never from the request — Django warns the check is bypassable "if your code accesses the Host header directly from `request.META`."

```bash
grep -rn 'SECRET_KEY *= *os.environ.get' . --include=*.py   # the fail-open default
grep -rn 'ALLOWED_HOSTS *= *\[.\*.\]' . --include=*.py
git log -p --all -S 'django-insecure-'
```

Rotate via `SECRET_KEY_FALLBACKS`.

### 6.4 `fields = "__all__"` is mass assignment, and it is a time bomb (`PY-05`)

A `ModelForm` with `fields = "__all__"` (or only an `exclude`) accepts a POST parameter for **every editable model field**, rendered on the page or not. POST `is_staff=on` or `credits=99999` to the profile form and it saves.

Django's own docs: *"Failure to do so can easily lead to security problems when a form unexpectedly allows a user to set certain fields, especially when new fields are added to a model. Depending on how the form is rendered, the problem may not even be visible on the web page."* And: *"This fundamental approach is known to be much less secure and has led to serious exploits on major websites (e.g. GitHub)."*

The time-bomb property is what makes it worse than it reads: it is **correct when written** and becomes vulnerable the day someone adds `is_premium` to the model. DRF's `ModelSerializer` has the identical footgun.

```python
# vulnerable
class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = "__all__"        # or: exclude = ["password"]

# fixed — an explicit allowlist, never an exclude list
class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["display_name", "avatar", "bio"]
```

```bash
grep -rn 'fields\s*=\s*"__all__"' . --include=*.py
grep -rn 'exclude\s*=' . --include=*.py
```

FastAPI's twin: omitting `response_model` serializes the whole ORM object — `password_hash`, `stripe_customer_id`, `reset_token`, other users' emails. FastAPI's docs state that `response_model` is a filter: "FastAPI filters out all data not declared in the output model." And Pydantic is **not** an authorization boundary — it "won't error when you provide extra data, and these values will simply be ignored" by default. Use a separate `UserOut` on every read route, `ConfigDict(extra='forbid')` as defence in depth, and **explicit field-by-field assignment as the actual control** — never `User(**payload.model_dump())`.

### 6.5 FastAPI ships `/docs`, `/redoc` and `/openapi.json` by default (`PY-08`)

Zero configuration required. `GET /openapi.json` hands over every route, method, parameter, enum and response model — including admin and internal routes — and Swagger UI is a click-to-exploit console against it.

**The agent literally tells the user to open `/docs` to test the API**, so it is never turned off. On a project with `PY-05`-shaped holes (a route that omits `Depends` entirely, or an optional `token: str | None = None` dependency that lets anonymous requests fall through), `/docs` is a guided tour.

```python
app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,   # disables both UIs
)
```

```bash
for p in /docs /redoc /openapi.json; do
  printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "$APP$p"
done
```

Useful inversion during an audit: **enumerate the real attack surface from the app's own `/openapi.json`** before you turn it off.

### 6.6 PyPI has no `--ignore-scripts` equivalent (`PY-02`)

This is the Python-specific delta over the npm supply-chain chapter, and the reason `npm`-shaped advice does not port.

CPython's `site` module executes lines in a `.pth` file that begin with `import `, **"at every Python startup, regardless of whether a particular module is actually going to be used."** So the payload does not need `setup.py`, does not need you to import the package, and re-runs on every interpreter launch.

The April 2026 incident: PyPI's own report says malicious `litellm` and `telnyx` releases *"ran on install, harvesting sensitive credentials."* GHSA-98x5-vq43-vc5p names the mechanism — `litellm==1.82.8` shipped `litellm_init.pth` stealing environment variables, AWS/GCP/Azure credentials, SSH keys and kube configs to `models.litellm.cloud`. **It was live 2h32m with over 119,000 downloads.** Amplified by unbounded version ranges (`semantic-router` declared `litellm>=1.61.3` with no ceiling).

`pip-audit` would not have caught it, and documents so itself: it *"is not a static code analyzer"* and *"cannot defend against malicious packages."*

Controls that actually apply:

```toml
# pyproject.toml — a cooldown is the control that works
[tool.uv]
exclude-newer = "P3D"
```

```bash
uv lock                                   # or: pip-compile --generate-hashes
pip install --require-hashes -r requirements.txt
# never: sudo pip install ...            # one venv per project

# audit an existing environment for the .pth channel
grep -rlE '^import ' "$VIRTUAL_ENV"/lib/python*/site-packages/*.pth 2>/dev/null

# before adding a package: 404 means the model hallucinated the name
pip index versions <name>
```

pip 26.1's `uploaded-prior-to` is the pip-native equivalent of the uv cooldown.

### 6.7 Lane C deployment defaults

The `python:3.12` image runs as **root** and generated Dockerfiles add no `USER`, so any RCE lands as uid 0 with write access to `site-packages` — which is exactly where the `.pth` backdoor above gets dropped for persistence.

```bash
grep -rn "debug=True\|app.run(" . --include=*.py
grep -rn "yaml.load(\|pickle.loads(\|torch.load(" . --include=*.py
grep -rn "shell=True" . --include=*.py
grep -rn "render_template_string(" . --include=*.py
grep -rn "requests.get(\|requests.post(" . --include=*.py | grep -v timeout
grep -rLn '^USER ' Dockerfile*
python manage.py check --deploy
```

Two async footguns complete the set: code inside `async def` runs on the event loop, so a sync `requests.get`, a blocking DB driver, a bcrypt hash or a PIL resize **stalls every concurrent request in that worker** (a plain `def` path operation is run in a threadpool instead); and `BackgroundTasks` is in-process with no queue, retry, persistence or concurrency limit — on an unauthenticated endpoint that is denial-of-wallet with unbounded fan-out, and a deploy silently drops everything queued.

---

## 7. Choices at stage zero that delete whole bug classes

These are the highest-leverage decisions in the entire skill, because each one removes a category of vulnerability rather than fixing an instance of it. They are all made in the first ten minutes, usually by accepting an AI's default, and several are expensive to reverse.

### 7.1 OAuth-only or passkeys, rather than storing a password

Use a managed provider. **No password column means the reset-token, enumeration, credential-stuffing, hashing and revocation bug classes do not exist for you.** There is nothing to audit because there is nothing there.

Storing one signs you up to the full obligation set in **NIST SP 800-63B-4** (final 31 July 2025):

- 15-character minimum for single-factor
- comparison against a blocklist of compromised passwords
- **no** composition rules
- **no** periodic rotation
- **no** password hints, **no** knowledge-based authentication

AI-generated auth implements roughly none of that, and adds two things of its own: `Math.random()` reset tokens, stored in plaintext.

If you truly must hash: `argon2` with argon2id, `memoryCost: 19456, timeCost: 2, parallelism: 1` — one of OWASP's five equivalent-security configurations.

### 7.2 Stripe Checkout or Payment Element, rather than your own card inputs

**One line of frontend code decides your PCI scope.**

| Integration | Self-assessment questionnaire |
|---|---|
| Checkout, or Elements with **Stripe-hosted** payment fields; mobile SDK where card data goes directly to Stripe without touching your servers | **SAQ A** — "your customers' card information never touches your servers" |
| Stripe.js passing card data entered in a form **hosted on your own site** | **SAQ A-EP** |
| Card data collected exclusively through Stripe Terminal | **SAQ C** |
| Manual card entry in the Stripe Dashboard | **SAQ C-VT** |
| Passing card information directly to Stripe's API (raw PAN) | **SAQ D** — which Stripe warns can mean "more than 300 security controls" |

The mechanism producing SAQ A is architectural, not contractual: the cardholder enters payment information in a field that originates directly from Stripe's PCI DSS-validated servers — a cross-origin iframe your JavaScript cannot read. SAQ D also makes every XSS on your site a Magecart primitive.

**Sourcing caveat, carry it:** this SAQ mapping is sourced to **Stripe and Vercel documentation, not to the PCI Security Standards Council** — the Council's SAQ documents were not reachable during research. The dossier deliberately makes no claim about SAQ question counts, nor about PCI DSS v4.x requirements 6.4.3 or 11.6.1 and their applicability to SAQ A. Anyone making a scope decision confirms it against the current SAQ A from the Council and, ultimately, with their **acquirer** — the acquirer, not the processor, determines validation requirements.

The check that actually proves scope, and the only one worth running:

> Open DevTools → Network on your live checkout, type a test card, and confirm **no request to your own origin contains the digits**. If the card number appears in any request whose Host is your domain, you are in SAQ A-EP or SAQ D territory regardless of what your integration is called.

### 7.3 A verification vendor plus a boolean, rather than storing a government ID

Store `is_verified: true` and a vendor reference. Do not store the artifact.

Holding the image or template triggers CCPA sensitive personal information, GDPR Art. 9 if it is biometric, and COPPA's amended 16 CFR 312.2 biometric identifiers (the amended Rule — FR 2025-05904, published 2025-04-22, effective 2025-06-23, compliance by 2026-04-22 — adds biometric identifiers including facial templates and voiceprints).

The general form of this move: **store derived assertions, not raw facts.** `is_over_18`, not `date_of_birth`.

If an image is genuinely unavoidable: private bucket, seconds-long signed URLs, server-side authorization before minting the URL, a hard TTL, and **the deletion job written before the upload feature ships**.

### 7.4 Managed Postgres with RLS, rather than a self-hosted database on a VPS

`INFRA-03`. Docker's docs: **"Publishing container ports is insecure by default… it becomes available not only to the Docker host, but to the outside world as well."** `-p 8080:80` publishes to all interfaces, IPv4 and IPv6.

The trap that catches everyone is that **you did configure ufw and it does nothing.** Docker's docs again: traffic to a published container port "gets diverted before it goes through the ufw firewall settings," because **"Docker routes container traffic in the `nat` table, which means that packets are diverted before it reaches the `INPUT` and `OUTPUT` chains that ufw uses."** So `ufw status` prints `5432 DENY` and Postgres is still answering the internet.

Every `docker-compose.yml` an AI writes maps 5432/6379/27017 so `psql -h localhost` works from the laptop, and that file gets copied to the VPS unchanged. Pair it with the default credentials that usually come with it — Redis `--protected-mode no`, Postgres `trust` auth which "allows anyone that can connect… to login as any PostgreSQL user they wish, without the need for a password" — and there is no exploitation step at all.

If you self-host anyway:

```yaml
# remove `ports:` from database services entirely — they are reachable
# by service name on the compose network
services:
  db:
    image: postgres:16
    # ports:                    <-- delete this block
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
  app:
    build: .
    ports:
      - "127.0.0.1:3000:3000"   # bind to loopback, put a reverse proxy in front
```

And enforce a **cloud-provider firewall outside the host**, because that is the layer Docker's nat rules cannot bypass.

```bash
# prove it from outside the box, not from on it
nmap -Pn -p 5432,6379,27017,9200 "$VPS_IP"
redis-cli -h "$VPS_IP" ping        # PONG without AUTH is the finding
```

### 7.5 Your own domain, rather than an enumerable platform namespace

`INFRA-02`. `*.lovable.app`, `*.replit.app`, `*.base44.app` and `*.netlify.app` are **enumerable namespaces**. Red Access scanned ~380,000 vibe-coded assets across those platforms and found **~5,000 leaking sensitive data**. Nobody had to find your app; they enumerated the namespace and your app was in it.

You also share reputation with whatever phishing is hosted next door. Guardio's VibeScamming benchmark scored Lovable **1.8/10** for abuse resistance and produced a Microsoft login clone at `login-microsft-com.lovable.app`.

Platforms also default to public and get search-indexed, and Replit's response to this was that public apps being accessible is "expected behavior." So the fix is: deny-by-default middleware plus `X-Robots-Tag: noindex`; move the real product to your own domain and certificate **before onboarding users**; publish DMARC `p=reject`; monitor crt.sh for lookalikes.

```bash
# what is already indexed on the shared namespace
# (run as a site: search on the platform apex, e.g. site:lovable.app)
curl -s "https://crt.sh/?q=%25.example.com&output=json" | head -c 2000   # your own lookalikes
```

A custom domain does not make the app secure. It removes it from a list that gets scanned continuously, and it decouples your reputation from a shared parent domain. Note the sibling risk it introduces: a **parent-domain cookie** plus a dangling subdomain is `INFRA-08`, subdomain takeover.

`NOTVULN-13` applies and you should not over-report here: a discoverable subdomain is **not** a vulnerability. Certificate Transparency makes every hostname public by design. The finding is what is *on* it.

---

## 8. Named defaults for Lane A

So the agent has something concrete to install rather than a philosophy.

| Purpose | Package / setting |
|---|---|
| Environment validation, fail loud at boot | `@t3-oss/env-nextjs` + `zod` |
| Keep server code out of the bundle at build time | `server-only` |
| Password hashing, **only if you must store one** (§7.1) | `argon2`, argon2id, `memoryCost: 19456, timeCost: 2, parallelism: 1` |
| Rate limiting | `@upstash/ratelimit` on Redis |
| Secret scanning, pre-commit | `gitleaks` v8.30.1 |

### 8.1 The CI gate

```bash
semgrep scan --error && gitleaks dir -v . && osv-scanner scan -r .
```

Use `semgrep scan --error` specifically. Bare `semgrep ci` exits 0 **"regardless of whether there were findings"** — it is a *reporting* step that will never fail the build, and putting it in a gate produces a green check that means nothing.

```yaml
# .github/workflows/security.yml
name: security
on: [pull_request, push]
permissions:
  contents: read                          # narrow by default; widen per job only where needed
jobs:
  static:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pipx install semgrep && semgrep scan --error
      - uses: gitleaks/gitleaks-action@<40-char-sha>
      - run: |
          curl -sSfL https://raw.githubusercontent.com/google/osv-scanner/main/scripts/install.sh | \
            sh -s -- -b /usr/local/bin        # or install from a pinned release artifact
          osv-scanner scan -r .
      - run: npm ci --ignore-scripts && npm rebuild <only-what-genuinely-needs-a-build>
      - run: npm run build && ! grep -rqE 'sb_secret_|service_role|sk_live_|sk-ant-' .next/static/
```

Pin the `gitleaks-action` to a 40-character commit SHA, not a tag. And note the ruleset trap that undoes all of it: GitHub rulesets let you *"allow certain users to bypass the rules… users with a certain role, such as repository administrator"*, and *"Anyone with read access to a repository can view its active rulesets."* A solo builder who adds Repository admin to the bypass list exempts the single most phishable account **and publishes that fact to anyone who can read the repo.** Keep `bypass_actors` empty.

### 8.2 CodeQL, and the setting that matters more than the scanner

Enable CodeQL through **Settings → Advanced Security → Code Security → CodeQL analysis → Default** (JavaScript/TypeScript supported, zero YAML).

Then make **"Code scanning results" a required status check in branch protection.** GitHub's docs: that check *"may be a required check that prevents pull requests from being merged until it passes."*

**Making the check required is the single highest-leverage setting in this document.** It converts everything above from advisory to enforcing — from "a tool that produces a report someone might read" into "a merge that cannot happen."

**And be honest about what it will not catch.** Verified by directory listing through the GitHub contents API: CodeQL's JavaScript security query pack (`javascript/ql/src/Security/`) has directories for **56 CWEs**, and there is **no `CWE-285`, no `CWE-863`, and no `CWE-639` directory**. The `CWE-862` directory contains exactly two files — `EmptyPasswordInConfigurationFile.ql` and its `.qhelp`. That is the entirety of CodeQL's JavaScript coverage of "Missing Authorization."

Which means: **static analysis cannot find your broken access control, and you should stop expecting it to.** Missing authorization is the *absence* of a check the tool cannot know was required — the intended policy exists only in the founder's head. `zap-baseline.py` is passive and single-identity, so it never has a second account and can never find a BOLA. Nuclei's fuzzing engine derives everything from one request/response pair and defines no multi-identity primitive; BOLA is by definition a two-identity property.

Authorization is verified by the two-account replay harness, not by a scanner. That lives in the audit toolkit, not here.

---

## 9. Routing: what the lane changes about the audit

| Lane | Fixes are written as | Do not emit |
|---|---|---|
| **A** | TypeScript in a server-only DAL; checks inside every action body and route handler | Nothing lane-specific; but never accept a middleware/proxy guard as the control |
| **B** | SQL migrations and Security Rules. RLS policies, column grants, `CHECK` + `NOT NULL`, triggers, bucket policies | DAL advice, `safeParse` at "the trust boundary", `@upstash/ratelimit`, webhook signature verification. None of it can run. Say so explicitly instead |
| **B in migration** | SQL for the tables; DAL-style advice **only** for the function files that exist | Advice that assumes the function set covers paths the client still hits directly |
| **C** | Python. `manage.py check --deploy` output first, then the settings and form/serializer allowlists | npm supply-chain advice — `--ignore-scripts` has no pip equivalent (`PY-02`) |

Two closing rules for reporting in any lane:

**Flag the missing control, not the visible key.** A publishable key in the bundle is expected. Supabase's CEO said it plainly: *"Finding a Supabase project URL and anon key in client code is expected, as both are designed to be public."* The fastest way to destroy a builder's trust is to report it as a critical leak — and once you have cried wolf, they will ignore the `BAAS-01` that actually matters. The dossier's own fact-checkers caught the research filing the Moltbook breach under "leaked secrets" when the exposed credential was a publishable key that was supposed to be there, and the real defect was missing RLS.

**Prove the condition before reporting it.** Every false positive above is safe *conditionally*. Run the probe:

```bash
curl -s "https://<ref>.supabase.co/rest/v1/profiles?select=*&limit=3" -H "apikey: $ANON"
```

Rows back means `BAAS-01`. `[]` or a 401 means the key is doing exactly what it was designed to do, and there is no finding here.
