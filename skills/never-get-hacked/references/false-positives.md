# Read before reporting anything: things that look like vulnerabilities but are not

Load this file **before you write a single finding into the report**, on every audit, at every stage. It is the last gate between the audit output and the user's eyes. The fastest way to destroy a builder's trust in a security tool is to tell them their `NEXT_PUBLIC_SUPABASE_ANON_KEY` is a critical leak. It is not, it was never meant to be secret, and once you have cried wolf about it they will ignore the finding that actually matters. This failure mode is not hypothetical: the dossier's own fact-checkers caught the research **filing the Moltbook breach under "leaked secrets" when the exposed credential was a publishable key that was supposed to be there** — the real defect was missing RLS. Every entry below is a thing that *is* safe, but only *conditionally*. Your job is to check the condition, not to report the shape.

---

## The governing rule

> **FLAG THE MISSING CONTROL, NOT THE VISIBLE KEY.**
>
> Every item in this file is safe **conditionally**. Report the condition.

Supabase's CEO, Paul Copplestone (`kiwicopple`), said it directly in the Hacker News thread about a scan that reported 11.04% of indie launches "exposing Supabase credentials":

> *"Finding a Supabase project URL and anon key in client code is expected, as both are designed to be public."*

Three operating consequences for the agent:

1. **Never report a shape. Report a verified condition.** "I found `pk_live_` in the bundle" is not a finding. "Your checkout endpoint accepts a client-supplied `unit_amount`" is a finding.
2. **Never rotate a public key as remediation.** Rotating a publishable key while the authorization gap behind it remains is theater — and it costs the user a deploy and their confidence in you.
3. **When a check is ambiguous, run the command in this file rather than guessing from the string.** Every entry has one.

### Set these once before running any check in this file

```bash
APP=https://yourapp.example
REF=<your-supabase-project-ref>          # from the bundle
ANON=<your-supabase-publishable-key>     # from the bundle — public by design
```

### Decode, don't guess

A Supabase legacy JWT states its own privilege level. Base64url payloads are usually unpadded, and both GNU and BSD `base64 -d` reject unpadded input — the naive one-liner silently prints nothing, so pad it:

```bash
decode_jwt() {
  p=$(printf '%s' "$1" | cut -d. -f2 | tr '_-' '/+')
  while [ $(( ${#p} % 4 )) -ne 0 ]; do p="${p}="; done
  printf '%s' "$p" | base64 -d | jq .
}
decode_jwt 'eyJhbGciOi...'
# {"iss":"supabase","ref":"abcdefgh","role":"anon","iat":...}          -> fine, now go check RLS
# {"iss":"supabase","ref":"abcdefgh","role":"service_role","iat":...}  -> CRITICAL, rotate now
```

New-format keys need no decoding: `sb_publishable_…` is safe by design, `sb_secret_…` is not.

### The public-by-design allowlist, with the control each one requires

| Key | Format | Public by design? | The control that must exist |
|---|---|---|---|
| Supabase publishable / anon | `sb_publishable_…` / legacy `eyJ…` with `"role":"anon"` | **Yes** — docs: "Safe to expose online: web page, mobile or desktop app, GitHub actions, CLIs, source code." | RLS on every table in an exposed schema, plus least-privilege grants |
| Supabase secret / service_role | `sb_secret_…` / `eyJ…` with `"role":"service_role"` | **No.** Carries Postgres `BYPASSRLS`. | Server-side only. Never in a bundle. |
| Firebase web config (`apiKey`, `authDomain`, `projectId`, `appId`) | `AIza…` | **Yes** — docs: keys restricted to Firebase services "do not need to be treated as secrets" | Security Rules + App Check |
| **Gemini Developer API key** | `AIza…` (**same shape**) | **No.** Firebase docs: it "should *never* be included in your code or configuration files." | Server-side proxy |
| Stripe publishable | `pk_test_…` / `pk_live_…` | **Yes** — Stripe's table: "Safe to expose: Yes … you can put in front-end code." | All authorization, amounts and prices decided server-side |
| Stripe secret / restricted | `sk_…` / `rk_…` / `sk_org_…` | **No** for all three. | Server-side; access policy on live keys |
| Clerk publishable | `pk_test_…` / `pk_live_…` | **Yes** — frontend key | Session verification server-side; `sk_…` never leaves the backend |
| PostHog project API key | `phc_…` | **Yes** — "safe to expose in client-side code." | Distinct from personal API keys, project *secret* API keys, and the feature-flags secure key — all secret |
| Mapbox public token | `pk.…` | **Yes** | URL restrictions (max 100 per token) |
| Mapbox secret token | `sk.…` | **No.** | Server-side only |
| Google Maps JS API key | `AIza…` | **Yes, necessarily** — it is in the script URL | HTTP referrer + API restrictions in Google Cloud Console |
| Analytics IDs (GA `G-…`, Plausible domain, Sentry public DSN) | various | **Yes.** | Sentry DSN accepts events only; consider inbound filters and rate limits |

---

## `NOTVULN-01` — Supabase publishable / anon key in the bundle

**What it looks like.** `sb_publishable_…` in a shipped chunk, or `NEXT_PUBLIC_SUPABASE_ANON_KEY` / `VITE_SUPABASE_ANON_KEY` in `.env.local` and inlined into the build, or a legacy JWT whose payload decodes to `"role":"anon"`.

**Why it is not a finding.** Supabase's docs list the publishable key as safe to expose on "web page, mobile or desktop app, GitHub actions, CLIs, source code." A browser client cannot talk to the data API without it. Moltbook is the clean demonstration of the trap: the exposed credential was `sb_publishable_…`, exactly where it belonged, and roughly 4.75M records fell out anyway. Nobody leaked a secret. Somebody forgot RLS.

