# Proving authorization works — the section that matters most

Load this file for any app that has accounts, tenants, roles, or per-user data — which is every app past a static landing page. It is the only part of the audit that can find `AUTHZ-01` (IDOR/BOLA), `AUTHZ-02` (function-level authz), `AUTHZ-06` (multi-tenant leakage), `AUTHZ-08` (service-role routes), `AUTHZ-09` (fail-open), and `AUTHZ-11` (the route that missed the guard, and the cache that leaks across identities), because no scanner in the rest of the toolkit can. Load it during the audit whenever `AUTHZ-10` applies, at GATE 4 → 5 before deploy, and **always** before making a public "try to hack this" claim — the saved artifacts in the last section are what make that claim defensible instead of a bet.

---

## 1. `AUTHZ-10` — static analysis structurally cannot find this. Say so out loud.

This is not tool immaturity. It is definitional. A missing authorization check is not a *pattern* — it is the *absence* of a check the tool has no way to know was required. `db.post.findUnique({where:{id}})` is indistinguishable at the AST level from the same line in an app where posts are genuinely public. The intended policy lives only in the founder's head.

The evidence, verified by directory listing through the GitHub contents API: **CodeQL's JavaScript security query pack (`javascript/ql/src/Security/`) has directories for 56 CWEs. There is no `CWE-285` (Improper Authorization), no `CWE-863` (Incorrect Authorization), and no `CWE-639` (Authorization Bypass Through User-Controlled Key) directory. The `CWE-862` directory contains exactly two files — `EmptyPasswordInConfigurationFile.ql` and its `.qhelp`.** That is the entirety of CodeQL's JavaScript coverage of "Missing Authorization." `[CONFIRMED]`

> Stated narrowly on purpose: this is asserted from the query-pack directory listing, not from running CodeQL. A CWE-862-relevant query could live in another directory or in a `security-experimental` pack that was not enumerated. Do not widen the claim beyond what is written above.

| Tool the audit already recommends | Why it cannot answer this question |
|---|---|
| **CodeQL** | See above. No query directory exists for the three relevant CWEs. |
| **Semgrep** (free `semgrep scan --error`) | Pattern rules match syntax that is *present*. Missing authz is syntax that is *absent*. |
| **Semgrep Multimodal** | Vendor claims it "automatically identif[ies] complex business logic flaws, such as insecure direct object references (IDORs) and broken authorization" — but it *"Requires the Semgrep AppSec Platform"*, is probabilistic, and the only published accuracy figure is a **triage** metric ("over 95% accurate in categorizing Semgrep Code findings as false positives"), not a detection-recall metric for IDOR. **Lead generator, never a gate.** |
| **`zap-baseline.py`** | Passive and single-identity. It never holds a second account, so it can never diff. |
| **nuclei** | Its fuzzing engine derives every decision from **one** request/response pair and defines no multi-identity primitive. BOLA is by definition a two-identity property: identical request, different caller, same response = bug. Use nuclei for the unauthenticated tier only. |
| **OSV-Scanner / Trivy / gitleaks / Socket** | Answer a completely different question. Not in scope for this class at all. |

