# The 15 killers: what actually takes these apps down

Load this file when you have findings and need to decide what to fix first, when a user asks "how bad is this really", or when you need the evidence behind a recommendation. It is the prioritisation core of the skill: fifteen ranked entries, each with the stable catalog ID the rest of the skill routes on, the one command that proves it, and the minimal fix as working code. The bottom half of the file — the deliberate exclusions and the exploitation chains — is what makes the ranking defensible; do not quote the ranking without reading it.

---

## How this ranking was built (and why it is not CVSS)

Every entry in the master catalog carries a CVSS-style severity for triage compatibility, but the order here is **real-world risk for a vibe-coded app**, defined as:

> **prevalence in AI-generated code × discoverability from outside × blast radius**

That product is why a "medium" like an exposed source map outranks a "high" like ReDoS, and why the ranking is deliberately hostile to CVSS. The threat model is a solo founder with **no SOC, no WAF, and a time-to-detect of "whenever they next look."**

Two rules that govern how you *report* anything from this list:

1. **Flag the missing control, not the visible key.** A `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the bundle is not a finding. Telling someone it is destroys their trust in the whole audit, and the real finding gets ignored. Each entry below states its common false positive next to its true positive.
2. **Prevalence numbers here are from benchmarks, controlled studies, or named scans of named populations.** No number in this file states what fraction of *real deployed* vibe-coded apps carry a given bug. "45% of generations" is not "45% of shipped apps." Do not let it become one.

| # | ID | Name | Reachable pre-auth? |
|---|---|---|---|
| 1 | `BAAS-01` | RLS never enabled on a public table | Yes |
| 2 | `AUTHZ-01` | IDOR / BOLA — id from the request, no ownership check | No (needs an account) |
| 3 | `SECRET-01` | A real secret behind a public env prefix | Yes |
| 4 | `AUTHZ-03` | Middleware- and UI-only route guards | Yes |
| 5 | `INJECT-01` | XSS | Varies |
| 6 | `PAY-01` / `PAY-02` | Unverified webhooks and client-supplied prices | Yes |
| 7 | `SUPPLY-01` | Install-time lifecycle scripts | N/A (your machine) |
| 8 | `BAAS-03` | Firebase rules that are `if true` or `if request.auth != null` | Yes |
| 9 | `AUTHZ-05` | Mass assignment | No (needs an account) |
| 10 | `INFRA-01` | Preview deployments, public, pointed at production | Yes |
| 11 | `AI-01` | The unauthenticated LLM proxy | Yes |
| 12 | `AUTHZ-08` | Service-role code that skips authorization | Varies |
| 13 | `SECRET-04` | Production source maps | Yes |
| 14 | `INJECT-11` | Read-check-write races on credits and coupons | No (needs an account) |
| 15 | `OPS-01` | Nobody can tell whether it already happened | N/A (meta) |

Set these once before running any detection command in this file:

```bash
APP=https://yourapp.example              # the live deployment
STAGING=https://staging.yourapp.example  # for anything that writes or floods
REF=<your-supabase-project-ref>          # from the shipped bundle
ANON=<your-supabase-publishable-key>     # from the shipped bundle — public by design
COOKIE_A='session=<a real session cookie for your own test account>'
```

---

## 1. `BAAS-01` — Row Level Security never enabled on a public table

**Mechanism.** PostgREST exposes every table in the `public` schema at `https://<ref>.supabase.co/rest/v1/<table>` to anyone holding the publishable key — which is in the shipped bundle by design. Row Level Security is the only thing between that URL and the rows. Supabase enables RLS by default **only for tables created in the Table Editor**; their docs say plainly that if you create a table in raw SQL or the SQL editor you must enable RLS yourself. Coding agents emit raw SQL migrations. That is the unprotected path, and the app works perfectly during development because the developer is logged in.

**Why it ranks first.** Prevalence: the default is against you on exactly the path agents use. Discoverability: zero effort — the OpenAPI root at `/rest/v1/` self-documents every table and RPC, and one `curl` returns rows. Blast radius: the whole database, read **and** write, since `POST`/`PATCH`/`DELETE` work through the same interface.

- CVE-2025-48757 — CVSS 9.3 (assigner score), CWE-863. **NVD tags this CVE DISPUTED** — Lovable argues each customer owns their own app's data protection — and NVD issued no independent CVSS. The underlying misconfiguration is independently confirmed in Supabase's own documentation, so cite the docs for the mechanism and the CVE for the record.
- The discoverer's scan found **303 endpoints across 170 projects, 10.3% of 1,645 analysed**. That 1,645 denominator belongs to this scan only and must never be merged with Escape's separate October 2025 study of 4,000+ apps.
- Wiz's Moltbook writeup: **~4.75M records, 1.5M agent API tokens, 35,000+ emails, 4,060 private DMs.** The critical detail for how you report things: the "leaked" key in that incident was a **publishable** key that was supposed to be public. The only defect was missing RLS. The dossier's own fact-checkers caught the research filing this under "leaked secrets" — do not repeat that error.

Sources: `nvd.nist.gov/vuln/detail/CVE-2025-48757` · `supabase.com/docs/guides/database/postgres/row-level-security` · `wiz.io/blog/exposed-moltbook-database-reveals-millions-of-api-keys`

**Fastest detection.** From outside, with the public key from the bundle — this is safe to run against production:

```bash
curl -s "https://$REF.supabase.co/rest/v1/<table>?select=*&limit=1" -H "apikey: $ANON"
```

*True positive:* a JSON row comes back for a table that should be private. That is a full table read, from a browser, by anyone. *False positive:* a table that is genuinely public — published blog posts, a public directory. Confirm intent before reporting.

Repo-side and in-database equivalents:

```bash
grep -rL 'enable row level security' supabase/migrations/*.sql
```

```sql
select c.relname,
       c.relrowsecurity      as rls_enabled,
       c.relforcerowsecurity as rls_forced,
       (select count(*) from pg_policy p where p.polrelid = c.oid) as policies
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by c.relrowsecurity, policies;
```

Supabase's own Security Advisor (Dashboard → Advisors → Security) covers this as lint `0013_rls_disabled_in_public`. It is free and it is the vendor's lint set — run it.

**Minimal correct fix.** Policies **first**, then enable, then revoke, in **one transaction**. Running `alter table … enable row level security` on its own is a default-deny cutover that blanks every page instantly, and the panic repair for that outage is `using (true)` — which passes an "is RLS on?" audit while leaving the table fully readable.

```sql
begin;
create policy "sel own" on public.todos for select to authenticated
  using ((select auth.uid()) = user_id);
create policy "ins own" on public.todos for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy "upd own" on public.todos for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "del own" on public.todos for delete to authenticated
  using ((select auth.uid()) = user_id);
alter table public.todos enable row level security;
alter table public.todos force row level security;
revoke all on public.todos from anon;
commit;
```

Then stop identity being accepted from the request body, and index the policy column:

```sql
alter table public.todos alter column user_id set default auth.uid();
alter table public.todos alter column user_id set not null;
create index if not exists todos_user_id_idx on public.todos (user_id);
```

Two things that will otherwise get your fix reverted. Policies are only half of it — Supabase notes that "adding policies doesn't take those grants back," so audit `information_schema.role_table_grants` for `anon` and `authenticated`. And wrap `auth.uid()` as `(select auth.uid())`: Supabase's own benchmarks show 179ms → 9ms for that one change, a role check going 11,000ms → 7ms, and a join-in-policy 178,000ms → 12ms when moved into a `stable security definer set search_path=''` helper. A timeout gets the security fix rolled back.

---

## 2. `AUTHZ-01` — IDOR / BOLA: the route reads an id and never checks ownership

**Mechanism.** A handler takes an object id out of the URL or body, fetches that object, and returns it. Nothing in the code asks whether the caller owns it. In a Supabase app the model often writes no check at all on the assumption RLS handles it — while RLS was never enabled (`BAAS-01`).