**The control that must exist.** RLS enabled on every table in an exposed schema, **plus** least-privilege grants — grants and policies are two separate checks (`BAAS-07`), and models essentially never emit `revoke`.

**Check the real condition:**

```bash
curl -s "https://$REF.supabase.co/rest/v1/profiles?select=*&limit=3" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
```

| Outcome | Report |
|---|---|
| Rows come back for a table that should be private | `BAAS-01` CRITICAL — missing RLS. Not a leaked key. |
| Rows come back for a genuinely public table (published posts, a public directory) | Nothing |
| `42501` or an empty set | Nothing |

**Do not rotate it as remediation.** The fix is `alter table … enable row level security;` plus per-command policies, then `revoke all on <t> from anon, authenticated;` and grant back explicitly.

---

## `NOTVULN-02` — Firebase web config (`apiKey`, `authDomain`, `projectId`, `appId`)

**What it looks like.** An `AIza…` string sitting in `firebaseConfig` in client source, usually alongside a builder panicking about "my exposed API key."

**Why it is not a finding.** Firebase's docs: keys restricted to Firebase services "do not need to be treated as secrets, and it's safe to include them in your code or configuration files" — they "only *identify* your Firebase project and app." A builder worrying about the visible key while running test-mode rules has the priority exactly inverted.

**The control that must exist.** Firestore / RTDB / Storage Security Rules, plus App Check. A project in test mode (`allow read, write: if true`) with a public config is fully open.

**⚠️ The genuine trap in the same shape.** A **Gemini Developer API key has the identical `AIza` prefix**, and Firebase's docs say it "should never be included in your code or configuration files." Prefix matching cannot tell these apart. **Check what the key is restricted to, not the prefix.**

**Check the real condition — three steps, in this order:**

```bash
# 1. Which SDK consumes this key? A key handed to a Gemini/Generative-AI client
#    in browser code is a real finding regardless of its prefix.
grep -rn "AIza" --include='*.ts' --include='*.tsx' --include='*.js' --include='*.jsx' \
  --include='*.env*' . --exclude-dir=node_modules --exclude-dir=.git
grep -rniE "generativelanguage|@google/gener|GoogleGenerativeAI|GEMINI_API_KEY" \
  . --exclude-dir=node_modules --exclude-dir=.git

# 2. Are the rules actually closed? (unauthenticated probe, safe on production)
curl -s "https://<project>-default-rtdb.firebaseio.com/.json" -o /dev/null -w '%{http_code}\n'
curl -s "https://firestore.googleapis.com/v1/projects/<project>/databases/(default)/documents/users" | head
curl -s "https://firebasestorage.googleapis.com/v0/b/<project>.appspot.com/o" | head
```

3. In Google Cloud Console → APIs & Services → Credentials, open the key and read its **API restrictions**. Restricted to Firebase services → safe in client code. Unrestricted, or permitting the Generative Language API → it is a secret sitting in public.

| Outcome | Report |
|---|---|
| `200` with data on an unauthenticated request | `BAAS-01`-class CRITICAL — open Security Rules. This is the Tea app shape: ~72,000 images including ID-verification selfies and driver's licenses, then a second datastore with ~1.1M private messages. |
| The key reaches a Gemini/Generative-AI SDK in client code | `SECRET-01`-class CRITICAL — server-side proxy required |
| Firebase-restricted key, rules deny | Nothing |

---

## `NOTVULN-03` — Stripe / Clerk publishable keys (`pk_test_`, `pk_live_`)

**What it looks like.** `pk_live_…` visible in the page source or in `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`.

**Why it is not a finding.** Stripe's own table marks publishable keys "Safe to expose: Yes… you can put in front-end code." Clerk's publishable key identifies the Clerk application to the frontend. Both are meant to be there.

**The control that must exist.** All authorization, and **every amount and price**, decided server-side (`PAY-03`). `sk_`, `rk_` and `sk_org_` are the opposite — Stripe: "Only publishable keys are safe to expose outside your application's backend."

**Check the real condition:**

```bash
# (a) Is a real secret key in the shipped output?
npm run build
grep -roE 'sk_live_[A-Za-z0-9]+|sk_test_[A-Za-z0-9]+|rk_(live|test)_[A-Za-z0-9]+|sk_org_[A-Za-z0-9]+' \
  .next/static/ dist/ build/ 2>/dev/null

# (b) Does the server take price or amount from the client?
grep -rnE "price_data|unit_amount|req\.body\.(amount|price|priceId|plan|credits)|body\.(amount|priceId)" \
  app/ src/ pages/ api/ 2>/dev/null --exclude-dir=node_modules
```

| Outcome | Report |
|---|---|
| (a) hits | `SECRET-01` / secret-key-in-bundle, CRITICAL, rotate at the provider first |
| (b) shows `price_data` built from request body, or `metadata.credits` taken as a value | `PAY-03` CRITICAL — Stripe emits a genuine, correctly signed event for the tampered value, so signature verification does not save you |
| Only `pk_` present, server resolves an opaque `planKey` against a hardcoded catalog | Nothing |

---

## `NOTVULN-04` — PostHog `phc_`, Sentry public DSN, GA `G-…`, Plausible domain, Mapbox `pk.`, Google Maps `AIza`

**What it looks like.** A secret scanner lighting up on half the analytics stack.

**Why it is not a finding.** All of these are designed for client-side use. Their presence is the product working.

**The controls that must exist** — these are per-vendor, and the missing restriction is the finding, not the key:

| Key | The real finding to check for |
|---|---|
| PostHog `phc_` | Confusion with the **personal API key**, the **project secret API key**, or the **feature-flags secure API key** — all three are secret. Optionally restrict authorized URLs in project settings. |
| Sentry public DSN | Accepts events only. Absence of inbound filters / rate limits is a cost and noise issue, not a data exposure. |
| Mapbox `pk.` | Missing **URL restrictions** (max 100 URLs per token) so a scraped token cannot be billed from another origin. Mapbox itself calls URL restrictions "a best-effort mitigation technique"; they do not apply to mobile SDK requests or `noreferrer` origins. `sk.` tokens are server-only. |
| Google Maps `AIza` | An **unrestricted key is a billing liability**. Needs HTTP referrer restrictions **plus** API restrictions. That is the finding — not its presence in the script URL, where it must be. |

**Check the real condition:**

```bash
# Distinguish PostHog's public key from its secret siblings
grep -rnE "phx_|phs_|POSTHOG_PERSONAL_API_KEY|POSTHOG_(SECRET|PROJECT_SECRET)" \
  . --exclude-dir=node_modules --exclude-dir=.git
# Mapbox secret tokens must never appear in client code or build output
grep -roE 'sk\.[A-Za-z0-9._-]{20,}' .next/static/ dist/ build/ src/ app/ 2>/dev/null
```

Then read the restriction settings in each vendor dashboard: Google Cloud Console → Credentials (referrer + API restrictions), Mapbox account → Tokens → URL restrictions, PostHog project settings → authorized URLs.

| Outcome | Report |
|---|---|
| A secret-tier sibling key in client code or build output | CRITICAL, rotate at the provider |
| Public key present, restrictions absent | MEDIUM, framed as **billing/abuse exposure**, never as data exposure |
| Public key present, restrictions configured | Nothing |

---

## `NOTVULN-05` — schema enumeration: the Supabase OpenAPI root, GraphQL introspection, guessable table names

**What it looks like.** `GET /rest/v1/` returning a full OpenAPI description — every table, view and RPC — to anyone holding the anon key. Or `/graphql/v1` answering an introspection query. Or an attacker guessing `users`, `profiles`, `orders` on the first try.

**Why it is not a finding.** This is inherent to PostgREST, not a bug in your app. **Hiding it is not a fix.** The tables being introspectable only matters when the tables lack RLS. Turning off `openapi_mode` reduces attacker convenience by minutes. The same applies to Firebase: your project ID ships in every bundle and APK, so discovery is free and continuous.

> **Enumeration returning NOTHING is the goal. Enumeration being impossible is not.**

**The control that must exist.** RLS on the tables behind the schema, plus revoked default grants. Report `BAAS-01` — and report the enumeration only as *context* for it, never as a standalone critical.

**⚠️ The copy-paste error to catch.** `openapi_mode` is set on the **`authenticator`** role, not on `anon`. PostgREST reads in-database config from `authenticator`, so the widely-pasted `alter role anon set pgrst.openapi_mode …` does nothing at all. Valid values are `follow-privileges`, `ignore-privileges`, `disabled`. Note also that PostgREST now discourages `ALTER ROLE` configuration and Supabase Cloud may not honour it — which is another reason this is not the fix.

```sql
-- Correct role, if you want it at all. This is hardening, not remediation.
alter role authenticator set pgrst.openapi_mode = 'disabled';
```

**Check the real condition — enumerate, then read every table you find:**

```bash
# list the surface
curl -s "https://$REF.supabase.co/rest/v1/" -H "apikey: $ANON" | jq '.definitions | keys[]'
# GraphQL introspection over the same data API
curl -s -X POST "https://$REF.supabase.co/graphql/v1" -H "apikey: $ANON" \
  -H 'Content-Type: application/json' -d '{"query":"{__schema{types{name kind fields{name}}}}"}'
# then, for EVERY table the first command printed:
curl -s "https://$REF.supabase.co/rest/v1/<table>?select=*&limit=1" -H "apikey: $ANON"
```

| Outcome | Report |
|---|---|
| Enumeration works **and** a private table returns rows | `BAAS-01` CRITICAL — cite the enumeration as chain step A, not as its own finding |
| Enumeration works, every table returns `42501` or empty | Nothing. This is the target state. |
| Enumeration blocked, tables still readable | `BAAS-01` CRITICAL. Hiding the index changed nothing. |

**Related note kept honest:** `/rest-admin/v1/schema_cache` reachability on Supabase Cloud is **REPORTED**, from two secondary sources, and is architecturally implausible since PostgREST's admin server is not meant to be internet-facing. Never build a detection step that depends on it alone — the OpenAPI root, GraphQL introspection and the schema error-message oracle all work without it.

---

## `NOTVULN-06` — `sb_secret_` keys returning 401 from a browser

**What it looks like.** A leaked `sb_secret_…` key that, when someone pastes it into the browser console, returns 401 — and the conclusion "so it's fine."

**Why it is not a finding — and why the inverse is also wrong.** Supabase's new secret keys reject requests whose `User-Agent` looks like a browser. This is a real guardrail **against accidents** and **is not a mitigation**. `User-Agent` is client-supplied, and an attacker who has extracted the key uses `curl`. Both errors are live here:

- **Do not** downgrade a leaked secret key because of the 401.
- **Do not** treat the 401 as evidence the key is safe.

**The control that must exist.** The secret key never reaches the client at all: it lives in an Edge Function or a server route, with no public env prefix, in a module that imports `server-only`.

**Check the real condition — ask whether the key still works from a non-browser client:**

```bash
# 1. Is a secret key in the shipped output at all?
npm run build
grep -roE 'sb_secret_[A-Za-z0-9_-]+|service_role' .next/static/ dist/ build/ 2>/dev/null
# any bundled JWT: decode and read the role claim
echo '<the-eyJ...-token>' | cut -d. -f2 | base64 -d 2>/dev/null | jq .role

# 2. If one is found, prove it is live — curl sends no browser User-Agent.
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://$REF.supabase.co/rest/v1/<table>?select=*&limit=1" \
  -H "apikey: <the-sb_secret_key>" -H "Authorization: Bearer <the-sb_secret_key>"
```