**Correct the rest of the toolkit explicitly when you report.** Recommending `zap-baseline.py` or nuclei as the access-control control is the specific mistake this file exists to fix. If a security write-up (yours or anyone's) claims a scanner covers access control, delete the claim.

What *does* work is **differential testing**: two accounts in two tenants, every object, every endpoint, replayed as the other user and as nobody, responses diffed. OWASP's Web Security Testing Guide says it plainly — *"The best way to test for direct object references would be by having at least two (often more) users to cover different owned objects and functions."* ASVS 5.0 makes it **Level 1** (minimum, applies to every app) in three places, including 8.2.2: *"data-specific access is restricted to consumers with explicit permissions to specific data items to mitigate insecure direct object reference (IDOR) and broken object level authorization (BOLA)."* OWASP Top 10:2025 A01 states the constructive half: *"Developers and QA staff should include functional access control in their unit and integration tests."* `[CONFIRMED]`

### The 30-second version of this whole file

```bash
# Does any test in this repo ever authenticate as a SECOND user?
grep -rlniE "user(B|2)|otherUser|secondUser|tenantB|otherOrg" test/ tests/ e2e/ __tests__/ 2>/dev/null \
  || echo "NO DIFFERENTIAL AUTHZ TESTS EXIST — this is the finding"

# Every DB read/write keyed on an id with no tenant/owner predicate nearby.
grep -rnE "(findUnique|findFirst|update|delete|upsert)\(\{\s*where:\s*\{\s*id" app/ src/ lib/ data/ 2>/dev/null \
  | grep -vE "userId|orgId|ownerId|tenantId|workspaceId|accountId"
```

**If your security process has no step where a *second* set of credentials replays the *first* user's traffic, you have no access-control testing at all, whatever your scanner dashboard says.**

### The four layers, and what each one proves

| Layer | Artifact | Proves | Blind to |
|---|---|---|---|
| HTTP, point-in-time | `authz-diff.ts` (§2) | Cross-tenant isolation over the real wire, including mutations | Regressions after today; routes not in the manifest |
| Test suite, continuous | `tests/authz.spec.ts` + required CI check (§3) | The policy matrix still holds on every PR | Routes with no matrix row |
| Inventory | `scripts/route-coverage.mjs` (§4) | No entrypoint was forgotten | Whether the covered route is actually *correct* |
| Data layer | pgTAP (§5) / `rules-unit-testing` (§7) | The database or rules engine itself refuses the query | Any app route holding a `BYPASSRLS` key (§6) |

None of these substitutes for another. §6 exists because a green data layer plus a leaking app is the most common way this goes wrong.

---

## 2. `authz-diff.ts` — the standalone differential harness

Runs with `npx tsx authz-diff.ts`. No framework, no Burp, no account. **🔴 STAGING ONLY — the DELETE cases mutate data. Seed fresh fixtures per run and never point it at production.**

Get the two cookies once from a real login in two browser profiles (DevTools → Network → any request → copy the `Cookie` request header). Keep them out of git.

```ts
// authz-diff.ts — run: npx tsx authz-diff.ts  (needs Node 20+)
// Proves cross-tenant isolation over HTTP. Exit code 1 on any leak.

type Identity = { name: string; headers: Record<string, string> }
type Case = {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** {id} is substituted with the *victim's* object id */
  path: string
  body?: unknown
  /** statuses that mean "correctly refused" */
  denyStatus?: number[]
}

const BASE = process.env.BASE_URL ?? 'http://localhost:3000'
const DENY_DEFAULT = [401, 403, 404]

// 1. Identities. Get these once, from a real login, and keep them out of git.
const alice:   Identity = { name: 'alice(tenantA)',   headers: { cookie: process.env.COOKIE_A! } }
const mallory: Identity = { name: 'mallory(tenantB)', headers: { cookie: process.env.COOKIE_B! } }
const nobody:  Identity = { name: 'anonymous',        headers: {} }

// 2. The manifest. Generate the skeleton with route-coverage.mjs (§4),
//    then fill in one victim-owned id per resource.
const VICTIM_POST_ID = process.env.VICTIM_POST_ID!   // an object owned by ALICE
const cases: Case[] = [
  { method: 'GET',    path: `/api/posts/${VICTIM_POST_ID}` },
  { method: 'PATCH',  path: `/api/posts/${VICTIM_POST_ID}`, body: { title: 'pwned' } },
  { method: 'DELETE', path: `/api/posts/${VICTIM_POST_ID}` },
  { method: 'GET',    path: `/api/posts` },        // list route: must not contain victim data
  { method: 'GET',    path: `/api/admin/users` },  // privileged route
]

async function fire(id: Identity, c: Case) {
  const res = await fetch(BASE + c.path, {
    method: c.method,
    headers: { 'content-type': 'application/json', ...id.headers },
    body: c.body ? JSON.stringify(c.body) : undefined,
    redirect: 'manual',
  })
  const text = await res.text()
  return { status: res.status, len: text.length, text }
}

let failures = 0
const readBack = (c: Case): Case => ({ method: 'GET', path: c.path.split('?')[0] })

for (const c of cases) {
  // ORDER MATTERS. Attacker attempts run FIRST, because the owner baseline for a
  // DELETE actually deletes the fixture and would poison every later assertion.
  const before = await fire(alice, readBack(c))
  if (before.status >= 400 && c.method !== 'POST') {
    console.log(`SKIP  ${c.method} ${c.path} — owner cannot even read it (${before.status}); fix the manifest`)
    continue
  }

  for (const attacker of [mallory, nobody]) {
    const got = await fire(attacker, c)
    const refused = (c.denyStatus ?? DENY_DEFAULT).includes(got.status)
    if (!refused) {
      failures++
      const identical = got.text === before.text
      console.log(
        `LEAK  ${c.method} ${c.path}  as ${attacker.name} -> ${got.status} ` +
        `(${got.len}B${identical ? ', BYTE-IDENTICAL to owner view' : ''})`,
      )
    } else {
      console.log(`ok    ${c.method} ${c.path}  as ${attacker.name} -> ${got.status}`)
    }

    // A refusal status that still mutated is its own bug class. Re-read as the owner.
    if (c.method !== 'GET') {
      const after = await fire(alice, readBack(c))
      if (c.method === 'DELETE' && before.status < 400 && after.status === 404) {
        failures++; console.log(`LEAK  ${c.path} was actually DELETED by ${attacker.name} despite ${got.status}`)
        break // fixture is gone; stop testing this case
      }
      if (c.method !== 'DELETE' && after.text !== before.text) {
        failures++; console.log(`LEAK  ${c.path} was actually MUTATED by ${attacker.name} despite ${got.status}`)
      }
    }
  }

  // Owner baseline LAST, so a destructive baseline cannot invalidate the attacker results.
  const owner = await fire(alice, c)
  if (owner.status >= 400) console.log(`WARN  owner got ${owner.status} on ${c.method} ${c.path} — case may be mis-specified`)
}
console.log(failures === 0 ? '\nPASS — no cross-tenant access' : `\nFAIL — ${failures} leak(s)`)
process.exit(failures === 0 ? 0 : 1)
```

Run it:

```bash
npm i -D tsx
BASE_URL=https://staging.example.com \
COOKIE_A='sb-access-token=...; sb-refresh-token=...' \
COOKIE_B='sb-access-token=...; sb-refresh-token=...' \
VICTIM_POST_ID=8f3c... \
npx tsx authz-diff.ts

# Second pass: confirm the anonymous lane actually fires.
COOKIE_B= npx tsx authz-diff.ts
```

Add `"authz:diff": "tsx authz-diff.ts"` to `package.json` and run it against a preview deployment before every production promotion.

**Two details usually got wrong.**

- **(a) The owner-baseline call is not optional.** Without it, a typo'd path produces 404 for everyone and the harness reports a clean pass on an endpoint it never reached. That is why a `SKIP` line is printed rather than swallowed — a run full of `SKIP` is a broken manifest, not a secure app.
- **(b) The byte-identical comparison is what separates the two 200s.** "Attacker got a 200 with an empty list" is correct filtering. "Attacker got the victim's exact payload" is a confirmed leak. Autorize's *Enforcement Detectors* (§9) implement the same idea with content-length and body regex.

**Stated limits, from the researcher:** the harness matches on path, it mutates data, and it assumes a disposable environment.

### True positive vs. false positive when reading the output

| Output line | Real finding | Common false positive |
|---|---|---|
| `LEAK … -> 200 (0B)` on a **list** route | Attacker received victim rows in a collection | An empty list rendered as `[]` or `{"data":[]}` — correct filtering. Read the body before filing. |
| `LEAK … -> 200, BYTE-IDENTICAL to owner view` | Always real. Same bytes, different caller. | None. This one you file. |
| `LEAK … -> 302` | The redirect target carries the data, or the mutation already happened | A redirect to `/login` is a *deny*. Add `302` to `denyStatus` only after confirming the `Location` header points at the login page. |
| `SKIP … owner cannot even read it (404)` | — | Not a finding at all. Your manifest path or fixture id is wrong. Fix and re-run. |
| Everything `ok`, every case `SKIP` | — | A clean-looking pass that proves nothing. Verify at least one owner baseline returned < 400. |

---

## 3. The Playwright policy matrix, wired as a REQUIRED CI check

§2 is a point-in-time audit. The regression is what kills you — an agent refactors `lib/authz.ts` three weeks later, a guard stops being called, and nothing fails. Worse, and specific to this audience: **an agent will happily delete an authorization check to make a failing test pass**, because the test told it "expected 200, got 403." Only a test that asserts **403** survives that.

```ts
// tests/authz.spec.ts — npx playwright test tests/authz.spec.ts
import { test, expect, request as pwRequest, type APIRequestContext } from '@playwright/test'

const BASE = process.env.BASE_URL ?? 'http://localhost:3000'

type Role = 'anon' | 'member' | 'otherTenant' | 'admin'
const ctx: Partial<Record<Role, APIRequestContext>> = {}

test.beforeAll(async () => {
  // Isolated contexts: pwRequest.newContext() has its own cookie storage,
  // so signing in as one user cannot contaminate another.
  ctx.anon = await pwRequest.newContext({ baseURL: BASE })
  for (const [role, state] of [
    ['member',      'storage/member.json'],
    ['otherTenant', 'storage/other.json'],
    ['admin',       'storage/admin.json'],
  ] as const) {
    ctx[role] = await pwRequest.newContext({ baseURL: BASE, storageState: state })
  }
})
test.afterAll(async () => { for (const c of Object.values(ctx)) await c?.dispose() })

// THE POLICY MATRIX. One row = one cell of (role × endpoint × object-owner).
// `object` is always owned by `member`. This table IS your authorization spec.
const MATRIX: Array<{
  method: 'get' | 'post' | 'patch' | 'delete'
  path: string; role: Role; expect: 'allow' | 'deny'; body?: object
}> = [
  { method: 'get',    path: '/api/posts/{POST_ID}', role: 'member',      expect: 'allow' },
  { method: 'get',    path: '/api/posts/{POST_ID}', role: 'otherTenant', expect: 'deny'  },
  { method: 'get',    path: '/api/posts/{POST_ID}', role: 'anon',        expect: 'deny'  },
  { method: 'patch',  path: '/api/posts/{POST_ID}', role: 'otherTenant', expect: 'deny', body: { title: 'x' } },
  { method: 'delete', path: '/api/posts/{POST_ID}', role: 'otherTenant', expect: 'deny'  },
  { method: 'delete', path: '/api/posts/{POST_ID}', role: 'admin',       expect: 'allow' },
  { method: 'get',    path: '/api/admin/users',     role: 'member',      expect: 'deny'  },
  { method: 'post',   path: '/api/org/invite',      role: 'member',      expect: 'deny', body: { email: 'a@b.c', role: 'owner' } },
]

for (const row of MATRIX) {
  const name = `${row.expect.toUpperCase()} ${row.method.toUpperCase()} ${row.path} as ${row.role}`
  test(name, async () => {
    const url = row.path.replace('{POST_ID}', process.env.MEMBER_POST_ID!)
    const res = await ctx[row.role]![row.method](url, row.body ? { data: row.body } : {})
    if (row.expect === 'allow') {
      expect(res.status(), await res.text()).toBeLessThan(400)
    } else {
      // Assert DENY explicitly. 200 is a failure even if the body is empty.
      expect([401, 403, 404], `got ${res.status()}: ${await res.text()}`).toContain(res.status())
    }
  })
}

// Mass-assignment / BOPLA cell (AUTHZ-05): allowed to update, NOT allowed to update this field.
test('DENY privilege field via legitimate update', async () => {
  const res = await ctx.member!.patch(`/api/me`, { data: { name: 'ok', role: 'admin', credits: 999999 } })
  const me = await (await ctx.member!.get('/api/me')).json()
  expect(me.role,    'role was mass-assignable').not.toBe('admin')
  expect(me.credits, 'credits were mass-assignable').not.toBe(999999)
  expect(res.status()).toBeLessThan(500)
})

// Fail-open cell (AUTHZ-09). Point BROKEN_ENV_URL at an instance booted with AUTH_SECRET unset.
// Skips itself when that instance is not provisioned, so the suite stays green locally.
test('DENY when the auth provider is misconfigured (fail closed)', async () => {
  test.skip(!process.env.BROKEN_ENV_URL, 'BROKEN_ENV_URL not set — see the ci-fail-open workflow leg')
  const res = await fetch(`${process.env.BROKEN_ENV_URL}/api/me`)
  expect([401, 403, 500]).toContain(res.status)   // 200 here is CVE-2026-73421's exact shape
})
```

Generate the three `storageState` files once with a seeding script so CI does not need real credentials in the matrix job:

```ts
// tests/global-setup.ts — referenced from playwright.config.ts as `globalSetup`
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE_URL ?? 'http://localhost:3000'
const USERS = [
  { file: 'storage/member.json', email: 'member@test.local',  pw: process.env.PW_MEMBER! },
  { file: 'storage/other.json',  email: 'other@test.local',   pw: process.env.PW_OTHER!  },
  { file: 'storage/admin.json',  email: 'admin@test.local',   pw: process.env.PW_ADMIN!  },
]

export default async function globalSetup() {
  mkdirSync('storage', { recursive: true })
  const browser = await chromium.launch()
  for (const u of USERS) {
    const page = await browser.newPage({ baseURL: BASE })
    await page.goto('/login')
    await page.getByLabel(/email/i).fill(u.email)          // adjust selectors to your login form
    await page.getByLabel(/password/i).fill(u.pw)
    await page.getByRole('button', { name: /sign in|log in/i }).click()
    await page.waitForURL(/\/(dashboard|app|)$/)
    await page.context().storageState({ path: u.file })
    await page.close()
  }
  await browser.close()
}
```

```yaml
# .github/workflows/authz.yml — this is the gate
name: authz
on: [pull_request, push]
jobs:
  authz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npm run seed:test-tenants        # creates member/otherTenant/admin + fixtures
      - run: npx playwright test tests/authz.spec.ts
      - run: node scripts/route-coverage.mjs  # fails if a route has no matrix row
```

Then make the `authz` check **required** in branch protection (GitHub → Settings → Branches → branch protection rule → *Require status checks to pass* → tick `authz`). **A required check is the difference between a test suite and a control.** Verify it with the CLI rather than trusting the UI:

```bash
gh api repos/:owner/:repo/branches/main/protection/required_status_checks --jq '.contexts'
# must contain "authz"
```

**Health metric — run this every time you touch the matrix:**

```bash
grep -c "expect: 'deny'"  tests/authz.spec.ts
grep -c "expect: 'allow'" tests/authz.spec.ts
```

If the deny count is zero, or much smaller than the allow count, you wrote a functionality suite, not an authorization suite. **A healthy matrix has more deny rows than allow rows.**

**And add a matrix row for every *second* entrypoint.** An MCP server, a mobile API, an admin panel, or an OAuth/integration surface is an authorization-boundary twin written later by a different prompt. n8n **CVE-2026-65594** (GHSA-q5xf-xhwf-cwqf, CWE-863) is the reference case: the OAuth 2.1 consent flow *"lacked verification that the authenticated user has access to the workflow referenced as the OAuth resource"*, so a member could self-approve consent for another user's workflow and execute it with the owner's stored credentials. `[CONFIRMED]`

---

## 4. `scripts/route-coverage.mjs` — the inventory diff that catches "one route missed the guard"

Four 2026 advisories are the identical shape — one endpoint in a family lacks the middleware its siblings have — and no SAST tool flagged any of them. This is `AUTHZ-11`.

| Advisory | Package / versions | Endpoint | What was missing |
|---|---|---|---|
| **CVE-2026-70475** (GHSA-fm2f-4339-4p2f, CVSS 7.1, CWE-862) | `flowise` ≤ 3.1.2, fixed 3.1.3 | `PUT /api/v1/executions/:id` | *"lacks the `checkAnyPermission()` middleware that protects all other execution endpoints (GET, DELETE)"* |
| **CVE-2026-69252** (GHSA-wp74-f5hh-5f3r, CVSS 7.2) | `flowise` ≤ 3.1.2, fixed 3.1.3 | `GET`/`DELETE /api/v1/files` | no `checkPermission(...)`; DELETE acted on `activeOrganizationId` + a user-controlled path |
| **CVE-2026-73301** (GHSA-4qcj-m5wp-jmf4, CVSS 4.3) | `@budibase/server` ≤ 3.38.1 | `GET /api/global/groups` | no `auth.builderOrAdmin`; the sibling `GET /:groupId` had it |
| **CVE-2026-70476** (GHSA-gmmw-qg98-6j6p, CVSS 8.3) | `flowise` billing routes | `POST .../update-subscription-plan` | trusted a user-supplied `subscriptionId` — ownership missing on a **body field**, not a path param |

```js
#!/usr/bin/env node
// scripts/route-coverage.mjs — run: node scripts/route-coverage.mjs
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

const ROOTS = ['app', 'src/app', 'pages/api', 'src/pages/api'].filter(existsSync)
const TEST_GLOBS = ['tests', 'test', 'e2e', '__tests__'].filter(existsSync)
const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    if (e === 'node_modules' || e.startsWith('.')) continue
    const p = join(dir, e)
    statSync(p).isDirectory() ? walk(p, out) : out.push(p)
  }
  return out
}

// app/api/posts/[id]/route.ts -> /api/posts/[id]   (route groups "(x)" and slots "@x" removed)
const toUrl = (file, root) =>
  '/' + relative(root, file).split(sep).slice(0, -1)
    .filter((s) => !(s.startsWith('(') && s.endsWith(')')) && !s.startsWith('@'))
    .join('/')

const surface = []
for (const root of ROOTS) {
  for (const file of walk(root)) {
    const src = readFileSync(file, 'utf8')
    const base = file.split(sep).pop()
    if (/^route\.(t|j)sx?$/.test(base)) {
      for (const m of METHODS) {
        if (new RegExp(`export\\s+(async\\s+)?(function|const)\\s+${m}\\b`).test(src))
          surface.push({ kind: 'route', id: `${m} ${toUrl(file, root)}`, file })
      }
    } else if (root.includes('pages' + sep + 'api') && /\.(t|j)sx?$/.test(base)) {
      surface.push({ kind: 'pages-api', id: `ANY /${relative(root, file).replace(/\.(t|j)sx?$/, '')}`, file })
    }
    if (/^\s*['"]use server['"]/m.test(src)) {
      for (const [, fn] of src.matchAll(/export\s+async\s+function\s+([A-Za-z0-9_$]+)/g))
        surface.push({ kind: 'server-action', id: `ACTION ${fn}`, file })
    }
  }
}

const testCorpus = TEST_GLOBS.flatMap((d) => walk(d))
  .filter((f) => /\.(t|j)sx?$/.test(f)).map((f) => readFileSync(f, 'utf8')).join('\n')

const uncovered = surface.filter((s) => {
  const needle = s.kind === 'server-action'
    ? s.id.replace('ACTION ', '')
    : s.id.split(' ')[1].replace(/\[(\.\.\.)?([^\]]+)\]/g, '')  // match ignoring dynamic segments
  return !testCorpus.includes(needle)
})

console.log(`surface: ${surface.length} entrypoints; uncovered: ${uncovered.length}`)
for (const u of uncovered) console.log(`  UNCOVERED  ${u.id}   (${u.file})`)
process.exit(uncovered.length === 0 ? 0 : 1)
```

```bash
chmod +x scripts/route-coverage.mjs
node scripts/route-coverage.mjs
# add to package.json: "authz:coverage": "node scripts/route-coverage.mjs"
```

The first run prints a long list. **That list is your work queue**, and it is the artifact that would have caught all four advisories above.

Verified by its author against a synthetic tree: 6 entrypoints, 2 correctly flagged, exit 1.

**Stated limits — be honest about these when you report, or the tool loses credibility on its first false alarm:**

| Behaviour | True positive | False positive / false pass |
|---|---|---|
| Matches on **path, not method** | An entire route file absent from the test corpus | One test mentioning `/api/posts/[id]` marks that route's `GET`, `PATCH` **and** `DELETE` all covered. A method-flip bug (`AUTHZ-02`) hides here. |
| Substring match against the test corpus | Endpoint never named anywhere in tests | False *pass* when the path string appears in a non-authz test (a smoke test, a fixture, a comment). |
| Literal-string matching | — | False *failure* when you build URLs from constants (`` `${API}/posts/${id}` ``). Inline the literal in the test, or accept the noise. |

**It is a ratchet against forgotten endpoints, not a proof.** The proof is the deny row in §3.

Manual quick pass — run this even if you never wire the script:

```bash
# Route handlers that never mention a session/auth call — the shortlist to audit first.
for f in $(find app src/app -name 'route.ts' -o -name 'route.js' 2>/dev/null); do
  grep -qE "auth\(|getUser\(|getSession\(|currentUser\(|requireUser\(" "$f" || echo "NO AUTH CALL: $f"
done

# Server Actions are public POST endpoints (AUTHZ-04). Enumerate every file that exports one.
grep -rl "^'use server'\|^\"use server\"" app/ src/ 2>/dev/null | sort
```

---

## 5. RLS testing lies by default — and the pgTAP suite that doesn't

### Why the default is a lie

PostgreSQL's manual is unambiguous: *"Superusers and roles with the `BYPASSRLS` attribute always bypass the row security system when accessing a table. Table owners normally bypass row security as well."* `[CONFIRMED]`

Now list the three ways a founder actually "tests" RLS:

| How they test | Runs as | Result |
|---|---|---|
| Run the migration and see no error | **table owner** | Bypasses RLS |
| `select * from documents` in the Supabase SQL editor | **table owner** | Bypasses RLS |
| Click around the deployed app | often the **service_role** key on at least one route | `BYPASSRLS` |

**The three ways a founder normally "tests" RLS are the three ways RLS is bypassed.** They see their own rows, conclude the policy works, and ship. The attacker, meanwhile, arrives as `anon` or `authenticated` through PostgREST with the publishable key and hits the policy for real.

**Second trap: `USING` failures are silent.** Supabase documents the split directly — a `USING`-clause miss returns **zero rows with no error**, while `WITH CHECK` violations and missing grants raise SQLSTATE **`42501`**. A test suite built only on `throws_ok` passes against a completely wide-open UPDATE policy, because the wide-open policy also does not throw. This is why the UPDATE and DELETE assertions below **count affected rows** and only the INSERT/ownership-transfer assertions use `throws_ok`.

**Third trap: policies do not revoke grants.** Enabling RLS does not take back a `grant` that was already issued. A table "protected only by policies" still hands `anon` an insert path if the grant was never revoked. Grants first, policies second.

### The schema — grants, `force`, and a policy per verb

```sql
-- Grants first, policies second.
revoke all on table public.documents from anon, authenticated;
grant select, insert, update, delete on table public.documents to authenticated;
alter table public.documents enable row level security;
alter table public.documents force row level security;   -- owner is no longer exempt

create policy docs_select on public.documents for select to authenticated
  using (auth.uid() is not null and auth.uid() = owner_id);
create policy docs_insert on public.documents for insert to authenticated
  with check (auth.uid() = owner_id);                    -- cannot insert a row owned by someone else
create policy docs_update on public.documents for update to authenticated
  using  (auth.uid() = owner_id)                         -- which rows you may touch
  with check (auth.uid() = owner_id);                    -- what they may become (stops ownership transfer)
create policy docs_delete on public.documents for delete to authenticated
  using (auth.uid() = owner_id);
```

`force row level security` is the single line that makes every test below meaningful, because the test session runs as the table owner.

### The suite

`supabase/tests/database/010-documents-rls.test.sql`, run with `supabase test db`:

```sql
begin;
create extension if not exists pgtap with schema extensions;
select plan(11);

insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'alice@test.local'),
  ('22222222-2222-2222-2222-222222222222', 'mallory@test.local');
insert into public.documents (id, owner_id, org_id, body) values
  ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111',
   '00000000-0000-0000-0000-0000000000aa', 'alice secret'),
  ('bbbbbbbb-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222',
   '00000000-0000-0000-0000-0000000000bb', 'mallory doc');

-- Structural assertions (cheap, catch the "RLS silently off" regression).
select is((select relrowsecurity from pg_class where oid = 'public.documents'::regclass),
          true, 'RLS is enabled on public.documents');
select is((select relforcerowsecurity from pg_class where oid = 'public.documents'::regclass),
          true, 'RLS is FORCED (table owner is not exempt)');
select policies_are('public', 'documents',
  array['docs_select','docs_insert','docs_update','docs_delete'], 'all four command policies exist');
select table_privs_are('public','documents','anon', array[]::text[], 'anon has NO grants on documents');

-- Become Mallory. This is the part every hand-written "RLS test" omits.
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}', true);

select is_empty(
  $$ select body from public.documents where id = 'aaaaaaaa-0000-0000-0000-000000000001' $$,
  'mallory cannot SELECT alice''s document');
select results_eq(
  $$ select body from public.documents where id = 'bbbbbbbb-0000-0000-0000-000000000002' $$,
  $$ values ('mallory doc') $$,
  'mallory CAN select her own document');     -- positive case: proves it is not "deny everything"

-- A USING-clause miss returns zero rows, it does NOT raise. Assert the row count.
select is((with u as (update public.documents set body = 'pwned'
           where id = 'aaaaaaaa-0000-0000-0000-000000000001' returning 1)
           select count(*)::int from u), 0, 'mallory''s UPDATE of alice''s doc affects zero rows');
select is((with d as (delete from public.documents
           where id = 'aaaaaaaa-0000-0000-0000-000000000001' returning 1)
           select count(*)::int from d), 0, 'mallory''s DELETE of alice''s doc affects zero rows');

-- A WITH CHECK violation DOES raise 42501.
select throws_ok(
  $$ insert into public.documents (owner_id, org_id, body)
     values ('11111111-1111-1111-1111-111111111111','00000000-0000-0000-0000-0000000000aa','forged') $$,
  '42501', null, 'mallory cannot INSERT a row owned by alice');
select throws_ok(
  $$ update public.documents set owner_id = '11111111-1111-1111-1111-111111111111'
     where id = 'bbbbbbbb-0000-0000-0000-000000000002' $$,
  '42501', null, 'mallory cannot transfer her document to alice');

select set_config('request.jwt.claims', null, true);
set local role anon;
select is_empty($$ select * from public.documents $$, 'anon sees no documents');

select * from finish();
rollback;
```

```bash
supabase start
supabase test db
```

### What makes this suite honest, line by line

1. **`set local role authenticated` + `set_config('request.jwt.claims', …, true)`** is how Supabase documents switching identity in SQL. `is_local = true` scopes it to the transaction, so the closing `rollback` cleans up and tests cannot bleed into each other.
2. **`force row level security` plus the `relforcerowsecurity` assertion.** Without `force`, the whole suite runs as the exempt table owner and passes on a table with no policies at all.
3. **Row counts for UPDATE/DELETE, `throws_ok` for INSERT and ownership transfer.** This is the `USING`-is-silent split. Get it backwards and the suite is decorative.
4. **All four verbs.** A `for select` policy alone is the most common generated shape; INSERT, UPDATE and DELETE are then governed by grants nobody revoked.
5. **A positive case (`results_eq`) next to every negative case.** Without it, "deny everything" — a typo'd table name, a broken migration, a policy that references a null column — passes the entire suite.
6. **`table_privs_are(... 'anon', array[]::text[] ...)`** asserts the grant was actually revoked, not merely papered over with a policy.
7. **The anonymous lane at the end.** `set local role anon` with the claims cleared is the exact identity of someone holding only your publishable key.

Optional wrapper: `basejump-supabase_test_helpers` (installed via `dbdev`; version 0.0.6 in Supabase's docs) provides `tests.create_supabase_user()`, `tests.authenticate_as()`, `tests.get_supabase_uid()`, `tests.clear_authentication()` and a pgTAP-compliant `tests.rls_enabled()`. The raw `set_config` pattern above is what Supabase documents directly and is the safer thing to teach — the helper package's internals were not verified.

### The three audit queries to run alongside the suite

```sql
-- Every public table with RLS off, or RLS on but zero policies:
select c.relname,
       c.relrowsecurity      as rls_enabled,
       c.relforcerowsecurity as rls_forced,
       (select count(*) from pg_policy p where p.polrelid = c.oid) as policies
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
order by c.relrowsecurity, policies;

-- Every table where anon or authenticated still holds a grant:
select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public' and grantee in ('anon','authenticated')
order by table_name;

-- Tables with SELECT-only policies (the INSERT/UPDATE/DELETE hole):
select polrelid::regclass as tbl, array_agg(distinct polcmd) as commands
from pg_policy group by 1 having not (array_agg(distinct polcmd) @> array['a','w','d']::"char"[]);
```

**False positive to avoid reporting:** the mere existence of `anon` and `authenticated` in `pg_policies` is the normal Supabase model, not a finding (`NOTVULN-08`). The findings are `qual = true`, `'anon' = any(roles)` on non-public data, and grants that were never revoked. A policy scoped `to authenticated` becomes a finding only when signup is open — because `authenticated` means "anyone who completed a signup form," not "a customer."

---

## 6. Green pgTAP, leaking app: the service-role route your SQL tests cannot reach

This is `AUTHZ-08`, and it is the most important paragraph in this file.

**pgTAP proves the database refuses the query. It says nothing about the one route handler an agent switched to the service-role client to fix a "row not found" error.** That key carries `BYPASSRLS`, so on that path every policy you just proved is irrelevant. Your SQL tests cannot reach it — they test the database, and the leak is in the application.

Why it happens: when RLS blocks a legitimate query, the fastest fix an agent offers is "use the service role key on the server for this route." That silently converts an RLS-protected surface into a hand-written-authorization surface, and the hand-written authorization is usually just the authN check.

**Vulnerable:**

```ts
// app/api/documents/[id]/route.ts
import { createClient } from '@supabase/supabase-js'
const admin = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!)

export async function GET(_req: Request, ctx: RouteContext<'/api/documents/[id]'>) {
  const { id } = await ctx.params
  const { data } = await admin.from('documents').select('*').eq('id', id).single() // RLS bypassed
  return Response.json(data)
}
```

**Fixed — authenticate with the *user* client, then re-add by hand the predicate the admin client discarded:**

```ts
import 'server-only'
import { createServerClient } from '@supabase/ssr'
import { createClient } from '@supabase/supabase-js'

const admin = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { persistSession: false } })

export async function getDocument(id: string) {
  const supa = createServerClient(/* cookie-bound user client */)
  const { data: { user } } = await supa.auth.getUser()   // getUser(), never getSession()
  if (!user) throw new ForbiddenError()
  const { data } = await admin
    .from('documents').select('id,body,owner_id')
    .eq('id', id).eq('owner_id', user.id)                // <- the predicate RLS would have applied
    .maybeSingle()
  if (!data) throw new NotFoundError()
  return { id: data.id, body: data.body }                // minimal DTO
}
```

**The HTTP-level test nobody writes — this is how the gap gets closed:**

```ts
// tests/rls-through-http.spec.ts — vitest
import { createClient } from '@supabase/supabase-js'
import { expect, test, beforeAll } from 'vitest'

const URL = process.env.SUPABASE_URL!, ANON = process.env.SUPABASE_ANON_KEY!
const asUser = async (email: string, password: string) => {
  const c = createClient(URL, ANON, { auth: { persistSession: false } })
  const { error } = await c.auth.signInWithPassword({ email, password })
  if (error) throw error
  return c
}

let alice: Awaited<ReturnType<typeof asUser>>, mallory: typeof alice, aliceDocId: string

beforeAll(async () => {
  alice   = await asUser('alice@test.local',   process.env.PW_A!)
  mallory = await asUser('mallory@test.local', process.env.PW_B!)
  const { data } = await alice.from('documents').insert({ body: 'alice secret' }).select().single()
  aliceDocId = data!.id
})

test('PostgREST: mallory cannot read alice row', async () => {
  const { data } = await mallory.from('documents').select('*').eq('id', aliceDocId)
  expect(data).toEqual([])                       // RLS filter: empty, not an error
})

test('PostgREST: anon cannot read the table at all', async () => {
  const anon = createClient(URL, ANON)
  const { data, error } = await anon.from('documents').select('*')
  // Grants revoked => PostgREST surfaces 42501. An empty array with error === null means
  // the grant is still there and only RLS is filtering — weaker, and worth failing on.
  expect(error?.code, 'expected permission denied (42501)').toBe('42501')
  expect(data).toBeNull()
})

test('YOUR ROUTE: mallory cannot read alice row through the app', async () => {
  const { data: { session } } = await mallory.auth.getSession()
  const res = await fetch(`${process.env.BASE_URL}/api/documents/${aliceDocId}`, {
    headers: { authorization: `Bearer ${session!.access_token}` },
  })
  expect([401, 403, 404]).toContain(res.status)  // fails loudly if the route uses service_role
})
```

**Run the third assertion for *every* route that touches a tenant-scoped table.** That is the entire closure of this gap: pgTAP covers the database, and one HTTP assertion per route covers everything holding a key the database trusts.

Find the routes that need it:

```bash
# Which modules construct a service-role client?
grep -rn "SERVICE_ROLE\|sb_secret_\|SUPABASE_SERVICE" app/ src/ lib/ --include=*.ts --include=*.tsx

# Do those modules import 'server-only'? (files listed here do NOT)
grep -rLn "server-only" $(grep -rl "SERVICE_ROLE" app/ src/ lib/ 2>/dev/null) 2>/dev/null

# Ship-blocker: the key must never appear in the build output.
grep -roE "eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|sb_secret_[A-Za-z0-9_-]+" .next/static/ dist/ build/ 2>/dev/null
```

**Trap that bites mid-migration, in Supabase's own words:** *"A Service Key bypasses RLS only when the request carries no user access token. If the request carries one, it runs under the RLS policies of that signed-in user."* So admin, export and cron routes that forward the user's `Authorization` header silently become user-scoped and start returning empty — which reads as a bug, gets "fixed" by loosening a policy, and the loosened policy is the real vulnerability. `[CONFIRMED]`

**And one thing that is not proof:** "the app still works after I enabled RLS" is a false pass whenever a server route holds the service key. Pages render because that route has `BYPASSRLS`, so nothing about the actual attack path changed. Verify migrations **from outside the app with the publishable key**, never by clicking around. `[CONFIRMED]`

---

## 7. Firebase rules: the emulator, `rules-unit-testing`, and the coverage report

In a client-SDK Firebase app the rules are the *only* authorization layer — the browser talks to Firestore directly, so a wrong rule is a public database. Two properties make hand-inspection unreliable:

- **Rules are not filters.** If a client issues a query broader than the rules allow, the *entire query* fails rather than returning the allowed subset. A developer "testing" by loading the app sees a failure and loosens the rule until the app works — usually to `allow read: if request.auth != null`, which grants every signed-in user the whole collection.
- **`get()`/`exists()` lookups inside rules are quota-limited** — 10 for single-document and query requests, 20 for multi-document reads, transactions and batched writes. Ownership checks that walk a membership document hit the limit under load and then get "fixed" by removing them.

**The scaffold to grep for first:**

```bash
grep -n "if request.auth != null" firestore.rules storage.rules 2>/dev/null
```

**Fixed rules — deny by default, last:**

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function signedIn() { return request.auth != null; }
    function isOwner(uid) { return signedIn() && request.auth.uid == uid; }

    match /users/{uid} {
      allow get: if isOwner(uid);
      allow list: if false;                                   // no collection enumeration
      allow create: if isOwner(uid) && request.resource.data.keys().hasOnly(['name','createdAt']);
      allow update: if isOwner(uid)
                    && request.resource.data.diff(resource.data).affectedKeys()
                         .hasOnly(['name']);                  // role/credits immutable client-side
      allow delete: if false;
    }
    match /orgs/{orgId}/docs/{docId} {
      allow read, write: if signedIn()
        && exists(/databases/$(database)/documents/orgs/$(orgId)/members/$(request.auth.uid));
    }
    match /{document=**} { allow read, write: if false; }     // default deny, last
  }
}
```

**The test file:**

```ts
// test/firestore.spec.ts — run with: firebase emulators:exec "npm test"
import { readFileSync } from 'node:fs'
import { beforeAll, afterAll, beforeEach, describe, test } from 'vitest'
import { initializeTestEnvironment, assertSucceeds, assertFails, type RulesTestEnvironment }
  from '@firebase/rules-unit-testing'