**Why it ranks second.** OWASP A01:2025 reports that **100% of applications in the contributed dataset had some form of broken access control**, across **1,839,701 occurrences**. It sits at #2 rather than #1 only because the attacker needs an account first.

The structural point matters more than the number, and it is the reason "prompt more carefully" does not fix this class: **"show an invoice" is a complete, correct specification of the feature and says nothing about ownership.** There is no defect in the prompt to correct. A very common partial fix checks ownership on read and forgets it on write. And **UUIDs are not authorization** — they stop enumeration, not leakage; one leaked id is full access.

Platform-scale confirmation exists but is **REPORTED, not confirmed**: the Lovable March–April 2026 platform BOLA (authenticated but unscoped `/projects/{id}/*`) has no vendor advisory, no CVE, no researcher writeup and no published endpoint or payload. The circulating figures — five API calls, 18,697 student records, 4,538 minors — are exactly the kind of unsourced precision that discredits a document if one number is wrong. Use it as a narrative example that platform BOLA happens; cite CVE-2025-48757 for anything technical.

Sources: `owasp.org/Top10/2025/A01_2025-Broken_Access_Control/` · `owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/`

**Fastest detection.** Static analysis structurally cannot find this and you should stop expecting it to — CodeQL's JavaScript security pack has directories for 56 CWEs and **no** CWE-285, CWE-863 or CWE-639 directory; its CWE-862 directory contains exactly two files, both about empty passwords in config. Missing authorization is the absence of a check the tool cannot know was required. The intended policy exists only in the founder's head.

So the fastest real detection is the two-account replay, and the fastest *proxy* for it is asking whether any test has ever held a second identity:

```bash
grep -rlniE "user(B|2)|otherUser|secondUser|tenantB|otherOrg" test/ tests/ e2e/ __tests__/ 2>/dev/null \
  || echo "NO DIFFERENTIAL AUTHZ TESTS EXIST — this is the finding"
```

*True positive:* the echo fires. Every generated test is A-reads-A's-own-data, which passes in a completely broken app.

Code-side greps that narrow where to look:

```bash
grep -rnE "findUnique\(\{\s*where:\s*\{\s*id" src/ app/
grep -rnE "\.eq\('id',\s*(params|req|body)" src/
```

**Minimal correct fix.** Put the ownership predicate *inside* the query rather than in an `if` after it, and return **404, not 403** — a 403 confirms the object exists.

```ts
// data/posts.ts
import 'server-only'

export async function deletePost(postId: string) {
  const session = await auth()
  if (!session?.user?.id) throw new Error('Unauthorized')   // require a concrete identity
  const res = await db.post.deleteMany({ where: { id: postId, authorId: session.user.id } })
  if (res.count === 0) throw new NotFound()
}
```

Require a *concrete identity*, never a truthy object. next-auth v5 beta.0–beta.31 returned `{ message: 'There was a problem with the server configuration...' }` from `auth()` on a config error instead of `null`; that object is truthy, so the documented pattern `const isLoggedIn = !!auth` evaluated true for **every** request — CVE-2026-73421, CVSS 9.1, CWE-636 Failing Open, fixed in beta.32. Use `!!session?.user?.id`, and in the data layer catch around `auth()` and **throw**, never fall through to allow.

The strongest version of this fix is to delete the parameter entirely: `GET /api/me/invoices` cannot have an IDOR.

---

## 3. `SECRET-01` — a real secret behind a public env prefix

**Mechanism.** `NEXT_PUBLIC_`, `VITE_`, `REACT_APP_`, `EXPO_PUBLIC_`, `NUXT_PUBLIC_` and `PUBLIC_` are not conventions. They are instructions to string-replace the literal value into shipped JavaScript. Next.js documents that prefixed values are "inlined, at build time, into the js bundle"; Vite says "`VITE_*` variables should not contain sensitive information such as API keys"; Expo says they are "visible in plain-text in your compiled application."

**Why it ranks third.** Prevalence here is driven by a **debugging loop, not ignorance** — this is the part to explain to the builder, because it is the part that predicts recurrence. A client component reads `process.env.OPENAI_API_KEY`. Next.js replaces an unprefixed variable in client code with an **empty string**. The SDK throws a named error. The builder pastes the error into chat. The minimal change that makes the error stop is adding the prefix. It survives review because the diff is a one-word rename.

Two aggravating defaults: LLM App Router code opens with `"use client"`, whose entire import graph becomes browser code; and Vite generators have no server runtime at all, so there is nowhere else for the call to go. The Next.js Vite-migration guide even instructs "Change all environment variables with the VITE_ prefix to NEXT_PUBLIC_" — which faithfully converts `VITE_SUPABASE_SERVICE_ROLE_KEY` into `NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY` and re-inlines a `BYPASSRLS` key, laundered past review by looking idiomatic.

Discoverability is `grep` on a public file. Blast radius with `SUPABASE_SERVICE_ROLE_KEY` is total, because that key carries Postgres `BYPASSRLS`. A January 2026 scan of **20,052 indie-launch URLs found 11.04% exposing Supabase credentials across 2,217 domains, with 2,325 RLS-bypassing "critical" exposures.**

Sources: `nextjs.org/docs/pages/guides/environment-variables` · `vite.dev/guide/env-and-mode.md` · `docs.expo.dev/guides/environment-variables/`

**Fastest detection.** The grep is the first pass; the build output is the ground truth.

```bash
npm run build && grep -rIaoE 'sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{20,}|SG\.[A-Za-z0-9_-]{20,}|re_[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|sb_secret_[A-Za-z0-9_-]{20,}' .next/static dist build
```

Any bundled JWT — decode it and read the role claim rather than guessing from the prefix:

```bash
echo '<the-eyJ...-token>' | cut -d. -f2 | base64 -d 2>/dev/null | jq .role
```

*True positive:* `"role":"service_role"`, or any `sb_secret_` / `sk_live_` / `AKIA` string. **False positive — the allowlist of keys that are supposed to be public** (`NOTVULN-01` through `NOTVULN-04`): Supabase publishable/anon (`"role":"anon"`), Firebase web config (`apiKey`, `authDomain`, `projectId`, `storageBucket`), Stripe and Clerk `pk_`, PostHog `phc_`, Mapbox `pk.`, Google Maps `AIza`, Sentry public DSN. Supabase's CEO said it directly in the Hacker News thread about the 11.04% scan: "Finding a Supabase project URL and anon key in client code is expected, as both are designed to be public." **Do not rotate a publishable key as remediation.** The finding behind a visible public key is always the missing control — RLS, Security Rules plus App Check, referrer restrictions, domain allowlists.

One genuine trap in the same shape: a **Gemini Developer API key has the identical `AIza` prefix** as a Firebase web key, and Firebase's docs say it "should never be included in your code or configuration files." Check what the key is *restricted to* in Google Cloud, not the prefix.

One case no prefix grep catches at all: Vite's `define: { 'process.env.API_KEY': JSON.stringify(...) }` inlines with no naming signal whatsoever. Grep for `define:` in `vite.config.*` separately.

**Minimal correct fix.** Move the call server-side. A bare proxy is a different bug with the same invoice, so the route that replaces the client call must also enforce auth and a cap — this is the same fix as `AI-01` below.

```ts
// app/api/complete/route.ts
import 'server-only'
import { auth } from '@/auth'
import OpenAI from 'openai'

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY })  // NO public prefix

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return new Response('Unauthorized', { status: 401 })

  const body = await req.json()
  const prompt = typeof body?.prompt === 'string' ? body.prompt : ''
  if (prompt.length > 8000) return new Response('Payload too large', { status: 413 })

  const res = await client.chat.completions.create({
    model: 'gpt-4o-mini',                       // server-chosen, never from the client
    max_completion_tokens: 512,                 // hard cap
    messages: [{ role: 'user', content: prompt }],
  })
  return Response.json({ text: res.choices[0]?.message?.content ?? '' })
}
```