| Outcome | Report |
|---|---|
| `200` from step 2 | `BAAS-04` CRITICAL. The key carries `BYPASSRLS`; every policy in the project is irrelevant. Rotate at the provider **first**, then clean history. |
| `"role":"service_role"` in a decoded bundled JWT | `BAAS-04` CRITICAL, same treatment |
| Browser console shows 401 but curl shows 200 | Still `BAAS-04` CRITICAL. The 401 is the accident guardrail doing its job, nothing more. |
| `"role":"anon"` | Nothing — go check RLS instead (`NOTVULN-01`) |

Supabase auto-revokes secret keys it detects pushed to GitHub — "When a leak is detected, we immediately revoke the key and notify the project owner" — but do not rely on that as your control either.

---

## `NOTVULN-07` — a JWT visible in the browser

**What it looks like.** A session JWT in a cookie, in an `Authorization: Bearer` header, or printed in a network tab.

**Why it is not a finding.** That is how the application works. Every session-based app has a credential in the browser; the alternative is no sessions.

**"There is a JWT in the browser" is not itself a finding.** The findings in the neighbourhood are specific, and each one is a different report:

| The specific condition | ID | Severity | Why it is real |
|---|---|---|---|
| The token lives in `localStorage` | `CLIENT-04` | HIGH | Readable by any JS on the origin, so one XSS becomes a stolen bearer credential replayable offline until expiry. OWASP: "Do not store session identifiers in local storage as the data is always accessible by JavaScript." This is a **blast-radius control, not an XSS fix** — say so. |
| The server **decodes** rather than **verifies** | `AUTHN-02` | CRITICAL | `jwt-decode` and `jwt.decode()` verify nothing. This was a live component of CVE-2026-33640, where Outline's rate limiter "does not verify but decoded only" the JWT it keyed on. |
| The payload carries `"role":"service_role"` | `BAAS-04` | CRITICAL | `BYPASSRLS`. Every policy in the project is irrelevant. |
| Expiry lives only in cookie `Max-Age`, not inside the signed token | `AUTHN-06` | HIGH | Cookie `Max-Age` is a browser hint, not an authorization control. CVE-2025-46344 (`@auth0/nextjs-auth0` 4.0.1–4.5.0, fixed 4.5.1) omitted `.setExpirationTime` on the session JWE — "while the session cookie may expire or be cleared, the JWE remains valid." Scored Moderate, CVSS 4.0 = 4.9; cite it as an illustration, not a critical. |

**Check the real condition:**

```bash
# storage choice
grep -rnE "localStorage\.(set|get)Item\(.*(token|jwt|session|access)" src/ app/ pages/ 2>/dev/null
# decode-vs-verify
grep -rn "jwt-decode\|jwt\.decode(" src/ app/ pages/ api/ 2>/dev/null
grep -rn "jwt.verify(" . --exclude-dir=node_modules | grep -v algorithms
grep -rniE "(JWT_SECRET|AUTH_SECRET)\s*[=:]\s*['\"][^'\"]{0,31}['\"]" . --exclude-dir=node_modules
# does logout actually revoke? log in, copy the cookie, log out, then:
curl -si "$APP/api/me" -H "Cookie: <the-cookie-you-copied-before-logout>" | head -1
```

The logout replay is the highest-value check of the four: if the request still returns `200` after logout, logout revokes nothing (`AUTHN-06`).

---

## `NOTVULN-08` — `anon` and `authenticated` roles existing in `pg_policies`

**What it looks like.** A policy list full of `{anon}` and `{authenticated}` and an audit tool calling it "public access."

**Why it is not a finding.** Their presence is the normal Supabase model. Every project has them.

**The controls that must exist.** The findings are specific, and all three are visible in the same two queries:

- `qual = true` — the `USING (true)` policy that passes an "is RLS on?" audit while staying fully readable (`BAAS-02`).
- `'anon' = any(roles)` on non-public data.
- Grants that were never revoked (`BAAS-07`). Supabase, verbatim: "Grants decide whether a role can run an operation on the table at all. Policies decide which rows that operation applies to… Adding policies doesn't take those grants back." New tables in `public` start with every privilege granted to `anon`, `authenticated` and `service_role`, and models essentially never emit `revoke`.

**One conditional that gets missed.** A policy scoped `to authenticated` is only a finding **when combined with open signup** (`AUTHN-07`) — because `authenticated` means "anyone who completed a signup form," not "a customer."

**Check the real condition:**

```sql
-- Is RLS actually on?
select relname, relrowsecurity
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public';

-- Which policies are permissive-to-everyone?
select schemaname, tablename, policyname, roles, cmd, qual, with_check
from pg_policies where schemaname = 'public';

-- Were the default grants ever revoked?
select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public' and grantee in ('anon','authenticated');
```

```bash
# And the open-signup half of the 'to authenticated' conditional:
curl -si -X POST "https://$REF.supabase.co/auth/v1/signup" -H "apikey: $ANON" \
  -H 'Content-Type: application/json' \
  -d '{"email":"probe+audit@example.com","password":"Correct-Horse-9"}' | head -1
```

| Outcome | Report |
|---|---|
| `relrowsecurity = false` on a private table | `BAAS-01` CRITICAL |
| `qual` is `true` on non-public data | `BAAS-02` CRITICAL |
| Grants still present for `anon`/`authenticated` after policies exist | `BAAS-07` HIGH |
| An `access_token` in the signup response **and** policies scoped `to authenticated` on private data | `AUTHN-07` HIGH, chained — say explicitly that anyone can mint an `authenticated` identity |
| `anon`/`authenticated` merely appearing in `pg_policies` | Nothing |