import { doc, getDoc, setDoc, updateDoc, deleteDoc, collection, getDocs } from 'firebase/firestore'

let env: RulesTestEnvironment
const PROJECT_ID = 'demo-authz'      // "demo-" prefix => emulator never touches a real project

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT_ID,
    firestore: { rules: readFileSync('firestore.rules', 'utf8') },
  })
})
afterAll(async () => { await env.cleanup() })
beforeEach(async () => {
  await env.clearFirestore()
  // Seed as if rules were off — the ONLY sanctioned way to create fixtures you could not create legally.
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore()
    await setDoc(doc(db, 'users/alice'), { name: 'Alice', role: 'member' })
    await setDoc(doc(db, 'orgs/acme/members/alice'), { since: 1 })
    await setDoc(doc(db, 'orgs/acme/docs/d1'), { body: 'acme secret' })
  })
})

describe('users/{uid}', () => {
  test('owner reads own profile', async () =>
    assertSucceeds(getDoc(doc(env.authenticatedContext('alice').firestore(), 'users/alice'))))
  test('DENY other user reads profile', async () =>
    assertFails(getDoc(doc(env.authenticatedContext('mallory').firestore(), 'users/alice'))))
  test('DENY anonymous reads profile', async () =>
    assertFails(getDoc(doc(env.unauthenticatedContext().firestore(), 'users/alice'))))
  test('DENY collection enumeration', async () =>
    assertFails(getDocs(collection(env.authenticatedContext('alice').firestore(), 'users'))))
  test('DENY self-promotion via update (BOPLA)', async () => {
    const db = env.authenticatedContext('alice').firestore()
    await assertFails(updateDoc(doc(db, 'users/alice'), { role: 'admin' }))
    await assertSucceeds(updateDoc(doc(db, 'users/alice'), { name: 'Alice B' }))
  })
  test('DENY delete', async () =>
    assertFails(deleteDoc(doc(env.authenticatedContext('alice').firestore(), 'users/alice'))))
})