Then rotate the key at the provider — the old value is in every build artifact, every CDN cache and every fork.

**The rule worth giving the builder verbatim:** *if renaming an environment variable made the error go away, you may have just published it.*

---

## 4. `AUTHZ-03` — middleware- and UI-only route guards

**Mechanism.** Hiding a button does nothing to the API — route names are string literals in the shipped JS bundle. Middleware is one step better and still not a boundary: it is a perimeter, and requests route around perimeters.

**Why it ranks fourth.** Two CVSS 9.1 CVEs exist essentially to prove the point:

- **CVE-2025-29927** (Next.js). Sending `x-middleware-subrequest: middleware` — repeated five times for 13.2+ — makes Next.js skip middleware entirely. Affected: `>=12.0.0 <12.3.5`, `>=13.0.0 <13.5.9`, `>=14.0.0 <14.2.25`, `>=15.0.0 <15.2.3`.
- **CVE-2026-41248** (Clerk). `createRouteMatcher` bypassable by crafted requests. Fixed in `@clerk/nextjs` 5.7.6 / 6.39.2 / 7.2.1. This is the more instructive case, because **the bypassed pattern was the documented, correct-looking one.** The builder did nothing wrong and was still open.

The deeper point survives both patches: **middleware cannot see the object being requested.** It can gate `/api/orders/:id` and can never check whether this user owns that order. Any authorization scheme that works by classifying a URL will eventually be bypassed by a URL you classified wrong. OWASP A01:2025 scenario #3 is literally `curl` past a front-end control.

Sources: `github.com/vercel/next.js/security/advisories/GHSA-f82v-jwr5-mffw` · `github.com/clerk/javascript/security/advisories/GHSA-vqx2-fgx2-5wq9`

**Fastest detection.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'x-middleware-subrequest: middleware' "$APP/api/admin/users"
```

*True positive:* anything other than a 401/403 — you reached an admin route with a header. This probe is logged on the target, so run it against your own app knowingly.

The structural version of the check, which matters even on a fully patched Next.js:

```bash
for f in $(find app -name route.ts); do grep -qE 'auth\(|getUser\(|getSession\(' "$f" || echo "NO AUTH CALL: $f"; done
```

On Next.js 16+ the middleware file is `proxy.ts` — grep for both names.

**Minimal correct fix.** Upgrade, strip the header at the proxy, and re-check identity **inside** every handler, action and server component. Treat middleware as UX.

```ts
// middleware.ts — UX only. Never the authorization boundary.
import { NextResponse, type NextRequest } from 'next/server'

export function middleware(req: NextRequest) {
  // defence in depth: refuse the bypass header outright
  if (req.headers.has('x-middleware-subrequest')) {
    return new NextResponse('Bad Request', { status: 400 })
  }
  return NextResponse.next()
}
export const config = { matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'] }
```

```ts
// app/api/orders/[id]/route.ts — the actual boundary
import 'server-only'
import { auth } from '@/auth'
import { db } from '@/db'

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await auth()
  if (!session?.user?.id) return new Response('Unauthorized', { status: 401 })

  const { id } = await params
  const order = await db.order.findFirst({ where: { id, userId: session.user.id } })
  if (!order) return new Response('Not Found', { status: 404 })   // 404, not 403

  return Response.json(order)
}
```

---

## 5. `INJECT-01` — XSS

**Mechanism.** User-controlled text reaches the DOM as markup rather than as text. React, Vue and Svelte auto-escape, so the model is safe right up until the prompt says "render the rich text" — at which point it reaches for `dangerouslySetInnerHTML` / `v-html`, or writes a regex "sanitizer" like `replace(/<script.*?>/gi, '')`, which is defeated by `<img src=x onerror=…>`.

**Why it ranks fifth.** It is empirically the **#1 vulnerability class LLMs generate**.

- **Veracode:** a **13.53% security pass rate for CWE-80 in 2025** (the two-decimal figure is from the gated full report; the public 2025 blog states models "failed to defend against it in 86% of relevant code samples," ≈14% pass — consistent), measured across **100+ models on 80 tasks**. Compare **80.44% for SQL injection** and **85.61% for weak crypto** in the same 2025 dataset. **The models have not learned output encoding; they have largely learned parameterized queries.** No 2026 per-CWE figures are public — do not quote one, and note there is **no Veracode "Spring 2026" edition**; the real publications are the 2025 report and the 2026 report's Summer 2026 dataset.
- **BaxBench:** CWE-79 occurrence of **0.70** (Claude 3.5 Sonnet) to **1.00** (Qwen2.5 7B) of functionally-correct backends per model — 0.96 for o1, 0.84 for GPT-4o, and 0.99 on the ProfileCollection scenario.

Both papers give the same cause, and it is the same shape as `AUTHZ-01`'s: **escaping requires knowing which variable is attacker-controlled, a whole-program dataflow fact invisible in a single-function prompt.** In this ecosystem it also has an AI-native delivery path — `AI-04`, markdown rendering of model output — so the attacker does not need a form field.

Sources: `veracode.com/blog/genai-code-security-report/` · `veracode.com/blog/2026-genai-code-security-report/` · `arxiv.org/pdf/2502.11844`

**Fastest detection.**

```bash
grep -rn "dangerouslySetInnerHTML\|v-html\|innerHTML *=\|document.write\|srcdoc=" src/ app/ --include=*.ts --include=*.tsx --include=*.vue
grep -rn "rehype-raw" package.json src/ app/
```

*True positive:* an `__html` sink with no `DOMPurify.sanitize`, a regex "sanitizer," `rehype-raw` without `rehypeSanitize` **after** it in the same plugin array, or an out-of-date DOMPurify. *False positive:* `dangerouslySetInnerHTML` on a build-time constant, and `DOMPurify.sanitize()` with an explicit allowlist — that is the correct fix, not a residual vulnerability (`NOTVULN-09`). Grepping this pattern produces mostly noise; trace every value back to a request, a DB row, or model output before you report it.

The AI-chat-specific instance worth checking by hand: AnythingLLM GHSA-rrmw-2j6x-4mf2 / CVE-2026-32626 (CVSS 9.6, ≤1.11.1, fixed 1.11.2), where the **streaming** render path used `dangerouslySetInnerHTML` with no DOMPurify while the historical-message path did sanitize. Sanitize on **every** render path.

**Minimal correct fix.**

```ts
// lib/sanitize.ts
import DOMPurify from 'isomorphic-dompurify'   // pin >= 3.2.7