Supabase's Security Advisor lint `0013_rls_disabled_in_public` covers the first row of that table if you prefer a dashboard check.

---

## `NOTVULN-09` — `dangerouslySetInnerHTML` wrapped in a real sanitizer

**What it looks like.** A grep for `dangerouslySetInnerHTML` returning twelve hits and an audit calling all twelve XSS.

**Why it is not a finding.** The API name is alarming by design, and grepping for it produces mostly noise. **A `DOMPurify.sanitize()` with an explicit allowlist is the correct fix, not a residual vulnerability.** Reporting it as a finding tells the user their correct code is broken.

**The control that must exist.** Every `__html` value passes through a real HTML parser-based sanitizer, on **every render path**, with the sanitizer itself current.

**The four things that ARE findings** (`INJECT-01`):

1. **An unsanitized `__html` sink** — trace the value back to a request, a DB row, or model output.
2. **A regex "sanitizer"** — `replace(/<script.*?>/gi,'')` is defeated by `<img src=x onerror=…>`.
3. **`rehype-raw` without `rehype-sanitize` AFTER it in the same array.** `react-markdown` is safe by default — its README says so — and adding `rehype-raw` alone removes that protection, rendering raw HTML from assistant messages as live DOM. Shipped in `@copilotkit/react-ui` 1.55.3 and 1.56.0 (issue #3938, opened 2026-04-15, closed with no maintainer comment and no linked PR — treat as unfixed and pin/patch yourself).
4. **An out-of-date DOMPurify.** Pin **≥ 3.2.7** — CVE-2025-26791 (fixed 3.2.4) and CVE-2025-15599 (`</textarea>` breakout of `SAFE_FOR_XML`, affects 3.1.3–3.2.6 and 2.5.3–2.5.8, fixed 3.2.7; **the 2.x branch was never patched**).

**Check the real condition:**

```bash
# 1+2: find every sink, then read each one — is the value sanitized, and by what?
grep -rn "dangerouslySetInnerHTML\|v-html\|innerHTML\s*=\|srcdoc=" src/ app/ pages/ components/ 2>/dev/null
grep -rnE "replace\(/<[^/]*/(g|gi|ig)" src/ app/ components/ 2>/dev/null   # regex "sanitizers"

# 3: rehype-raw without rehype-sanitize after it
grep -rn "rehype-raw\|rehypeRaw" package.json src/ app/ components/ 2>/dev/null | grep -v rehype-sanitize

# 4: DOMPurify version
npm ls dompurify
```

The correct shape, for copy-paste when (3) fires:

```js
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

<ReactMarkdown rehypePlugins={[rehypeRaw, [rehypeSanitize, defaultSchema]]}>
  {message}
</ReactMarkdown>
```

And when (1) fires:

```js
import DOMPurify from "dompurify";

const clean = DOMPurify.sanitize(userHtml, {
  ALLOWED_TAGS: ["b", "i", "em", "strong", "a", "p", "ul", "ol", "li", "code", "pre", "br"],
  ALLOWED_ATTR: ["href", "title"],
  ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
});
return <div dangerouslySetInnerHTML={{ __html: clean }} />;
```

| Outcome | Report |
|---|---|
| Sink fed unsanitized attacker-influenced data | `INJECT-01` CRITICAL |
| Regex "sanitizer" | `INJECT-01` CRITICAL |
| `rehype-raw` with no `rehype-sanitize` after it | `INJECT-01` CRITICAL — test by making the model echo `<img src=x onerror=alert(1)>` |
| DOMPurify < 3.2.7, or any 2.x | `INJECT-01` HIGH — upgrade; 2.x was never patched |
| `DOMPurify.sanitize()` with an explicit allowlist, current version | Nothing |

**One correction to carry:** React **19+** does block `javascript:` URLs in `href`/`src`, unconditionally — `sanitizeURL.js` rewrites them to a thrown error. The claim that it does not was true for React ≤ 18 and is stale for 19. The sanitizer covers DOM attributes only, so `window.location = userInput`, `router.push()`, and URLs inside `dangerouslySetInnerHTML`/`srcDoc` are still live sinks.

---

## `NOTVULN-10` — `target="_blank"` without `rel="noopener"`

**What it looks like.** Dozens of anchor tags flagged as reverse tabnabbing.

**Why it is not a finding.** MDN and OWASP both confirm modern browsers apply `noopener` implicitly to `<a>`, `<area>` and `<form>`. MDN, verbatim: *"Setting `target="_blank"` on `<a>`, `<area>` and `<form>` elements implicitly provides the same `rel` behavior as setting `rel="noopener"` which does not set `window.opener`."* Reporting anchor-tag boilerplate wastes the reader's attention on a solved problem.

**The genuine residual.** A JavaScript `window.open()` call **still** sets `window.opener` (`CLIENT-05`, deliberately scored LOW). AI writes `window.open` constantly for OAuth popups and "open docs" links, and it composes with the open-redirect step in chain E: `?next=//evil.tld` → OAuth `code` delivered to the attacker → `window.open` popup completes the hop.

**Check the real condition:**

```bash
grep -rn "window\.open(" src/ app/ pages/ components/ 2>/dev/null
```

Fix, when it fires:

```js
const w = window.open(validatedUrl, "_blank", "noopener,noreferrer");
if (w) w.opener = null;
```

| Outcome | Report |
|---|---|
| `window.open()` with a user-influenced URL and no `noopener` | `CLIENT-05` LOW — and raise it if an open redirect exists on the same app |
| `<a target="_blank">` without `rel` | **Nothing.** Do not generate this alarm. |

---

## `NOTVULN-11` — a CORS error in the console

**What it looks like.** `Access to fetch at … has been blocked by CORS policy` in the browser console, reported as a vulnerability.

**Why it is not a finding.** That is the browser doing its job. Neither the error nor a correctly-scoped CORS policy is a vulnerability. And critically: **CORS is not an authorization mechanism.** `curl` ignores it entirely, so "we have CORS configured" answers nothing about who can call your API — do not accept it as a mitigation for anything.

**The control that must exist.** An exact-string allowlist (a `Set`), never regex / `endsWith` / reflection; always send `Vary: Origin`; `credentials:false` for token-authenticated APIs.

**The findings that ARE real** (`INJECT-10`, `CLIENT-02`). `ACAO: *` plus credentials is rejected by browsers, so the "working" fix a model lands on is to **reflect the request Origin back** with `credentials: true`. That is the same-origin policy switched off: any site the victim visits can issue credentialed fetches to your API and read the responses. Subtler variants: `origin: /myapp\.com$/` matches `evilmyapp.com`; `origin.includes("myapp.com")` matches `myapp.com.evil.tld`; a whitelisted `null` is forgeable from a sandboxed `data:` iframe.

**Check the real condition:**

```bash
curl -sI "$APP/api/me" -H 'Origin: https://evil.example' \
  | grep -iE 'access-control-allow-origin|access-control-allow-credentials|vary'
curl -sI "$APP/api/me" -H 'Origin: null' | grep -i access-control-allow-origin
curl -sI "$APP/api/me" -H 'Origin: https://yourapp.example.evil.tld' | grep -i access-control-allow-origin
curl -sI "$APP/api/me" -H 'Origin: https://notyourapp.example' | grep -i access-control-allow-origin
```

| Outcome | Report |
|---|---|
| The attacker origin is **reflected** in `Access-Control-Allow-Origin`, especially with `Access-Control-Allow-Credentials: true` | `INJECT-10` HIGH |
| `null` accepted | `INJECT-10` HIGH |
| Either suffix/prefix bypass echoed back | `CLIENT-02` HIGH |
| A specific origin echoed without `Vary: Origin` | MEDIUM — cache-poisoning risk |
| A CORS error in the console | **Nothing** |

---

## `NOTVULN-12` — `npm audit` output, and low-severity ReDoS advisories

**What it looks like.** `47 vulnerabilities (31 low, 12 moderate, 4 high)` pasted into a report as a headline number. Or the opposite: `found 0 vulnerabilities` presented as an all-clear.

**Why neither is a finding as stated.** Both directions mislead.

- **"0 vulnerabilities" is a false assurance** (`SUPPLY-05`). `npm audit` matches resolved versions against the GitHub Advisory Database. It structurally **cannot** flag a package published minutes ago with a malicious postinstall, a typosquat, or a compromised version inside the pre-disclosure window — it would have missed every incident in the supply-chain lane. Only **GitHub-reviewed** advisories trigger Dependabot alerts at all, and the "Dismiss low impact issues for development-scoped dependencies" preset is **enabled by default for public repositories** while "alerts that are auto-dismissed upon creation do not send notifications." "0 open alerts" is a filtered view.
- **The flood in the other direction is equally misleading**, and it trains users to run `npm audit fix --force`, which is how a working app breaks.

**Keep these specific examples and their real severities — they are the calibration:**

| Advisory | Real severity | What it actually needs |
|---|---|---|
| `brace-expansion` **CVE-2025-5889** | **CVSS 1.3 LOW.** Affects 1.0.0–1.1.11, 2.0.0–2.0.1, 3.0.0, 4.0.0; fixed 1.1.12 / 2.0.2 / 3.0.1 / 4.0.1. | The advisory that most often panics a vibe coder. It is rated "difficult to exploit" for a reason. Fix it, do not lead with it. |
| `ajv` **CVE-2025-69873** | **Disputed — 2.9 LOW from the CNA, 7.5 HIGH from CISA/Red Hat.** Affects `<6.14.0` and `7.0.0`–`8.17.1`, fixed **8.17.2**. | The `pattern` keyword accepts runtime data via a `$data` reference "passed directly to the JavaScript `RegExp()` constructor without validation"; a 31-character payload blocks the CPU for ~44 seconds, doubling per added character. **Only exploitable if you enable `$data`, which is off by default.** Quote both numbers or neither. |
| `path-to-regexp` **CVE-2026-4867** | **CVSS 7.5 HIGH.** All versions **before 0.1.13** — the 0.1.x line is what Express 4 depends on. | **This is the one that actually matters.** Route definitions with three or more parameters in one segment separated by non-period characters (`/:a-:b-:c`) generate a malformed regex; "the backtrack protection added in earlier versions only prevents ambiguity for two parameters." Reachable from any request to a matching route. |

> **Report reachability, not count.** A count is not a finding. "Pre-auth reachable from any request to a matching route" is a finding.

**Check the real condition:**

```bash
# 1. Is the advisory in a production path, or only a build-time dev dependency?
npm ls --omit=dev path-to-regexp
npm ls --omit=dev ajv brace-expansion

# 2. Query OSV directly, so you are not subject to the reviewed/unreviewed split
brew install osv-scanner   # or: go install github.com/google/osv-scanner/v2/cmd/osv-scanner@latest
osv-scanner scan -r .

# 3. Check signatures and provenance, which npm audit does not
npm audit signatures

# 4. Read what Dependabot silently dismissed
gh api "repos/:owner/:repo/dependabot/alerts?state=auto_dismissed"
```

| Outcome | Report |
|---|---|
| A CVE in a **production** dependency, reachable pre-auth (the `path-to-regexp` shape) | HIGH, tier IMMEDIATE. Name the reachable path. |
| A CVE that appears in the **CISA KEV** catalogue | Tier IMMEDIATE regardless of CVSS |
| A LOW ReDoS in a transitive dev dependency | One line at the bottom of the report: "patch on the weekly pass." Never a headline. |
| A disputed score | Quote **both** scorers with their names, or neither |
| `found 0 vulnerabilities` | Not an all-clear. Say what `npm audit` structurally cannot see, and add the behavioural layer. |

---

## `NOTVULN-13` — a public GitHub repo, an exposed project ID, a discoverable subdomain, or a `.well-known/security.txt`

**What it looks like.** "Your source code is public." "Your Supabase project ref is in the bundle." "I found your staging subdomain." "Your security contact is published."

**Why none of these is a vulnerability.** Certificate Transparency makes every hostname public by design — every deployment hostname lands in CT logs within seconds, so subdomain discovery is free and continuous and cannot be prevented. Every route path, table name and backend URL an AI-built app uses is in the bundle by construction; the client-driven architecture requires it. And `security.txt` is **RFC 9116 and is good practice** — publishing one is a positive, not an exposure.

**The controls that must exist / the real findings nearby:**

| Shape | The real finding |
|---|---|
| Public repo | Only a finding if it **ever contained a secret** (`SECRET-05`) — GitHub: "Simply removing the secret from the codebase, pushing a new commit, or deleting and recreating the repository do not prevent the secret from being exploited," and Cross Fork Object Reference means commits survive deletion of the fork *and* the upstream — or if its `.mcp.json` / `.claude/` / `AGENTS.md` agent config is executable (`AGENT-01`). |
| Discoverable subdomain | Only a finding if the preview deployment behind it is **public and pointed at production** (`INFRA-01`), or if the CNAME is **dangling** and claimable (`INFRA-08`). |
| `security.txt` | The common defect is an **expired `Expires` field**. It must be present **exactly once**, and under RFC 9116 is recommended to be less than a year out. |
| Exposed project ID / project ref | Nothing on its own. Go check RLS or Security Rules (`NOTVULN-01`, `NOTVULN-02`). |

**Check the real condition:**

```bash
# Enumerate what is actually reachable, then open every result logged-out
curl -s "https://crt.sh/?q=%25.yourdomain.com&output=json" | jq -r '.[].name_value' | sort -u

# Is the security.txt Expires field still valid, and present exactly once?
curl -s "$APP/.well-known/security.txt" | grep -i '^Expires:'

# Did the repo ever hold a secret?
git log --all --full-history --oneline -- '*.env' '.env.*' '*.pem'
gitleaks git -v --log-opts="--all" .
trufflehog git file://. --results=verified

# Is an agent config in the repo executable?
ls -la .claude/ .cursor/ .mcp.json CLAUDE.md AGENTS.md 2>/dev/null
```

| Outcome | Report |
|---|---|
| A verified live secret in history | `SECRET-05` CRITICAL — rotate at the provider **first**; history rewriting is not remediation |
| A preview URL that loads without an auth wall | `INFRA-01`, tier THIS WEEK (IMMEDIATE if it reaches production data) |
| `Expires` in the past, or listed more than once | LOW, "fix the file you already publish" |
| `.mcp.json` / `.claude/settings.json` hooks committed to a public repo | `AGENT-01` CRITICAL — CVE-2025-59536 ran shell commands from `.claude/settings.json` hooks automatically after the trust dialog |
| A repo being public, a hostname being in CT, a project ref being in the bundle | **Nothing** |

---

## `NOTVULN-14` — things this dossier deliberately does not claim

Honesty about the boundary is part of not crying wolf. A skill that repeats an unsourceable figure gets everything else it says discounted. Reproduce these constraints exactly; do not soften them, and do not fill the gaps from memory.

**Claims that must not be made:**

- **The Lovable March–April 2026 platform BOLA is secondary-sourced only.** No vendor advisory, no CVE, no researcher writeup, and no published endpoint, method or payload were located. The circulating figures — **five API calls; 18,697 student records; 4,538 minors** — are exactly the kind of unsourced precision that discredits a document if one number is wrong. Use it as a **narrative example that platform BOLA happens**; cite **CVE-2025-48757** for the confirmed technical claim.
- **The "1,645 Lovable projects" denominator must never be merged with Escape's separate October 2025 study** of 4,000+ apps out of ~5,600. They are two different scans of two different populations.
- **The EnrichLead shutdown** and its circulating dollar figure are **untraceable**.
- **The `$1,800` `user_metadata` bounty cannot be verified.**
- **The "100+ Firebase projects" attributed to a 2025 Tea follow-up scan was removed outright** — the tool's README makes no such claim.
- **Do not attribute to GitHub the quote "an intentional design decision and is working as expected."** That sentence is secondary-coverage paraphrase. The defensible framing: GitHub documents the Cross Fork Object Reference behaviour as expected and did not treat it as a vulnerability.
- **Do not cite the three 2026 JWT CVE IDs circulating in a DEV.to article** — they could not be corroborated in NVD or GHSA.
- **The `.claude/settings.local.json` npm study figures (428 packages / 33 live credentials) are not citable** — the source 404s and does not appear in the vendor's blog index.
- **There is no Veracode "Spring 2026" edition.** The real publications are the **2025 GenAI Code Security Report** and the **2026** report, whose current cut is the **Summer 2026 dataset**. Any per-CWE or per-language 2026 breakdown beyond Java is **not public** and must not be quoted.

**The prevalence boundary — the single most important line in this file after the governing rule:**

> **No figure in this catalog states how many real deployed vibe-coded apps carry a given bug.** Every prevalence number is from a benchmark, a controlled study, or a named scan of a named population. **"45% of generations" is not "45% of shipped apps," and a skill must not imply otherwise.**

So: when you cite the empirical baseline, cite it as what it is.

| Figure | What it actually measures |
|---|---|
| Veracode: **45% of samples introduce an OWASP Top 10 flaw**; CWE-80 XSS ~14–15% pass rate; CWE-117 log injection ~13%; CWE-89 SQLi ~82%; CWE-327 weak crypto ~86% | 80 tasks, 100+ models. **Generations in a benchmark**, not deployed apps. (2025 per-language, verified: Java 28%, Python 62%, JavaScript 57%, C# 55%. The circulated "Python 38%" is Python's *failure* rate, not its pass rate.) |
| BaxBench: CWE-79 occurrence **0.70 to 1.00** across 11 models | Benchmark scenarios |
| SupaExplorer: **11.04%** of 20,052 indie-launch URLs "exposing Supabase credentials" | A vendor's scan of a named population — **and the vendor's own CEO says finding the anon key there is expected.** Never restate this as "11% of apps are vulnerable." |
| Apiiro: privilege-escalation paths **+322%**, architectural flaws **+153%**, syntax errors **−76%** | Fortune 50 codebases, Dec 2024–Jun 2025 |
| Stanford CCS'23: AI-assisted **36% SQL-injectable vs 7% control** | 47 participants in a controlled study |

**Claims that are REPORTED, not CONFIRMED — carry the marking wherever you use them:**

- `/rest-admin/v1/schema_cache` reachability on Supabase Cloud — two secondary sources, architecturally implausible. Never build a detection step on it alone.
- `verify_jwt=true` being satisfiable by the anon key on legacy-key projects — follows from the key format, not stated outright in the docs. Resolve the caller in code regardless.
- Agent-tooling incident detail: the Replit production-database destruction and the Amazon Q wiper prompt. The CVE, affected version and root cause are confirmed from the AWS bulletin; the "system cleaner" wording, the `/tmp/CLEANER.LOG` path and the ship/pull dates are **press reporting**. Attribute accordingly.
- arXiv:2506.11022 (security degradation across AI iterations) — quotations verified accurate, methodology unrefereed with static-analysis labelling. Suggestive, not settled.
- The slopsquatting 2026 follow-up (arXiv 2605.17062) is **not peer reviewed**; the USENIX Security 2025 base study is.
- The `OPS-10` re-audit triggers are a **proposed control** synthesized from the lane, not a documented finding.

**Four CVEs where the scorers genuinely disagree — cite the scorer alongside the score, or cite neither:** CVE-2025-57822 (NVD 8.2 HIGH vs GitHub 6.5 MEDIUM), CVE-2026-34198 (GHSA 6.8 vs NVD 5.3), CVE-2026-42606 (GitHub 8.1 vs NIST 8.8), CVE-2026-33640 (GitHub CVSS 4.0 = 9.1 vs NIST CVSS 3.1 = 9.8).

**And three corrections that a naive audit gets backwards:**

1. **`auth.uid() = user_id` does not "fail open" on NULL rows.** `null = user_id` evaluates to `NULL`, which denies. The pattern fails **closed** and produces a confusing outage, not a leak. The genuine fail-open in that area is the ungranted-revoke problem (`BAAS-07`).
2. **RLS is enabled by default for Table-Editor-created tables**, not disabled. This makes the AI-specific argument *stronger*, since agents write raw SQL — the unprotected path.
3. **Nx s1ngularity affected versions are a discrete set**, not a contiguous range: nx 20.9.0 / 20.10.0 / 20.11.0 / 20.12.0 / 21.5.0 / 21.6.0 / 21.7.0 / 21.8.0 plus specific `@nx/*` versions. Do not write "20.9.0–21.8.0".

---

## The self-review pass: run this over your OWN draft findings before showing the user anything

Do not show the report until every draft finding has survived all six questions. Delete or downgrade anything that fails.

| # | Question | Fail condition → what to do |
|---|---|---|
| 1 | **Is the key actually secret?** | The string is on the public-by-design allowlist above → delete the finding, and report the missing control instead. Decode any JWT and triage on the **role claim**, never on the fact that a key is visible. |
| 2 | **Have I verified the condition, or assumed it?** | I did not run the check command for this entry → run it now, or mark the finding as unverified in the report itself. "I saw the shape" is not verification. |
| 3 | **Am I reporting a count, or a reachable path?** | The finding is a number (`47 vulnerabilities`, `12 dangerouslySetInnerHTML hits`) → replace it with the one reachable path, or drop it. |
| 4 | **Would a competent engineer roll their eyes at this?** | Anchor-tag `noopener`, a CORS console error, a public repo, a project ref, an anon key → delete. Every eye-roll you cause discounts the finding below it. |
| 5 | **Am I citing something the dossier says not to cite?** | The claim is on the `NOTVULN-14` list → remove it. If it is REPORTED, say REPORTED in the sentence that uses it. If two scorers disagree, name both or neither. |
| 6 | **Is my prevalence claim about generations or about shipped apps?** | I wrote "45% of apps" → rewrite as "45% of generations in Veracode's 80-task benchmark." Never state how many deployed apps carry a bug; nothing in the source supports it. |

**Then apply the ordering rule.** A report that leads with a LOW ReDoS and buries a missing RLS policy has failed even if every line is technically correct. Lead with the chain that terminates in "all customer data" or "money moves," and fix the cheapest, earliest step in it — in the classic recon-to-dump chain that is one `alter table … enable row level security`.

**And when you find nothing, say so plainly.** "The publishable key is in the bundle, which is correct; RLS is on and the grants are revoked; I could not read any table from outside the app" is a real, valuable result. Manufacturing a finding to look useful is the same failure as crying wolf, one step earlier.