describe('orgs/{orgId}/docs', () => {
  test('member reads org doc', async () =>
    assertSucceeds(getDoc(doc(env.authenticatedContext('alice').firestore(), 'orgs/acme/docs/d1'))))
  test('DENY non-member reads org doc (cross-tenant)', async () =>
    assertFails(getDoc(doc(env.authenticatedContext('mallory').firestore(), 'orgs/acme/docs/d1'))))
  test('DENY forged custom claim — rule checks the membership doc, not the claim', async () =>
    assertFails(getDoc(doc(env.authenticatedContext('mallory', { org: 'acme' }).firestore(),
      'orgs/acme/docs/d1'))))
  test('DENY non-member WRITE to org doc', async () =>
    assertFails(setDoc(doc(env.authenticatedContext('mallory').firestore(), 'orgs/acme/docs/d1'),
      { body: 'pwned' })))
})
```

```bash
npm i -D @firebase/rules-unit-testing firebase vitest
npx firebase emulators:exec "npx vitest run"
```

`@firebase/rules-unit-testing` is at **5.0.2**, requires **Node ≥ 20**, and peer-depends on `firebase ^12.0.0` (verified against the npm registry API). `authenticatedContext(uid, tokenOptions)` injects custom claims, `unauthenticatedContext()` gives the anonymous lane, and `withSecurityRulesDisabled()` is the sanctioned escape hatch for fixtures. `firebase emulators:exec` starts the emulators, runs the suite, and shuts them down.

### The coverage report — the strongest machine-generated "prove the fix" artifact in this whole skill

While the emulator is running, the Firestore emulator serves a **rules coverage report** showing which rule expressions your tests actually exercised:

```
http://<host>:<port>/emulator/v1/projects/<projectId>:ruleCoverage.html
```

(Realtime Database uses `http://<host>:<port>/.inspect/coverage?ns=<databaseName>`.)