export function sanitizeRichText(dirty: string): string {
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'ul', 'ol', 'li', 'a', 'code', 'pre', 'blockquote'],
    ALLOWED_ATTR: ['href', 'title'],
    ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
  })
}
```

```tsx
<div dangerouslySetInnerHTML={{ __html: sanitizeRichText(post.body) }} />
```

For markdown rendering of model output, the fix is to drop `rehype-raw`, or to place `rehypeSanitize` after it:

```ts
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
// order matters: raw first, sanitize second
const plugins = [rehypeRaw, [rehypeSanitize, defaultSchema]]
```

**Pin DOMPurify ≥ 3.2.7.** CVE-2025-26791 was fixed in 3.2.4; CVE-2025-15599 (a `</textarea>` breakout of `SAFE_FOR_XML`) affects 3.1.3–3.2.6 and 2.5.3–2.5.8 and was fixed in 3.2.7 — **the 2.x branch was never patched.**

One correction to carry, because stale advice is common here: **React 19+ *does* neutralize `javascript:` URLs in `href` and `src`, unconditionally** — `sanitizeURL.js` rewrites them to a thrown error. React ≤18 only warned. That sanitizer covers DOM attributes only, so `window.location = userInput`, `router.push()`, and URLs inside `dangerouslySetInnerHTML` / `srcDoc` are still live sinks.

---

## 6. `PAY-01` / `PAY-02` — unverified webhooks and client-supplied prices

**Mechanism.** `PAY-01`: an attacker POSTs hand-written JSON (`type=checkout.session.completed`, a `metadata.userId`, a `plan`) to a guessable `/api/webhooks/stripe` and gets a free upgrade or unlimited credits. Stripe states the risk verbatim: without signature verification "an attacker could send fake webhook events… to trigger actions like fulfilling orders, granting account access."

`PAY-02` is how `PAY-01` gets *created*. `express.json()` mounted before the webhook route, the Next.js Pages `bodyParser`, or a `JSON.stringify(req.body)` mutates the bytes, so the HMAC over `{ts}.{rawBody}` never matches. Legitimate events start failing. The usual "fix" is deleting verification.

The sibling defect is passing a client-supplied `amount` / `priceId` / `credits` into checkout creation, after which Stripe emits a **genuine, correctly signed** event for the tampered value — signature verification cannot save you from it.

**Why it ranks sixth.** This is the point where a vulnerability becomes revenue. Both defects are one `curl` away and both are silent — there is no error, no alert, and the app keeps working.

Sources: `docs.stripe.com/webhooks` · `docs.stripe.com/webhooks/signature`

**Fastest detection.** Forge an event against your own endpoint:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$APP/api/webhooks/stripe" \
  -H 'Content-Type: application/json' \
  -d '{"type":"checkout.session.completed","data":{"object":{"metadata":{"userId":"me","plan":"pro"}}}}'
```

*True positive:* anything other than `400`, or any entitlement change in the database. Then the two greps:

```bash
grep -rn 'constructEvent' app/ api/ || echo "FAIL: no signature verification"
grep -rn 'price_data' app/ api/ && echo "FAIL: server accepting client-priced line items"
```

**Minimal correct fix.** Read the **raw** body, verify, then write — with idempotency in the same transaction as the grant.

```ts
// app/api/webhooks/stripe/route.ts
import 'server-only'
import Stripe from 'stripe'
import { db } from '@/db'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!)

export async function POST(req: Request) {
  const raw = await req.text()                                  // raw bytes, never req.json()
  const sig = req.headers.get('stripe-signature')!
  let event: Stripe.Event
  try {
    event = stripe.webhooks.constructEvent(raw, sig, process.env.STRIPE_WEBHOOK_SECRET!)
  } catch {
    return new Response('Invalid signature', { status: 400 })   // do NOT delete this branch
  }

  await db.$transaction(async (tx) => {
    try {
      await tx.processedWebhookEvent.create({ data: { id: event.id } })  // PK = event.id
    } catch {
      return                                                    // unique violation = already handled
    }
    if (event.type === 'checkout.session.completed') {
      const s = event.data.object as Stripe.Checkout.Session
      await grantEntitlement(tx, s)                             // resolves plan from price.id
    }
  })

  return new Response(null, { status: 200 })
}
```

In Express, mount the raw parser on the webhook route **before** `app.use(express.json())`:

```js
app.post('/api/webhooks/stripe', express.raw({ type: 'application/json' }), stripeHandler)
app.use(express.json())
```

For the price half, the client sends only an **opaque plan key** and the server resolves it against a hardcoded catalog:

```ts
const PLANS = { pro_monthly: 'price_1AbCdEfGh', pro_yearly: 'price_1XyZwVuT' } as const

const price = PLANS[body.plan as keyof typeof PLANS]
if (!price) return new Response('Unknown plan', { status: 400 })
await stripe.checkout.sessions.create({ mode: 'subscription', line_items: [{ price, quantity: 1 }] })
// never price_data; treat metadata as a key into the catalog, never as a value
```

Keep Stripe's default 5-minute tolerance — their docs say "Don't use a tolerance value of 0." During a secret roll Stripe sends **one `v1` signature per active secret for up to 24 hours** (plus a decoy `v0` on test events), so if you ever verify manually, iterate every `v1=` value with a constant-time compare. **Never** grant entitlement on the success redirect: Stripe is explicit that "you can't rely on triggering fulfillment only from your checkout landing page." The success page may call the *same* idempotent `fulfillCheckout(sessionId)` the webhook calls — retrieving the session server-side and checking `payment_status !== 'unpaid'` — but the webhook stays mandatory.

The same raw-body rule applies to Paddle (`ts:rawBody`), RevenueCat (`timestamp.rawBody`) and Standard Webhooks.

---

## 7. `SUPPLY-01` — install-time lifecycle scripts

**Mechanism.** `preinstall`, `install`, `postinstall` and `prepare` run arbitrary shell **as your user** for every package in the transitive tree, during `npm install`. You do not have to import the package, call it, or know it exists.

**Why it ranks seventh.** This is the mechanism behind essentially every 2025–26 registry incident:

- **Shai-Hulud.** Wave 1 (Sept 2025) used `postinstall` across 500+ packages. Wave 2 (24 Nov 2025) moved to **`preinstall`** across **796 packages, 1,092 versions, ~20M weekly downloads** — Datadog notes the move "dramatically widened the area of impact."
- **chalk / debug.** 18 packages with **>2B combined weekly downloads**, live for about **2.5 hours**.
- **Nx s1ngularity.** The malicious `postinstall` weaponized the victim's own AI CLIs — `claude --dangerously-skip-permissions -p`, `gemini --yolo -p`, `q chat --trust-all-tools --no-interactive` — to enumerate secrets. Affected versions are a **discrete set, not a range**: nx 20.9.0 / 20.10.0 / 20.11.0 / 20.12.0 / 21.5.0 / 21.6.0 / 21.7.0 / 21.8.0 plus specific `@nx/*` versions.

A vibe coder has no idea `chalk` is in their tree. And `npm audit` **structurally cannot** flag a package published minutes ago — it is a CVE lookup against the GitHub Advisory Database, so the entire pre-disclosure window is invisible to it. Microsoft's own first-line mitigation for the May 2026 typosquat campaign is literally `npm install --ignore-scripts`.

Sources: `pnpm.io/supply-chain-security` · `microsoft.com/en-us/security/blog/2026/05/28/typosquatted-npm-packages-used-steal-cloud-ci-cd-secrets/`

**Fastest detection.**

```bash
npm query ':attr(scripts, [postinstall])'
```

That is your real install-script surface. Then confirm the guard is actually on:

```bash
grep -h 'ignore-scripts' .npmrc ~/.npmrc 2>/dev/null || echo "FAIL: install scripts run unrestricted"
```

*False positive to expect:* `npm audit` reporting "0 vulnerabilities" is not evidence of anything here (`NOTVULN-12`), and neither is a clean Dependabot page — the "Dismiss low impact issues for development-scoped dependencies" preset is enabled by default on public repos and auto-dismissed alerts send no notification, while devDependencies run with full filesystem, network and secret access on your CI runner. Read the silenced ones with `gh api /repos/:owner/:repo/dependabot/alerts?state=auto_dismissed --paginate | jq length`.

**Minimal correct fix.** Turn scripts off globally and re-enable them per package, then add a cooldown — the cooldown is the control that actually works, because a 24-hour delay would have blocked both Shai-Hulud waves and all 18 chalk/debug versions.

```ini
# .npmrc — commit this, and put the same lines in ~/.npmrc
ignore-scripts=true
save-exact=true
```

```bash
npm ci --ignore-scripts
npm rebuild sharp better-sqlite3     # only the packages that genuinely need a native build
```

```yaml
# .github/dependabot.yml
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

On pnpm v11: `minimumReleaseAge` (defaults to 1440 minutes), `minimumReleaseAgeStrict`, `trustPolicy: no-downgrade`, plus `allowBuilds: []` and `blockExoticSubdeps: true`.

**Prepare the cooldown escape hatch before you need it.** npm's docs: when `npm audit fix` is blocked by the release-age filter, "npm retains the vulnerable version and exits with a non-zero code." Under a real 9.8 RCE the panic response is deleting the cooldown permanently. Instead exclude by **exact version**, never by package name — `minimumReleaseAgeExclude: ['next@16.2.11']`, or a one-off `npm install pkg@ver --min-release-age=0 --ignore-scripts`.

And verify any AI-suggested package before installing it. USENIX Security 2025 measured **19.7% of LLM-recommended packages as non-existent**, 205,000+ unique hallucinated names, with 43% recurring across all 10 reruns — that persistence is what makes pre-registration economical for an attacker.

```bash
npm view <name> time.created repository maintainers   # 404 = hallucination; <90 days + "popular" = stop
```

---

## 8. `BAAS-03` — Firebase rules that are `if true` or `if request.auth != null`

**Mechanism.** Two rules, one class. `allow read, write: if true` is test mode never tightened; Firebase's own docs say "NEVER use this ruleset in production; it allows anyone to overwrite your entire database." The subtler rule is worse because it survives review: `if request.auth != null` checks that **someone** is logged in. With open or anonymous signup, the attacker mints that identity themselves in one API call.

**Why it ranks eighth.** `if request.auth != null` is the canonical "secure-looking" answer to "require login," and — critically — it is the **shipped default ruleset for Cloud Storage** over `{allPaths=**}`. So teams harden `firestore.rules`, never open `storage.rules`, and leave every uploaded ID document world-writable.

Outcome: **the Tea breach** — ~**72,000 images** including ~**13,000 ID-verification selfies and driver's licences**, downloaded with a script posted to 4chan, followed by a second datastore holding ~1.1M private messages.

Two properties make hand-inspection unreliable and actively push developers toward the over-broad rule: **rules are not filters** (an over-broad query fails entirely rather than returning the allowed subset, so the developer loosens the rule until the app works), and `get()`/`exists()` calls inside rules are quota-limited to **10 for single-document and query requests, 20 for multi-document reads, transactions and batched writes** — so ownership walks get deleted under load. Rules also **do not cascade to subcollections**: a rule on `/users/{uid}` does not cover `/users/{uid}/messages`.

Sources: `firebase.google.com/docs/rules/insecure-rules` · `404media.co` (Tea breach)

**Fastest detection.** Unauthenticated, from outside:

```bash
curl -s -o /dev/null -w '%{http_code}\n' "https://<project>-default-rtdb.firebaseio.com/.json"
curl -s "https://firestore.googleapis.com/v1/projects/<project>/databases/(default)/documents/users" | head
curl -s "https://firebasestorage.googleapis.com/v0/b/<project>.appspot.com/o" | head
```

*True positive:* 200 with data on an unauthenticated request. *False positive to refuse:* the Firebase web config being in the bundle is not the finding (`NOTVULN-02`) — your project ID is in every shipped bundle and APK, so discovery is free and continuous. **Enumeration returning nothing is the goal, not enumeration being impossible.**

Repo-side:

```bash
grep -rniE "allow (read|write|read, write).*: *if *true|if *request\.auth *!= *null" firestore.rules storage.rules
```

**Minimal correct fix.** Own the resource, exclude anonymous identities, and write a separate `create` rule against `request.resource` (the incoming document) rather than `resource` (the existing one, which does not exist yet on create):

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /notes/{noteId} {
      allow read, update, delete: if request.auth != null
        && request.auth.token.firebase.sign_in_provider != 'anonymous'
        && request.auth.uid == resource.data.owner_uid;
      allow create: if request.auth != null
        && request.auth.token.firebase.sign_in_provider != 'anonymous'
        && request.auth.uid == request.resource.data.owner_uid;
    }
    // rules do not cascade — subcollections need their own match block
    match /notes/{noteId}/comments/{commentId} {
      allow read, write: if request.auth != null
        && request.auth.uid == request.resource.data.author_uid;
    }
  }
}
```

Storage — the default that nobody edits:

```javascript
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /uploads/{uid}/{fileName} {
      allow read, write: if request.auth != null && request.auth.uid == uid
        && request.resource.size < 5 * 1024 * 1024
        && request.resource.contentType.matches('image/(jpeg|png|webp)');
    }
  }
}
```

Two deploy semantics to know before you push: CLI-deployed "rules defined in your project directory overwrite any existing rules in the Firebase console," and "Firebase Security Rules releases take a period of several minutes to fully propagate," with no staged rollout. Verify with the emulator and `@firebase/rules-unit-testing` — the count of `assertFails` cases must exceed the count of `assertSucceeds` cases, or the suite is only testing the happy path.

---

## 9. `AUTHZ-05` — mass assignment

**Mechanism.** `{ ...req.body }` goes into a database write. The attacker takes a working `PATCH` out of the network tab, adds `"role":"admin"`, `"credits":999999` or `"is_pro":true`, and resends it. OWASP API3:2023 documents live payloads.

**Why it ranks ninth.** `{...body}` is the shortest generic update endpoint and it keeps working when you add a field, which makes it *feel* better engineered. Generated Zod schemas are frequently `.passthrough()`, or used for types only and never actually parsed at runtime.

The BaaS version is sharper and much less known, and it is the highest-yield attack on vibe-coded Supabase apps: **RLS scopes rows, not columns, and a policy cannot compare OLD to NEW.** `WITH CHECK` sees only the new row; there is no `OLD` in a policy. So a *correct*, owner-scoped UPDATE policy still permits `PATCH {"role":"admin"}` against your own row, and no amount of policy authoring expresses column immutability. Column-level `REVOKE` / `GRANT` is the only expressible control in that direction, and no AI writes it.

Source: `owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/`

**Fastest detection.**

```bash
grep -rnE "\.\.\.(req\.body|body|payload)" src/ app/
```

Then the two follow-ups that tell you whether it matters:

```bash
grep -rn "passthrough()" . --exclude-dir=node_modules
grep -rnE "\b(is_admin|role|credits|plan|approved|tenant_id)\b" prisma/schema.prisma
```

*True positive:* a spread into a write **and** a privileged column on the same table. *False positive:* a spread on a table with no privileged columns at all — real, but low value; report it under the fix for the privileged table rather than as its own finding.

**Minimal correct fix — server framework.** `.strict()` rejects unknown keys rather than silently stripping them, so you find out instead of guessing. Identity always comes from the session, never the body.

```ts
import { z } from 'zod'

const UpdateProfile = z.object({
  displayName: z.string().min(1).max(80),
  bio: z.string().max(500).optional(),
}).strict()                                     // unknown keys are an error, not a strip

export async function PATCH(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return new Response('Unauthorized', { status: 401 })

  const parsed = UpdateProfile.safeParse(await req.json())
  if (!parsed.success) return new Response('Bad Request', { status: 400 })

  const updated = await db.profile.update({
    where: { userId: session.user.id },         // id from the session, never the body
    data: { displayName: parsed.data.displayName, bio: parsed.data.bio },  // explicit fields
    select: { id: true, displayName: true, bio: true },                    // explicit response
  })
  return Response.json(updated)
}
```

**Minimal correct fix — Supabase, where RLS cannot express it.** PostgREST honours column grants, so revoke table-wide and grant back only the columns a user may write:

```sql
revoke update on public.profiles from anon, authenticated;
grant update (display_name, bio) on public.profiles to authenticated;
```

Belt and braces, for the case where a grant gets restored by a later migration:

```sql
create or replace function public.freeze_privileged_columns()
returns trigger language plpgsql as $$
begin
  new.role    := old.role;
  new.credits := old.credits;
  new.is_pro  := old.is_pro;
  return new;
end $$;

create trigger profiles_freeze_privileged
  before update on public.profiles
  for each row execute function public.freeze_privileged_columns();
```

Best of all, move the privileged attributes out of the user's own row entirely: an `entitlements` table with a SELECT-only owner policy and `revoke insert, update, delete on public.entitlements from anon, authenticated`, written only by the service-role webhook. The Firestore equivalent of the trigger is `request.resource.data.role == resource.data.role`.