```bash
# Start the emulator in one terminal, run tests in another, then fetch the report before shutdown:
npx firebase emulators:start &
npx vitest run
curl -s "http://127.0.0.1:8080/emulator/v1/projects/demo-authz:ruleCoverage.html" > rules-coverage.html
open rules-coverage.html
```

**Any rule line with zero coverage is a rule no test proves anything about.** Screenshot it or keep the HTML; that is your evidence artifact.

**The health metric:**

```bash
grep -c "assertFails"    test/*.spec.ts    # must exceed…
grep -c "assertSucceeds" test/*.spec.ts    # …this
```

**False positive to avoid:** the Firebase web config in the bundle — `apiKey`, `authDomain`, `projectId`, `appId` — is not a leaked secret (`NOTVULN-02`). Firebase's docs state these keys "do not need to be treated as secrets" and "only *identify* your Firebase project and app." The control that must exist is Security Rules plus App Check. Reporting the config as an exposed key while test-mode rules are live inverts the priority and burns your credibility. **The genuine trap wearing the same costume:** a Gemini Developer API key has the identical `AIza` prefix and Firebase's docs say it *"should never be included in your code or configuration files."* Check what the key is restricted to in Google Cloud, not the prefix.

---

## 8. Fail-open authorization and cache-layer leaks