---

## 10. `INFRA-01` — preview deployments, public by default, pointed at production data

**Mechanism.** No exploit required. Vercel's own knowledge base states that `X-Robots-Tag: noindex` "only asks search engines not to index… Anyone with the URL can still open it." Branch aliases are deterministic, and every deployment hostname lands in **Certificate Transparency logs** within seconds, so previews are enumerable from `crt.sh` by anyone. When `DATABASE_URL` is scoped to all environments, a half-finished public preview — no auth wired up yet, debug routes still present — talks to the production database.

**Why it ranks tenth.** It requires zero skill, it is the default, and it defeats every control you added to the production build because the attacker is not using the production build. Red Access scanned ~**380,000 vibe-coded assets** across Lovable, Replit, Base44 and Netlify and found ~**5,000 leaking sensitive data** — medical records, a bank's internal financials, customer-support chat logs.

Source: `vercel.com/docs/deployment-protection`

**Fastest detection.**

```bash
curl -s "https://crt.sh/?q=%25.yourdomain.com&output=json" | jq -r '.[].name_value | split("\n")[]' | sed 's/^\*\.//' | sort -u
```

Open every result in a logged-out private window. *True positive:* a preview loads without an auth wall, or `vercel env pull` shows `DATABASE_URL` present in the preview environment.

**Minimal correct fix.** Scope every secret to Production only, give Preview a throwaway database, and turn on the platform's own access wall.

```bash
# remove the production DB from preview and development, then re-add it production-only
vercel env rm DATABASE_URL preview
vercel env rm DATABASE_URL development
vercel env add DATABASE_URL production

# a disposable database for previews
vercel env add DATABASE_URL preview      # paste the throwaway connection string

# confirm what each environment actually resolves to
vercel env pull .env.preview --environment=preview && grep DATABASE_URL .env.preview
```

Then enable **Vercel Authentication with Standard Protection** (Project → Settings → Deployment Protection), which is available on all plans, or the equivalent Cloudflare Pages access policy. On Vercel also mark production secrets **Sensitive** — non-readable after creation, and values of 32 characters or more are redacted in build logs.

**Never** set `"public": true` in `vercel.json` or run `vercel deploy --public`. That serves `/_src` (source code and build output) and `/_logs` (build logs) to anyone, and toggling protection back on does **not** retroactively protect deployments that already exist — Vercel's documented remedy is that "the only option is to delete these deployments."

---

## 11. `AI-01` — the unauthenticated LLM proxy

**Mechanism.** The default AI-SDK scaffold ships `POST /api/chat` with no auth, no size cap and no quota. An attacker opens DevTools, right-clicks the request, "Copy as cURL," and now owns your provider budget.

**Why it ranks eleventh — and this high.** It is **default-generated**, so prevalence tracks scaffold usage rather than developer error; it is trivially found; and it converts directly into money leaving your account **while the app keeps working perfectly**, so nothing alerts you. Sysdig documented the resale economy this feeds — the OAI-Reverse-Proxy market — at up to ~**$46k/day of victim spend**. Publicly reachable unauthenticated compute does get found: in the July 2026 Hugging Face incident an escaped agent used a Modal Labs customer's public endpoint for code execution.

Source: `sysdig.com/blog/llmjacking-stolen-cloud-credentials-used-in-new-ai-attack`

**Fastest detection.** An incognito, cookie-less `curl` against your own endpoint (this one is logged on your side):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$APP/api/chat" \
  -H 'content-type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}'
```

*True positive:* a 200.

**Minimal correct fix.** Session check as line one, a size cap, a server-chosen model, a hard output cap, an abort timeout, and a per-user budget that lives in the database rather than in memory.

```ts
// app/api/chat/route.ts
import 'server-only'
import { auth } from '@/auth'
import { streamText } from 'ai'
import { openai } from '@ai-sdk/openai'
import { sql } from '@/db'

const MODEL = 'gpt-4o-mini'          // server-chosen, never from the request
const DAILY_TOKEN_BUDGET = 50_000

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return new Response('Unauthorized', { status: 401 })

  const { messages } = await req.json()
  const chars = JSON.stringify(messages ?? []).length
  if (chars > 8000) return new Response('Payload too large', { status: 413 })

  // atomic budget decrement — see INJECT-11; zero rows means over budget
  const rows = await sql`
    update ai_budgets set spent = spent + 1000
    where user_id = ${session.user.id} and day = current_date
      and spent + 1000 <= ${DAILY_TOKEN_BUDGET}
    returning spent`
  if (rows.length === 0) return new Response('Quota exceeded', { status: 429 })

  const result = streamText({
    model: openai(MODEL),
    messages,
    maxOutputTokens: 1024,                       // hard cap
    abortSignal: AbortSignal.timeout(30_000),    // no unbounded generation
  })
  return result.toTextStreamResponse()
}
```

Set a provider-side spend cap as well, and check that it has an **automatic action** attached, not just an alert. Vercel's docs are explicit: "Setting a spend amount does not automatically stop usage. If you want to pause all your projects at a certain amount, you must enable the option" — and Vercel "checks your metered resource usage… every few minutes," with pausing that "is not instantaneous." Supabase's Spend Cap is Pro-only and **does not cover** Compute, Branching Compute, Read Replica Compute, Custom Domain, provisioned Disk IOPS/Throughput, IPv4, Log Drain Hours/Events, MFA Phone or PITR. On AWS, use a Budget **action** (an IAM policy or SCP deny), not a notification. An unset spend cap is a security defect: OWASP API4:2023 lists "third-party service providers' spending limit" alongside execution timeouts and max upload size.

Never `eval` model output. If the model drives a database query, enforce it in the database with a dedicated `ai_reader` role — SELECT-only, `default_transaction_read_only`, a `statement_timeout`.

---

## 12. `AUTHZ-08` — service-role code that skips authorization

**Mechanism.** Two bugs get conflated everywhere and are worth separating. **Exposure** — the service-role key ending up in the bundle — is `SECRET-01` / `BAAS-04`. **Misuse** is subtler and far more common: correctly server-side service-role code *silently disables RLS for that route*. Every authorization decision RLS used to make must now be re-implemented by hand, in that handler, by the developer.

**Why it ranks twelfth.** Because of *why* the model reaches for it. A query returns nothing, the model swaps the user client for the admin client, the error goes away, and the security model is off. One line, no error, no warning. It is precisely the shape of change that an agent makes to unblock itself.

The consequence for testing is the part people miss: **a fully green pgTAP / RLS suite proves the database refuses the query and says nothing about the one route handler an agent switched to the service key.** Your database tests and your application are no longer testing the same path.

Source: `supabase.com/docs/guides/database/postgres/row-level-security`

**Fastest detection.**

```bash
grep -rn "SERVICE_ROLE\|service_role" src/ app/ supabase/functions/
```

Read **every** hit and ask one question: was the ownership filter re-added by hand in this query? *True positive:* a service-role client running a query with no `.eq('owner_id', …)` / `where: { userId }`. *False positive:* a service-role call that genuinely has no per-user scope — a Stripe webhook writing entitlements, a cron job. Those are correct uses; say so rather than flagging them, or the builder will start ignoring the check.

The finding cannot be settled from the database side. Confirm it with an HTTP-level cross-tenant test per route, using two real accounts.

**Minimal correct fix.** Fence the admin client so it can never be imported into browser code, then re-add the predicate by hand on every query that used to rely on RLS.

```ts
// lib/supabase-admin.ts
import 'server-only'                       // build fails if this is imported client-side
import { createClient } from '@supabase/supabase-js'

export const admin = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,   // NO public prefix, ever
  { auth: { persistSession: false, autoRefreshToken: false } },
)
```

```ts
// app/api/reports/route.ts
import { admin } from '@/lib/supabase-admin'
import { createServerClient } from '@/lib/supabase-server'

export async function GET() {
  // authenticate with the USER client — getUser() verifies with the auth server
  const supabase = await createServerClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return new Response('Unauthorized', { status: 401 })

  // RLS is OFF on this client. The predicate is now your job.
  const { data, error } = await admin
    .from('reports')
    .select('id, title, created_at')        // minimal DTO, never select('*')
    .eq('owner_id', user.id)                // <- the line that RLS used to be
  if (error) return new Response('Error', { status: 500 })

  return Response.json(data)
}
```

Never trust `supabase.auth.getSession()` in server code — Supabase's own wording: "Never trust supabase.auth.getSession() inside server code… The server gets the user session from the cookies, which can be spoofed by anyone." Use `getUser()` or `getClaims()`.

---

## 13. `SECRET-04` — production source maps

**Mechanism.** A `.js.map` file's `sourcesContent` array holds the **verbatim text of your original files** — comments, dead code, commented-out credentials, internal endpoint constants, routes that were never linked in the UI. Read the trailing `//# sourceMappingURL=` comment on any chunk, fetch the map, and `jq -r '.sourcesContent[]'` reconstructs your tree. Findable at scale with `site:example.com filetype:map`.

**Why it ranks thirteenth.** It is a textbook case of why this ranking is not CVSS: on paper it is "information disclosure," a medium. In practice it is present in a large fraction of apps, found by a search-engine dork, and hands over an admin endpoint. The Sentry writeup's worked example — **the pentest firm blog.sentry.security, not Sentry.io** — recovered an unreferenced `updateUserData()` function taking `email, firstName, lastName, password, accessToken`, enumerated a `userId`, POSTed a new password, and got a `200 OK`. Full account takeover from a static file.

Same debugging-loop origin as `SECRET-01`: "the stack trace is minified" → `sourcemap: true` → never turned off. Error-tracker integration snippets also enable maps without including the delete step.

Source: `blog.sentry.security/abusing-exposed-sourcemaps/`

**Fastest detection.**

```bash
curl -sI "$APP/_next/static/chunks/main.js.map" | head -1      # want 404
```

Locally:

```bash
find .next dist build out -name '*.map' 2>/dev/null
grep -rn "sourcemap: *true\|productionBrowserSourceMaps" next.config.* vite.config.* 2>/dev/null
```

*True positive:* a `.map` fetches with 200 from the deployed site. The local `find` alone is not the finding — what matters is what shipped.

**Minimal correct fix.** `hidden` only removes the trailing comment; **deleting the maps from the deployed artifact is the actual control.**

```ts
// vite.config.ts
export default defineConfig({
  build: { sourcemap: 'hidden' },
})
```

```jsonc
// package.json — upload for debugging, then delete before the artifact ships
{
  "scripts": {
    "build": "vite build && npm run sourcemaps:ship",
    "sourcemaps:ship": "sentry-cli sourcemaps upload ./dist && find ./dist -name '*.map' -delete"
  }
}
```

For Next.js, leave `productionBrowserSourceMaps` at its default of `false`; if you turn it on for a debugging session, turn it off in the same commit.

---

## 14. `INJECT-11` — read-check-write races on credits and coupons

**Mechanism.** `if (user.credits > 0) { …do the expensive thing…; update(credits - 1) }`. That is the natural expression of the requirement, and every LLM writes it. Fifty parallel requests all read `credits = 1`, all pass the check, and all proceed. The same pattern reuses single-use coupons, farms referral bonuses, and races usernames.

**Why it ranks fourteenth.** It is universal, free to exploit, and it lands directly on the metered resource you are paying for. And it is **invisible in single-request testing, which is all a vibe coder does** — the feature works every time you click it.

Source: `owasp.org/www-community/pages/vulnerabilities/race_conditions`

**Fastest detection.** One line, and it is definitive. Run it against **staging**, not production:

```bash
seq 1 50 | xargs -P 50 -I{} curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST "$STAGING/api/spend-credit" -H "cookie: $COOKIE_A" | sort | uniq -c
```

*True positive:* more than one `200` on a one-credit account.

Code-side, look for the read and the write in separate statements:

```bash
grep -rn "credits\|balance\|quota\|redeemed" src/ app/ --include=*.ts | grep -E "findUnique|findFirst|select"
```

**Minimal correct fix.** One atomic conditional statement. Zero rows updated *is* the "insufficient" branch.

```sql
update users
   set credits = credits - $1
 where id = $2
   and credits >= $1
returning credits;
```

```ts
const rows = await sql`
  update users set credits = credits - ${n}
  where id = ${userId} and credits >= ${n}
  returning credits`
if (rows.length === 0) return new Response('Insufficient credits', { status: 402 })
```

Then let the database enforce the invariants so a future refactor cannot reintroduce the race:

```sql
alter table public.users add constraint users_credits_nonneg check (credits >= 0);

-- one-time actions: uniqueness is the lock
create table public.coupon_redemptions (
  coupon_id uuid not null references public.coupons(id),
  user_id   uuid not null references auth.users(id),
  redeemed_at timestamptz not null default now(),
  unique (coupon_id, user_id)
);
```

For multi-step flows that cannot be collapsed into one statement, use `select … for update` inside a transaction, or an optimistic version column. Add an append-only ledger with a UNIQUE idempotency key if the balance needs to be auditable.

---

## 15. `OPS-01` — nobody can tell whether it already happened

**Mechanism.** The meta-killer. Every other entry in this list is a bug; this one is what converts a bug into "an unbounded, undatable, un-notifiable breach."

**Why it ranks fifteenth and still belongs on the list.** Documented log retention: **Vercel Hobby, 1 hour.** Pro, 1 day. Pro + Observability Plus, 30 days. Enterprise, 3 days. Log **drains** — the only way to ship logs somewhere durable — are "available to all users on the Pro and Enterprise plans," so a Hobby project cannot configure one at all. Supabase states only that "log retention is based on your project's pricing plan." **A weekend IDOR discovered on Tuesday has zero forensic evidence.**

Meanwhile the clock on the other side is brutal. Unit 42 honeypots were compromised within 24 hours (80% within 24h) and Postgres instances within **30 seconds**; a GitHub-leaked AWS key is abused in about a minute. **Assume zero grace period between deploy and scan** — the security pass has to happen before the first public URL exists, because there is no window afterwards and no evidence either.

**The correct incident order is: preserve evidence → contain → patch → rotate → scope → notify.** Most people patch first, destroying the only signal that drives the notification decision. And **if the logs are gone, that absence IS the finding** — it forces you to treat the exposure window as maximum scope, which is exactly what drives a notification obligation you would otherwise have argued out of.

Sources: `vercel.com/docs/observability` · `unit42.paloaltonetworks.com`

**Fastest detection.** Ask the question the incident will ask:

```bash
vercel logs <deployment-url> --since 48h | head
```

*True positive:* nothing older than your plan's retention comes back — on Hobby, that is one hour. Confirm there is no durable destination configured, and check whether you have any application-level audit trail at all:

```bash
grep -rn "security_events\|audit_log\|authz.denied" src/ app/ supabase/ || echo "NO APPLICATION AUDIT TRAIL — this is the finding"
```

**Minimal correct fix.** Your own append-only table that outlives your platform's retention, with UPDATE and DELETE revoked from the application role so a compromised app credential cannot rewrite history.

```sql
create table public.security_events (
  id          bigserial primary key,
  occurred_at timestamptz not null default now(),
  actor_id    uuid,
  event       text not null,          -- 'auth.login.failure', 'authz.denied', 'entitlement.granted'
  target      text,                   -- identifiers only, never values
  ip          inet,
  user_agent  text,
  detail      jsonb not null default '{}'::jsonb
);

alter table public.security_events enable row level security;
revoke update, delete on public.security_events from authenticated, anon;
revoke all on public.security_events from anon;
grant insert on public.security_events to authenticated;
create index security_events_occurred_at_idx on public.security_events (occurred_at desc);
```