Two ways a *passing* authorization test still describes a vulnerable app.

### 8a. Fail-open — the check that returns "allowed" when the config breaks (`AUTHZ-09`)

Auth.js / `next-auth` v5 beta returns a truthy **error object** rather than `null` from `auth()` on a server misconfiguration — a missing `AUTH_SECRET`, a provider missing `issuer`. The documented pattern `const isLoggedIn = !!auth` therefore evaluates **true for every request**, authenticated or not. GHSA-8fpg-xm3f-6cx3 / **CVE-2026-73421**, CVSS **9.1**, CWE-285 + **CWE-636 (Failing Open)**, affects v5.0.0-beta.0 through beta.31, fixed in **beta.32**. `[CONFIRMED]`

This generalises far past one library: any `try { await check() } catch { /* allow */ }`, any `if (policyService.unavailable) return true`, any feature flag that defaults permissive. Agents write defensive `try/catch` around auth calls to stop crashes — and "the page 500s" is a bug the founder reports, while "the page renders for logged-out users" is one they never see.

```bash
grep -rnE "!!\s*(req\.)?auth\b|!!session\b|if\s*\(\s*(req\.)?auth\s*\)" app/ src/ middleware.ts proxy.ts 2>/dev/null
grep -rnB2 -A2 "catch" app/ src/ lib/ --include=*.ts | grep -iE "auth|session|permission|authoriz"
npm ls next-auth    # must be >= 5.0.0-beta.32 if on v5
```