Write the audit row **inside the same transaction as the privileged mutation**, log identifiers and outcomes rather than values, and keep 90 days minimum.

**When something has actually happened, export before you touch anything.** Supabase edge logs carry exactly the fields you need — `request.headers.cf_connecting_ip`, `request.path`, `request.method`, `request.headers.user_agent`, `response.status_code`. Query them for: 200s on `/rest/v1/<table>` from non-app IPs; any `PATCH`/`DELETE` on tables the UI never writes; signup bursts followed immediately by reads; and `python-requests` / `curl` / `Go-http-client` user agents.

**Contain before patching.** A one-line tourniquet stops PostgREST reads without a deploy and without logging anyone out:

```sql
revoke all on public.<table> from anon;
```

Then patch, then rotate, then scope, then notify. Start a written timeline the moment personal-data exposure is confirmed — discovery time, exposure window, tables and columns, row counts, evidence preserved or missing. Under GDPR Art. 33(5) that document *is* the record you are required to keep, it is what makes phased notification defensible, and it costs ten minutes if you start it immediately (`OPS-02`).

---

## Deliberately not in the top 15, and why

A ranking is only credible if it says what it left out. All four of these are real vulnerabilities that appear in the full catalog. None of them earns a top-15 slot **for this population**, and the reasons are specific rather than dismissive.

| Excluded | ID | Why it is not top-15 here |
|---|---|---|
| CSRF | `INJECT-09` | Real, but Next.js Server Actions carry an automatic Origin check, and the highest-value targets in this population are already **unauthenticated** — there is no session to ride. |
| Clickjacking and the missing security-header set | `INFRA-09` | Near-universal, and rarely the step that loses the data. Fix it in the THIS MONTH tier, not ahead of an open database. |
| Weak password hashing | companion of `INJECT-13` | BaxBench's second-strongest empirical signal (CWE-522 occurrence **1.00** in SecretStorage, **0.94** in UptimeService) — but most vibe-coded apps now delegate to a managed auth provider and **never store a password at all**. Where the app does store one, it moves straight to IMMEDIATE. |
| Prototype pollution | `INJECT-08` | High impact, but needs a hand-rolled deep merge, which modern scaffolds mostly avoid. |

Two more exclusions worth stating explicitly, because they are what a naive scanner reports first and what a builder panics about:

- **Nothing about a visible public key is on this list.** Supabase publishable/anon (`NOTVULN-01`), Firebase web config (`NOTVULN-02`), Stripe and Clerk `pk_` (`NOTVULN-03`), PostHog `phc_` / Sentry DSN / Mapbox `pk.` / Google Maps (`NOTVULN-04`), the Supabase OpenAPI root and GraphQL introspection (`NOTVULN-05`), a JWT in the browser (`NOTVULN-07`), `dangerouslySetInnerHTML` wrapped in a real sanitizer (`NOTVULN-09`), `target="_blank"` without `rel="noopener"` (`NOTVULN-10`, solved by modern browsers), a CORS error in the console (`NOTVULN-11`), a public repo or a discoverable subdomain (`NOTVULN-13`). The finding is always the missing control behind the visible thing.
- **Low-severity dependency noise is not a killer** (`NOTVULN-12`). `brace-expansion` CVE-2025-5889 is **CVSS 1.3 LOW** and is the advisory that most often panics a builder; `ajv` CVE-2025-69873 is **disputed — 2.9 LOW from the CNA, 7.5 HIGH from CISA/Red Hat** — and is only exploitable if you enable `$data`, which is off by default. Fix them, but do not let them crowd out `path-to-regexp` CVE-2026-4867 (CVSS 7.5, all versions **before 0.1.13**, reachable from any request to a matching route). **Report reachability, not count.**

---

## Exploitation chains: why deferring "mediums" is how these apps get owned

A severity-sorted list trains a reader to defer mediums. **Every real breach in the record is a chain.** For this threat model, composite severity approximates the **product of exploitabilities, not the maximum of the parts** — because there is no SOC, no WAF, and a time-to-detect of "whenever they next look." Two mediums that compose are not a medium.

The six worked chains, all built from findings already in the catalog:

**A. Recon → dump.**
Verbose error (`INFRA-05`) reveals the ORM and schema → the Supabase OpenAPI root (`NOTVULN-05`) maps every table and RPC → one table missing RLS (`BAAS-01`) → full export with the publishable key. **No authentication anywhere in the chain.**
*Terminates in:* all customer data.

**B. Source map → cross-tenant write.**
Production source map (`SECRET-04`) → an admin route that was never linked in the UI → method flip on a handler that checks `GET` and not `DELETE` (`AUTHZ-02`) → cross-tenant mutation (`AUTHZ-06`).
*Terminates in:* all customer data.

**C. Enumeration → account takeover.**
The signup response distinguishes existing emails (`ABUSE-05`) → no login rate limit (`ABUSE-01`) → credential stuffing → no MFA, or a TOTP fallback that nullifies the passkey (`OPS-08`) → a session cookie that logout never revokes (`AUTHN-06`).
*Terminates in:* account takeover.

**D. Subdomain takeover → session theft, with no XSS at all.**
Dangling CNAME (`INFRA-08`) → attacker claims the subdomain → cookies set with `Domain=example.com` are sent to it → full session hijack. **This chain is invisible to every XSS control in the catalog**, and it is the one people miss, because no single step is high severity.
*Terminates in:* session theft.

**E. Open redirect → OAuth token theft.**
`?next=//evil.tld` (`INJECT-13` family) on an allowlisted redirect host → the authorization server delivers the `code` there (`AUTHN-03`) → and if a `window.open` popup carried it, `CLIENT-05` completes the hop.
*Terminates in:* account takeover.

**F. Preview deployment → production data → immutable exposure.**
Public preview (`INFRA-01`) with `DATABASE_URL` scoped to all environments → the production database from a half-finished build → and because Skew Protection keeps old deployments serving (`INFRA-07`), **shipping the fix does not close it.**
*Terminates in:* all customer data.

| Chain | Steps | Terminates in |
|---|---|---|
| A | verbose error → OpenAPI schema dump → RLS gap → bulk read | all customer data |
| B | source map → hidden admin route → BFLA method-flip → cross-tenant write | all customer data |
| C | enumeration → no login rate limit → credential stuffing → no MFA | account takeover |
| D | subdomain takeover → parent-domain cookie → session theft | session theft, **with no XSS anywhere** |
| E | open redirect → OAuth `redirect_uri` → token theft | account takeover |
| F | preview deployment → production database → old immutable deploys still live | all customer data |

### The triage rule

**Find the chains that terminate in "all customer data" or "money moves," then fix the cheapest, earliest step in each.** In chain A that is one `alter table … enable row level security`. In chain F it is one environment-variable scope change. Neither of those is the highest-severity finding in its chain, and both of them break the chain outright.

Three modifiers move a finding up a tier regardless of where it sits in this ranking:

1. **Is it reachable pre-auth?** Anything an anonymous `curl` reaches is worth more than the same bug behind a login, because attack volume against the internet-facing surface is continuous and automated.
2. **Does the data trigger a regulatory clock?** GDPR Art. 33's 72-hour notification duty starts at "aware," not at "confirmed." The FTC Health Breach Notification Rule sets a hard **60-calendar-day** deadline (16 CFR 318.4), and 16 CFR 318.2 expressly includes fitness, fertility, sexual health, sleep, mental health, genetic information and diet. Washington's My Health My Data adds a **45-day** deletion duty reaching "archived or backup systems." Under-13 users bring COPPA. **A finding that leaks regulated data is never a medium.**
3. **Can you even investigate?** If you cannot reconstruct the exposure window (`OPS-01`), treat every finding on that surface as maximum scope.