> Grep `proxy.ts` as well as `middleware.ts`: as of **Next.js 16.0.0** the convention was renamed `middleware.ts` → `proxy.ts`, so a middleware-only grep misses newer apps entirely.

**Fix — require a concrete identity, and in the DAL catch and *throw*, never allow:**

```ts
// middleware.ts / proxy.ts
export default auth((req) => {
  const isLoggedIn = !!req.auth?.user?.id   // a concrete identity, not object existence
  if (!isLoggedIn) return NextResponse.redirect(new URL('/login', req.url))
})

// lib/auth-guard.ts — the server-only DAL entry point
import 'server-only'
export async function requireUser() {
  let session
  try { session = await auth() } catch { throw new ForbiddenError('auth unavailable') } // never allow
  const id = session?.user?.id
  if (typeof id !== 'string' || id.length === 0) throw new ForbiddenError()
  return id
}
```

**Prove it — a CI leg that boots the app with `AUTH_SECRET` deliberately unset and asserts non-200:**

```yaml
# .github/workflows/authz.yml — add this job alongside `authz`
  fail-open:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
      - run: npm run build
      - name: boot with AUTH_SECRET unset
        run: |
          env -u AUTH_SECRET -u NEXTAUTH_SECRET npm start &
          npx wait-on http://localhost:3000 --timeout 60000
      - name: a protected route must NOT return 200
        run: |
          code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/api/me)
          echo "got $code"
          test "$code" != "200"
```

### 8b. Cache-layer leaks (`AUTHZ-11`)

Your authorization tests pass against the handler. Then a cache in front of it serves user A's authorized response to user B and the handler is never reached.

**CVE-2026-61836** (GHSA-c6w9-5g5j-jh2p, directus < 12.0.0, CVSS 8.6, CWE-524 + CWE-639) is the reference case: the cache key was built from `version`, `path`, `query` and `accountability.user` but **omitted** `share`, `role`, `roles`, `admin`, `app` and `policies`. Share tokens carry no `id` claim, so `user` resolved to `null` — the same value as an anonymous request — and *"two different shares (or an anonymous request and a share token) requesting the same URL with the same query produce identical cache keys."* Exposure persisted for `CACHE_TTL` and survived restarts on Redis. `[CONFIRMED]`

In a vibe-coded app this arrives as "make it faster," which produces `export const revalidate = 60` or a CDN rule on an authenticated route.

**Vulnerable:**

```ts
// app/api/documents/route.ts — per-user data, cached globally
export const revalidate = 300
export async function GET() { return Response.json(await listDocsForCurrentUser()) }
```

**Fixed:**

```ts
export const dynamic = 'force-dynamic'   // never statically cache a per-user response
export async function GET() {
  const docs = await listDocsForCurrentUser()
  return Response.json(docs, {
    headers: { 'cache-control': 'private, no-store', vary: 'Cookie, Authorization' },
  })
}
```

**🟡 Black-box check (logged on your own infrastructure — run against staging or your own production):**

```bash
APP=https://staging.example.com

# 1. Static: authenticated routes carrying any cache directive.
for f in $(grep -rl "revalidate\|unstable_cache\|force-static\|s-maxage" app/ src/app/ 2>/dev/null); do
  grep -qE "auth\(|getUser\(|cookies\(" "$f" && echo "CACHED + AUTHENTICATED: $f"
done

# 2. Dynamic: same URL, two identities, compare bodies AND cache headers.
curl -si -H "Cookie: $COOKIE_A" "$APP/api/documents" | tee /tmp/a.txt | grep -iE '^(cache-control|vary|age|x-vercel-cache|cf-cache-status):'
curl -si -H "Cookie: $COOKIE_B" "$APP/api/documents" | tee /tmp/b.txt | grep -iE '^(cache-control|vary|age|x-vercel-cache|cf-cache-status):'
diff <(tail -n +2 /tmp/a.txt) <(tail -n +2 /tmp/b.txt) && echo "IDENTICAL BODIES FOR TWO USERS — cache leak"

# 3. Anonymous: must not be 200. Run it TWICE, and again after an authenticated fetch.
curl -si "$APP/api/documents" | head -1
curl -si "$APP/api/documents" | head -1
```

**Run the anonymous fetch twice in a row and again after an authenticated fetch** — the leak often only appears once the entry is populated, so a single clean anonymous request proves nothing.

| Signal | True positive | False positive |
|---|---|---|
| Two identities, byte-identical bodies | Cache serving one user's data to another | Both users genuinely see the same public payload (a pricing table, a feature list). Confirm the body contains user-specific data first. |
| `x-vercel-cache: HIT` / `cf-cache-status: HIT` on an authenticated route | Response was served without reaching your handler | A `HIT` on a static asset or an intentionally public endpoint. Check the path. |
| `age:` header present and rising | Shared cache entry | Same caveat — public content is allowed to age. |
| Anonymous request returns 200 | The route has no guard, or the cache is answering for it | The route is meant to be public. Compare against your matrix, not your intuition. |

---

## 9. The 20-minute Autorize drill (Burp Community — free)

Everything above tests the endpoints you thought to list. This drill tests **every endpoint you actually touch**, including the ones you forgot exist. Autorize (Barak Tawily / AppSec Labs, on the PortSwigger BApp Store) **works in Burp Suite Community Edition**, so it costs nothing. It is the realistic ceiling for a non-expert, and it is what produces the evidence file behind a public "try to hack this" claim.

The mechanic: you give Autorize a *low-privileged* user's credentials, then browse the app as a *high-privileged* user. Autorize passively captures each request, silently replays it with the low-privileged credentials and again with none, and colour-codes the comparison.

### Setup (10 minutes, once)

```
1. Install Burp Suite Community Edition (free).
     macOS:   brew install --cask burp-suite
     Windows/Linux: download the Community installer from portswigger.net
2. Install Jython (Autorize is a Python extension):
     download jython-standalone-2.7.x.jar from jython.org
     Burp → Extensions → Options → Python Environment → set the JAR path
3. Burp → Extensions → BApp Store → search "Autorize" → Install
```

### The drill (10 minutes, every time)

```
4. In a normal browser, log in as USER B — the LOW-privilege account, in a DIFFERENT tenant.
   DevTools → Network → click any request → copy the whole `Cookie` request header.
   (If your app uses bearer tokens, copy the `Authorization: Bearer …` header instead.)
5. Burp → Autorize tab → paste that header into the header box (one header per line).
   Tick "Check unauthenticated"  → this adds the anonymous lane for free.
   Set the dropdown to "Autorize is on".
6. Burp → Proxy → Intercept is off → "Open browser" (Burp's embedded Chromium).
   Log in there as USER A — the HIGH-privilege account, the other tenant.
7. Now USE EVERY FEATURE as USER A. Deliberately, one at a time:
   create, read, edit, delete, list, search, upload, download, export,
   invite a teammate, change a role, change plan / billing, open the admin panel,
   open every settings page, trigger every background action.
   Autorize is recording and replaying the whole time.
8. Read the Autorize table. Three colours:
     RED    "Bypassed!"        — authorization was NOT enforced. This is a finding. File it.
     GREEN  "Enforced!"        — access control held.
     YELLOW "Is enforced???"   — undetermined. YOU must resolve every one of these by hand.
9. Resolve each yellow with an Enforcement Detector: select the request, right-click →
   set a body regex or content-length rule that distinguishes "you got the data"
   from "you were refused", then re-run. Yellow left unresolved is not a pass.
10. Right-click the table → Save. That file is your evidence artifact.
```

### How to read the result honestly

| Autorize says | What it means | The trap |
|---|---|---|
| **Red — Bypassed!** | User B's replay got the same authorized response as User A | Almost always real. Confirm the response body actually contains A's data before filing — a shared static asset can produce a red row. |
| **Green — Enforced!** | The replay was refused | Green on a request you never actually made proves nothing. Green is only meaningful for features you exercised in step 7. |
| **Yellow — Is enforced???** | Autorize could not tell | **This is where findings hide.** A 200 with a generic error page looks like a 200. Never report a run with unresolved yellows as clean. |
| An empty or short table | — | You browsed with the wrong browser, or Autorize was off. The table should have one row per request you made. If it has 12 rows after a full feature sweep, the capture failed. |

**Before a public "try to hack this" claim, the bar is: a saved Autorize table with zero red and zero unresolved yellow, over a session that touched every feature.** Anything less and you are inviting strangers to test something you have not tested yourself.

### The other two tools, and what they are for

- **AuthMatrix** (also Burp, also Jython 2.7.0+) is the interactive matrix twin: define users, assign roles, add captured requests as rows, tick which roles *should* be authorized per row, set a success/failure regex, hit Run. Green = correct, red = potential vulnerability, blue = probable false positive (expired credential or bad regex). Use it to *produce* the matrix you will then codify in §3.
- **ZAP's Access Control Testing add-on** defines Users plus per-node Allowed/Denied/Unknown rules that inherit down the URL tree, and raises alerts **10101 Improper Authentication** and **10102 Improper Authorization**. Documented constraint: *"Access control testing is not allowed in `Safe` mode nor `Protected` if the context is not in scope."* **Whether it runs headlessly in CI via ZAP's Automation Framework is `[REPORTED — unverified]`** — the automation-job documentation could not be confirmed. Treat ZAP access control as a **desktop-GUI technique** until you verify it yourself, and do not wire it into a CI gate on the strength of this file.

---

## 10. Remediation verification — the artifact that proves each finding is closed

**A finding is not closed by a diff. It is closed by an artifact that would go red again if the fix were reverted.** Every authorization finding you report must name its closing artifact from this table, and the report must include the artifact — the run log, the test name, the screenshot, the saved table.

| Finding (stable ID) | Closing artifact | Produced by |
|---|---|---|
| `AUTHZ-01` IDOR / BOLA on a route | A failing-then-passing **deny row** in the Playwright matrix naming the exact method + path + attacker role | `npx playwright test tests/authz.spec.ts` |
| `AUTHZ-02` missing function-level authz (whole endpoint, or one method of one endpoint) | The endpoint shows as **covered** by route-coverage, **plus** an anon deny row and an other-tenant deny row for each HTTP method it exports | `node scripts/route-coverage.mjs` exit 0 + the matrix |
| `AUTHZ-03` middleware- / layout- / UI-only guard | A deny row that calls the API **directly**, bypassing the UI entirely | `tests/authz.spec.ts` (uses `APIRequestContext`, never a page click) |
| `AUTHZ-04` unguarded Server Action | The action name appears in route-coverage as covered, plus a deny assertion invoking it as another tenant | `node scripts/route-coverage.mjs` + matrix row |
| `AUTHZ-05` mass assignment / BOPLA | The "DENY privilege field via legitimate update" test: PATCH with `role`/`credits` in the body, then re-read and assert the fields did **not** change | `npx playwright test` |
| `AUTHZ-06` cross-tenant read/write generally | `authz-diff.ts` run log showing `ok` for every case as other-tenant **and** anonymous, with at least one owner baseline < 400 | `npx tsx authz-diff.ts` exit 0 |
| `AUTHZ-08` service-role route bypassing RLS | The **third** test in `rls-through-http.spec.ts` — mallory's real JWT against your real route returning 401/403/404 — present for every route touching a tenant-scoped table | `vitest run tests/rls-through-http.spec.ts` |
| `AUTHZ-09` fail-open auth | A CI run against a deliberately misconfigured env (`AUTH_SECRET` unset) asserting non-200 on a protected route | the `fail-open` workflow job |
| `AUTHZ-11` route that missed the guard | route-coverage exit 0 with a non-trivial `surface:` count | `node scripts/route-coverage.mjs` |
| `AUTHZ-11` cache-layer leak | Two `curl -si` transcripts (two identities plus anonymous, anonymous run twice) showing different bodies, `Cache-Control: private, no-store`, and correct `Vary` | shell |
| `BAAS-01` RLS policy wrong or absent | pgTAP file with four-verb **positive and negative** cases green, **plus** the `pg_class` / `pg_policy` / `role_table_grants` audit queries returning the expected shape | `supabase test db` |
| `BAAS-01` missing RLS on a live table | An **external** `curl` with only the publishable key returning `42501` or an empty set — on both GET **and** PATCH | `curl` |
| Firestore / Storage rule wrong | An `assertFails` test for the exact denied operation **and** a rules **coverage report** showing that rule line is exercised | `firebase emulators:exec` + `:ruleCoverage.html` |
| Whole-app assurance before a public "hack this" claim | Saved Autorize table with **zero red and zero unresolved yellow** over a session that touched every feature | Burp Community + Autorize |

### Three rules that keep the artifacts honest

1. **Reproduce before you fix.** Write the test, watch it **fail** against the vulnerable build, then fix, then watch it pass. A test authored after the fix frequently asserts the wrong thing and passes vacuously. If you cannot make the test fail on the old code, you have not understood the finding.
2. **Pair every deny test with an allow test on the same endpoint.** Otherwise "deny everything" — a broken deploy, a typo'd path, a route that 404s for everyone — passes your entire security suite with flying colours.
3. **The artifact must be attached to the report, not described in it.** "I added a test" is not evidence. The test name, the run output, the coverage screenshot, the saved Autorize table: those are evidence. This is the difference between a security claim and a security posture, and it is the whole basis on which a public hack challenge is defensible.

### Do not close a finding on any of these

| Not proof | Why |
|---|---|
| "The scanner is green" | §1. The scanner was never asked this question. |
| "The app still works after I enabled RLS" | A service-role route has `BYPASSRLS`; the pages render and the attack path is unchanged. |
| "I checked in the SQL editor" | You ran as the table owner. RLS was bypassed. |
| "I rotated the anon key" | The anon key was never secret (`NOTVULN-01`). If rows came back, the finding was `BAAS-01` and it is still open. |
| "UUIDs are unguessable" | UUIDs stop enumeration, not leakage. OWASP: *"even with complex identifiers, access control checks are essential."* One leaked id is full access. |
| "The button is only shown to admins" | Every referenced `'use server'` function is an addressable HTTP endpoint (`AUTHZ-04`). |
| "The agent said it added the check" | Verify by running the deny test. An agent will delete an authorization check to make a failing test pass. |
