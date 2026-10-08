#!/usr/bin/env bash
#
# audit-bundle.sh — audit the BUILT artifact, which is what the attacker reads.
# Part of the "never-get-hacked" skill.
#
#   Usage:  ./audit-bundle.sh [build-dir]
#           ./audit-bundle.sh                 # auto-detects .next / dist / build / out
#           ./audit-bundle.sh dist --quiet    # findings only, no explanations
#           ./audit-bundle.sh dist --brief    # alias of --quiet
#
#   --quiet / --brief: ONE LINE PER FINDING. Same checks, same finding IDs, same
#   exit code — nothing is skipped. The long-form explanations, the NOTVULN
#   walkthroughs and the closing "what this cannot see" list collapse to one line
#   each; the Supabase RLS enumeration curl is kept because it is the highest-value
#   command in the audit. Re-run WITHOUT the flag to get every word and every curl
#   back. NOTVULN blocks keep their required-control clause in both modes: a bare
#   ID would let a publishable key be re-reported as a leak.
#
#   Read-only. Makes no network calls. Never writes to the build directory.
#   Prints curl commands for you to run yourself; it does not run them.
#
#   Exit codes:
#     0  no CRITICAL finding
#     2  at least one CRITICAL finding (a real secret in browser-reachable output)
#     3  no build output found — nothing was audited
#
#   Covers: SECRET-01 (secret behind a public env prefix, verified in the artifact),
#           SECRET-04 (production source maps), BAAS-04 (service_role key in the
#           bundle), SECRET-08 (secret serialized into prerendered HTML).
#   Deliberately does NOT flag: NOTVULN-01/02/03/04 (publishable keys). Those get
#           their own section with the control you must verify instead.
#
set -uo pipefail

VERSION="1.0"
QUIET=0
BUILD_DIR=""

for arg in "$@"; do
  case "$arg" in
    -q|--quiet|--brief) QUIET=1 ;;
    -h|--help)
      sed -n '3,31p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) printf 'unknown option: %s\n' "$arg" >&2; exit 64 ;;
    *)  BUILD_DIR="$arg" ;;
  esac
done

# ---------------------------------------------------------------- output helpers
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
  C_BLU=$'\033[36m'; C_DIM=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_RED=""; C_YEL=""; C_GRN=""; C_BLU=""; C_DIM=""; C_B=""; C_0=""
fi

CRIT_COUNT=0
WARN_COUNT=0

hdr()  { printf '\n%s%s== %s%s\n' "$C_B" "$C_BLU" "$*" "$C_0"; }
crit() { CRIT_COUNT=$((CRIT_COUNT+1)); printf '%s%sCRITICAL%s %s\n' "$C_B" "$C_RED" "$C_0" "$*"; }
warn() { WARN_COUNT=$((WARN_COUNT+1)); printf '%s%sWARN%s     %s\n' "$C_B" "$C_YEL" "$C_0" "$*"; }
ok()   { printf '%sok%s       %s\n' "$C_GRN" "$C_0" "$*"; }
info() { printf '         %s\n' "$*"; }
note() { [ "$QUIET" -eq 1 ] || printf '%s         %s%s\n' "$C_DIM" "$*" "$C_0"; }
# qinfo: prose that --quiet drops. Findings and required-control clauses never
# use it. qcmd: a curl line --quiet drops.
qinfo() { [ "$QUIET" -eq 1 ] || printf '         %s\n' "$*"; }
qcmd()  { [ "$QUIET" -eq 1 ] || printf '           %s$ %s%s\n' "$C_B" "$*" "$C_0"; }
cmd()  { printf '           %s$ %s%s\n' "$C_B" "$*" "$C_0"; }

TMPD="$(mktemp -d 2>/dev/null || mktemp -d -t nghbundle)"
cleanup() { rm -rf "$TMPD"; }
trap cleanup EXIT INT TERM

# ------------------------------------------------------------- base64url decode
# macOS base64 wants -D, GNU wants -d. Try both, keep whichever produced output.
b64url_decode() {
  local d="$1" pad out
  d="$(printf '%s' "$d" | tr -- '-_' '+/')"
  pad=$(( ${#d} % 4 ))
  case "$pad" in
    2) d="${d}==" ;;
    3) d="${d}=" ;;
    1) d="${d}A==" ;;
  esac
  out="$(printf '%s' "$d" | base64 -d 2>/dev/null)"
  if [ -z "$out" ]; then
    out="$(printf '%s' "$d" | base64 -D 2>/dev/null)"
  fi
  printf '%s' "$out"
}

# Pull one string claim out of a flat JSON payload without requiring jq.
json_str() {
  local json="$1" key="$2"
  printf '%s' "$json" \
    | tr ',{}' '\n\n\n' \
    | grep -aoE "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" \
    | head -1 \
    | sed -E 's/.*:[[:space:]]*"([^"]*)"$/\1/'
}

redact() {
  local s="$1" n=${#1}
  if [ "$n" -le 20 ]; then printf '%s' "$s"
  else printf '%s...%s (%d chars)' "${s:0:12}" "${s: -4}" "$n"; fi
}

# ------------------------------------------------------- locate the build output
detect_pm() {
  if   [ -f bun.lockb ] || [ -f bun.lock ]; then printf 'bun run'
  elif [ -f pnpm-lock.yaml ];               then printf 'pnpm'
  elif [ -f yarn.lock ];                    then printf 'yarn'
  else printf 'npm run'; fi
}

no_build_help() {
  printf '\n%s%sNo build output found.%s Nothing was audited.\n\n' "$C_B" "$C_YEL" "$C_0"
  printf 'This script reads the artifact your users download. Source code is a different\n'
  printf 'audit (scripts/audit-repo.sh). A clean repo grep proves nothing about the bundle:\n'
  printf 'build tools inline NEXT_PUBLIC_/VITE_/REACT_APP_ values as string literals at\n'
  printf 'build time, so the leak only becomes visible after you build.\n\n'

  local pm; pm="$(detect_pm)"
  if [ -f package.json ]; then
    if grep -qE '"build"[[:space:]]*:' package.json 2>/dev/null; then
      printf 'Run this first, from %s:\n\n' "$(pwd)"
      cmd "$pm build"
      printf '\n'
    fi
    if   grep -q '"next"' package.json 2>/dev/null; then
      printf 'Detected Next.js. Output lands in %s.next/static%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 .next"
    elif grep -q '"vite"' package.json 2>/dev/null; then
      printf 'Detected Vite. Output lands in %sdist/%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 dist"
    elif grep -q '"react-scripts"' package.json 2>/dev/null; then
      printf 'Detected Create React App. Output lands in %sbuild/%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 build"
    elif grep -q '"astro"' package.json 2>/dev/null; then
      printf 'Detected Astro. Output lands in %sdist/%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 dist"
    elif grep -q '"nuxt"' package.json 2>/dev/null; then
      printf 'Detected Nuxt. Output lands in %s.output/public%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 .output/public"
    elif grep -q '"@sveltejs/kit"' package.json 2>/dev/null; then
      printf 'Detected SvelteKit. Client output lands in %s.svelte-kit/output/client%s. Then re-run:\n\n' "$C_B" "$C_0"
      cmd "$0 .svelte-kit/output/client"
    elif grep -q '"expo"' package.json 2>/dev/null; then
      printf 'Detected Expo. Export the web bundle first:\n\n'
      cmd "npx expo export --platform web"
      cmd "$0 dist"
    else
      printf 'Build, then point this script at the output directory:\n\n'
      cmd "$pm build && $0 <output-dir>"
    fi
  else
    printf 'No package.json here, so this is not a JS build root.\n'
    printf 'If your app is plain HTML/JS with no build step, %sthe source IS the artifact%s —\n' "$C_B" "$C_0"
    printf 'point this script at the directory you deploy:\n\n'
    cmd "$0 ./public"
    printf '\nIf your app is built by a hosting platform (Lovable, Bolt, v0, Netlify, Vercel),\n'
    printf 'download the deployed assets instead and audit those:\n\n'
    cmd "mkdir -p loot && cd loot"
    cmd "curl -s https://yourapp.example | grep -oE '/[^\"]+\\.js' | while read -r p; do curl -sO \"https://yourapp.example\$p\"; done"
    cmd "$0 ."
  fi
  printf '\n'
  exit 3
}

if [ -n "$BUILD_DIR" ]; then
  if [ ! -d "$BUILD_DIR" ]; then
    printf '%s%sNot a directory:%s %s\n' "$C_B" "$C_RED" "$C_0" "$BUILD_DIR"
    no_build_help
  fi
else
  for candidate in .next dist build out .output/public .svelte-kit/output/client; do
    if [ -d "$candidate" ]; then BUILD_DIR="$candidate"; break; fi
  done
  [ -n "$BUILD_DIR" ] || no_build_help
fi

BUILD_DIR="${BUILD_DIR%/}"

# Which subtrees are actually shipped to a browser.
# .next/server/** is server-only code and is NOT downloaded by users — grepping it
# produces confident-sounding false positives about keys that never left the box.
SCAN_PATHS=()
if [ -d "$BUILD_DIR/static" ] && [ "$(basename "$BUILD_DIR")" = ".next" ]; then
  SCAN_PATHS+=("$BUILD_DIR/static")
  BROWSER_NOTE="Scanned .next/static only. .next/server/** is server-only code that users never download; flagging a key there is a false positive."
else
  SCAN_PATHS+=("$BUILD_DIR")
  BROWSER_NOTE="Scanned the whole of $BUILD_DIR as browser-reachable."
fi

GREP_EXCL=(--exclude-dir=node_modules --exclude-dir=cache --exclude-dir=.git)

# Prerendered HTML and RSC/flight payloads are downloaded by the browser even when
# they live under a server/ path — SECRET-08 lands here, not in the JS chunks.
find "$BUILD_DIR" \( -name node_modules -o -name cache \) -prune -o \
     -type f \( -name '*.html' -o -name '*.rsc' -o -name '*.txt' \) -print \
     > "$TMPD/html-files" 2>/dev/null
HTML_COUNT=$(wc -l < "$TMPD/html-files" | tr -d ' ')

ASSET_COUNT=$(find "${SCAN_PATHS[@]}" \( -name node_modules -o -name cache \) -prune -o \
              -type f \( -name '*.js' -o -name '*.mjs' -o -name '*.cjs' -o -name '*.css' -o -name '*.json' \) -print 2>/dev/null | wc -l | tr -d ' ')

printf '%s%saudit-bundle.sh v%s%s  —  %s\n' "$C_B" "$C_BLU" "$VERSION" "$C_0" "$(date '+%Y-%m-%d %H:%M')"
printf 'build dir: %s%s%s   (%s browser assets, %s html/payload files)\n' "$C_B" "$BUILD_DIR" "$C_0" "$ASSET_COUNT" "$HTML_COUNT"
note "$BROWSER_NOTE"

# Staleness: a clean scan of a stale build is worthless.
if [ -d src ] || [ -d app ]; then
  NEWER=$(find src app -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' -o -name '*.vue' -o -name '*.svelte' \) \
          -newer "$BUILD_DIR" -print 2>/dev/null | head -1)
  if [ -n "$NEWER" ]; then
    warn "Build is older than your source (e.g. $NEWER). Rebuild before trusting a clean result."
  fi
fi

# =============================================================================
# 1. JWTs — decode the payload and triage on the ROLE CLAIM, never on the prefix
# =============================================================================
hdr "1. JWTs in the artifact — decoded, not pattern-matched"

JWT_RE='eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'
: > "$TMPD/jwts"
grep -rhaoE "$JWT_RE" "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null >> "$TMPD/jwts"
if [ -s "$TMPD/html-files" ]; then
  tr '\n' '\0' < "$TMPD/html-files" | xargs -0 grep -haoE "$JWT_RE" 2>/dev/null >> "$TMPD/jwts"
fi
sort -u "$TMPD/jwts" -o "$TMPD/jwts"

SUPA_REF=""
ANON_KEY=""

if [ ! -s "$TMPD/jwts" ]; then
  ok "No JWT-shaped strings in browser-reachable output."
else
  while IFS= read -r tok; do
    [ -n "$tok" ] || continue
    payload_b64="$(printf '%s' "$tok" | cut -d. -f2)"
    payload="$(b64url_decode "$payload_b64")"
    if [ -z "$payload" ] || ! printf '%s' "$payload" | grep -q '{'; then
      note "Undecodable JWT-shaped string, probably not a token: $(redact "$tok")"
      continue
    fi
    role="$(json_str "$payload" role)"
    iss="$(json_str "$payload" iss)"
    ref="$(json_str "$payload" ref)"

    case "$role" in
      service_role)
        crit "BAAS-04 — a service_role JWT is in the shipped bundle."
        info "token: $(redact "$tok")"
        info "payload: $payload"
        info "This key carries Postgres BYPASSRLS. Every RLS policy you wrote is irrelevant"
        info "to anyone holding it: full read, full write, full delete on every table."
        info "Do this now, in this order:"
        info "  1. Treat it as compromised. It has been public since your first deploy."
        info "  2. Rotate at Supabase (Settings -> API Keys), then explicitly REVOKE the old"
        info "     one. Supabase: 'if you do not Revoke the key, older keys will still be valid.'"
        info "  3. Delete the client-side call. A secret key cannot be made safe in a browser;"
        info "     move the call into an Edge Function or a server route."
        info "  4. Assume the data was read. Legacy anon/service_role keys can no longer be"
        info "     rotated in place — the path is migration to publishable/secret keys."
        ;;
      anon)
        ok "NOTVULN-01 — anon JWT (role=\"anon\"). Public by design. Not a finding."
        note "ref=${ref:-unknown} iss=${iss:-unknown}"
        note "Do NOT rotate this as remediation. See section 5 for the check that matters."
        [ -n "$ref" ] && SUPA_REF="$ref"
        ANON_KEY="$tok"
        ;;
      authenticated)
        warn "A JWT with role=\"authenticated\" is baked into the artifact."
        info "That is a real user session token compiled into a static file — every visitor"
        info "gets it, and it is replayable until it expires. Usually a hardcoded test login."
        info "token: $(redact "$tok")"
        ;;
      "")
        warn "JWT with no role claim baked into the artifact: $(redact "$tok")"
        info "payload: $payload"
        info "Read it yourself. A signed token shipped as a constant is a credential unless"
        info "you can name what it authorizes. If it is a session token, it is an incident."
        ;;
      *)
        warn "JWT with role=\"$role\" in the artifact: $(redact "$tok")"
        info "payload: $payload"
        info "Unrecognised role. Decide deliberately whether this grants anything."
        ;;
    esac
  done < "$TMPD/jwts"
fi

note ""
note "Why decode instead of grep: 'eyJ...' says nothing. The anon key and the"
note "service_role key are the same shape, the same length, and differ only in one"
note "claim inside the payload. Grepping for the literal string 'service_role' also"
note "misses nothing-and-everything: it hits harmless SDK source strings and misses"
note "the actual token. The role claim is the check."
note "Decode any token by hand with:"
[ "$QUIET" -eq 1 ] || cmd "printf '%s' '<the-eyJ-token>' | cut -d. -f2 | base64 -d 2>/dev/null | jq ."

# =============================================================================
# 2. Non-JWT secrets that are unambiguously secret
# =============================================================================
hdr "2. Provider secrets in browser-reachable output"

# Every pattern here is a credential no vendor considers safe to publish.
# Ambiguous prefixes (AIza, pk_, phc_) are deliberately NOT in this list; see section 5.
SECRET_PATTERNS='sb_secret_[A-Za-z0-9_-]{16,}|sk_live_[A-Za-z0-9]{16,}|rk_live_[A-Za-z0-9]{16,}|sk_test_[A-Za-z0-9]{16,}|rk_test_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{16,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|re_[A-Za-z0-9]{8}_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20,}|shpat_[a-fA-F0-9]{32}|npm_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----'

: > "$TMPD/secrets"
grep -rHaoE "$SECRET_PATTERNS" "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null >> "$TMPD/secrets"
if [ -s "$TMPD/html-files" ]; then
  tr '\n' '\0' < "$TMPD/html-files" | xargs -0 grep -HaoE "$SECRET_PATTERNS" 2>/dev/null >> "$TMPD/secrets"
fi
sort -u "$TMPD/secrets" -o "$TMPD/secrets"

if [ ! -s "$TMPD/secrets" ]; then
  ok "No provider secret patterns in browser-reachable output."
else
  crit "SECRET-01 — provider credentials are in the shipped artifact."
  while IFS= read -r line; do
    f="${line%%:*}"
    m="${line#*:}"
    info "$(redact "$m")   <- $f"
  done < "$TMPD/secrets"
  info ""
  info "What each one costs you, from the dossier:"
  info "  sk_live_ / rk_live_   Stripe secret key: refunds, customer PII dumps, payout changes."
  info "  sk_test_ / whsec_     test-mode key or webhook signing secret. Lower blast radius,"
  info "                        still a credential, and whsec_ lets anyone forge 'payment"
  info "                        succeeded' events at your endpoint."
  info "  sk-ant- / sk-proj-    LLM provider key. Sysdig documented a resale economy for"
  info "                        exactly this, up to ~\$46k/day of victim spend."
  info "  AKIA / ASIA           AWS access key. Comparitech's honeypot saw a leaked key found"
  info "                        and abused in ~1 minute, full recon in 4."
  info "  SG. / re_             SendGrid/Resend: mail sent FROM your verified domain, passing"
  info "                        your own SPF/DKIM/DMARC. Phishing your users, as you."
  info "  gh*_ / github_pat_    repo access, and therefore every other secret in the repo."
  info "  sb_secret_            Supabase secret key: BYPASSRLS, same as service_role."
  info "  PRIVATE KEY           signing or SSH material. Rotate, do not 'remove and redeploy'."
  info ""
  info "Order of operations — get this wrong and you do the work twice:"
  info "  1. Rotate at the provider FIRST. The key is already public."
  info "  2. Then remove it from the code and rebuild."
  info "  3. Deleting the commit is not remediation. GitHub, verbatim: 'Simply removing the"
  info "     secret from the codebase, pushing a new commit, or deleting and recreating the"
  info "     repository do not prevent the secret from being exploited.'"
  info "  4. Rotation without revocation is not rotation. Stripe keeps old keys valid up to"
  info "     7 days by design; Supabase requires an explicit Revoke after rotating."
  info "  5. Move the call server-side. There is no client-side mitigation — the vendors do"
  info "     not offer domain restriction on these key types, and obfuscation is not a control."
fi

note ""
note "True positive: a live credential string. False positive worth knowing about:"
note "the literal text 'service_role' or 'sk_live_' appearing inside a vendor SDK's own"
note "error message or type definition that got bundled. Those carry no key material —"
note "check whether an actual value follows the prefix before you panic. This scan"
note "requires 16+ characters after each prefix precisely to avoid that noise."

# =============================================================================
# 3. Bearer-secret API hosts called directly from the browser
# =============================================================================
hdr "3. Direct calls to APIs that only accept a secret"

HOST_RE='api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.stripe\.com|api\.resend\.com|api\.sendgrid\.com|api\.mailgun\.net|api\.twilio\.com|api\.replicate\.com|api\.elevenlabs\.io|api\.deepseek\.com|api\.groq\.com|api\.together\.xyz|api\.mistral\.ai'

: > "$TMPD/hosts"
grep -rhaoE "$HOST_RE" "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u >> "$TMPD/hosts"

if [ ! -s "$TMPD/hosts" ]; then
  ok "No bearer-secret API hosts referenced in browser code."
else
  warn "The browser bundle contains a URL for an API that authenticates with a secret:"
  while IFS= read -r h; do info "  https://$h"; done < "$TMPD/hosts"
  info ""
  info "True positive: your client calls this host directly, so the key must also be in"
  info "the bundle. Section 2 above tells you whether it was found. If section 2 was clean"
  info "the key may be assembled at runtime, split across variables, or base64'd — none of"
  info "which is a control. Open the file and read the fetch() call."
  info "False positive: the string is dead weight from a vendor SDK, an example in a"
  info "comment that survived minification, or a URL your SERVER uses that got bundled"
  info "into a shared module. Confirm by finding the actual fetch."
  info ""
  info "If it is real, there is no client-side fix. The architecture is the bug: the"
  info "moment your app calls an API that authenticates with a secret, client-only is over."
  info "You do not need a backend, you need one function. Minimal Supabase Edge Function:"
  info ""
  info "  // supabase/functions/ai/index.ts   (deploy: supabase functions deploy ai)"
  info "  import { createClient } from 'jsr:@supabase/supabase-js@2'"
  info ""
  info "  Deno.serve(async (req) => {"
  info "    // 1. Identity from the caller's JWT, never from the body."
  info "    const authHeader = req.headers.get('Authorization') ?? ''"
  info "    const supabase = createClient("
  info "      Deno.env.get('SUPABASE_URL')!,"
  info "      Deno.env.get('SUPABASE_PUBLISHABLE_KEY')!,   // NOT the secret key"
  info "      { global: { headers: { Authorization: authHeader } } },"
  info "    )"
  info "    const { data: { user } } = await supabase.auth.getUser()"
  info "    if (!user) return new Response('unauthorized', { status: 401 })"
  info ""
  info "    // 2. Bound the input server-side."
  info "    const { prompt } = await req.json()"
  info "    if (typeof prompt !== 'string' || prompt.length > 4000) {"
  info "      return new Response('bad request', { status: 400 })"
  info "    }"
  info ""
  info "    // 3. Meter, using the DB as the only shared state you have."
  info "    const { error: quotaErr } = await supabase.rpc('consume_credit', { n: 1 })"
  info "    if (quotaErr) return new Response('quota exceeded', { status: 429 })"
  info ""
  info "    // 4. Only now is the secret used, and it never leaves this process."
  info "    const r = await fetch('https://api.openai.com/v1/chat/completions', {"
  info "      method: 'POST',"
  info "      headers: {"
  info "        Authorization: \`Bearer \${Deno.env.get('OPENAI_API_KEY')}\`,"
  info "        'Content-Type': 'application/json',"
  info "      },"
  info "      body: JSON.stringify({"
  info "        model: 'gpt-4o-mini',"
  info "        max_tokens: 512,                            // hard output cap"
  info "        messages: [{ role: 'user', content: prompt }],"
  info "      }),"
  info "    })"
  info "    return new Response(r.body, { headers: { 'Content-Type': 'application/json' } })"
  info "  })"
  info ""
  info "Set the secret with:  supabase secrets set OPENAI_API_KEY=sk-..."
  info "Keep verify_jwt at its default (true) for user-facing functions. Read it carefully"
  info "though: verify_jwt checks the token is VALID, not that a user is behind it. On"
  info "legacy-key projects the anon key is itself a project-signed JWT, so"
  info "'Authorization: Bearer <ANON_KEY>' satisfies the gate. Always resolve the caller"
  info "in code (auth.getUser()) and fail closed, exactly as above."
fi

# =============================================================================
# 4. Source maps (SECRET-04)
# =============================================================================
hdr "4. Source maps in the deployable artifact"

find "$BUILD_DIR" \( -name node_modules -o -name cache \) -prune -o \
     -type f -name '*.map' -print > "$TMPD/maps" 2>/dev/null
MAP_COUNT=$(wc -l < "$TMPD/maps" | tr -d ' ')

if [ "$MAP_COUNT" -eq 0 ]; then
  ok "No .map files in $BUILD_DIR."
  note "Also verify on the live host — the platform may generate them at deploy time:"
  [ "$QUIET" -eq 1 ] || cmd "curl -s -o /dev/null -w '%{http_code}\\n' https://yourapp.example/assets/index.js.map   # want 404"
else
  WITH_CONTENT=0
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    if head -c 200000 "$m" 2>/dev/null | grep -qa '"sourcesContent"'; then
      WITH_CONTENT=$((WITH_CONTENT+1))
    fi
  done < "$TMPD/maps"

  if [ "$WITH_CONTENT" -gt 0 ]; then
    warn "SECRET-04 — $MAP_COUNT source map(s) in the artifact, $WITH_CONTENT containing sourcesContent."
  else
    warn "SECRET-04 — $MAP_COUNT source map(s) in the artifact (no sourcesContent found)."
  fi
  head -20 "$TMPD/maps" | while IFS= read -r m; do info "  $m"; done
  [ "$MAP_COUNT" -gt 20 ] && info "  ... and $((MAP_COUNT-20)) more"
  info ""
  info "What an attacker recovers: sourcesContent holds the VERBATIM text of your original"
  info "files — comments, dead code, commented-out credentials, internal endpoint constants,"
  info "and functions that no UI ever calls. One command rebuilds your source tree:"
  [ "$QUIET" -eq 1 ] || cmd "jq -r '.sourcesContent[]' $(head -1 "$TMPD/maps")"
  info "The worked example in the Sentry (the pentest firm, not Sentry.io) writeup recovered"
  info "an unreferenced updateUserData() taking email, firstName, lastName, password and"
  info "accessToken, enumerated a userId, POSTed a new password, and got a 200 OK. Full"
  info "account takeover from a file nobody meant to publish. They are also findable at"
  info "scale: site:example.com filetype:map."
  info ""
  info "Fix — the config flag alone is NOT the fix:"
  info "  Vite:     build: { sourcemap: 'hidden' }        in vite.config.ts"
  info "  Next.js:  productionBrowserSourceMaps: false    in next.config.js (this is the default)"
  info "  Then, in your deploy step, after uploading to your error tracker:"
  [ "$QUIET" -eq 1 ] || cmd "sentry-cli sourcemaps upload ./$BUILD_DIR && find ./$BUILD_DIR -name '*.map' -delete"
  info "'hidden' only strips the trailing //# sourceMappingURL= comment. The file is still"
  info "there and still fetchable by guessing chunk.js.map. Deleting it from the deployed"
  info "artifact is the actual control."
fi

# Maps referenced by comment but absent from disk — still fetchable if the platform serves them.
SM_REFS=$(grep -rlaE '//# sourceMappingURL=[^ ]+\.map' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | wc -l | tr -d ' ')
if [ "$SM_REFS" -gt 0 ] && [ "$MAP_COUNT" -eq 0 ]; then
  warn "$SM_REFS shipped file(s) still carry a //# sourceMappingURL= comment with no .map on disk."
  info "Harmless if the map genuinely does not exist. It becomes a finding if your build"
  info "pipeline generates maps on the host. Probe the live URL named in the comment."
fi

# =============================================================================
# 5. Expected to be public — verify the CONTROL, not the key
# =============================================================================
hdr "5. Expected to be public — verify the control instead"

if [ "$QUIET" -eq 1 ]; then
  printf '%sNothing in this section is a finding.%s Reporting these as leaks is the fastest way to\n' "$C_B" "$C_0"
  printf 'get your real findings ignored — the bug is never the visible key, it is the missing\n'
  printf 'control behind it. (sources + full wording: re-run without --quiet)\n\n'
else
printf '%sNothing in this section is a finding.%s Reporting these as leaks is the single\n' "$C_B" "$C_0"
printf 'fastest way to get your real findings ignored. Supabase'"'"'s CEO, on a scan that\n'
printf 'claimed 11%% of indie launches were "exposing Supabase credentials": "Finding a\n'
printf 'Supabase project URL and anon key in client code is expected, as both are\n'
printf 'designed to be public." The bug is never the visible key. It is the missing\n'
printf 'control behind it.\n\n'
fi

FOUND_PUBLIC=0

# --- Supabase ---------------------------------------------------------------
SUPA_URL=$(grep -rhaoE 'https://[a-z0-9]{15,25}\.supabase\.co' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -1)
SB_PUB=$(grep -rhaoE 'sb_publishable_[A-Za-z0-9_-]{16,}' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -1)
if [ -n "$SUPA_URL" ] || [ -n "$SB_PUB" ] || [ -n "$ANON_KEY" ]; then
  FOUND_PUBLIC=1
  [ -z "$SUPA_REF" ] && [ -n "$SUPA_URL" ] && SUPA_REF=$(printf '%s' "$SUPA_URL" | sed -E 's#https://([a-z0-9]+)\.supabase\.co#\1#')
  PUBKEY="${SB_PUB:-$ANON_KEY}"
  printf '%sNOTVULN-01  Supabase publishable / anon key%s\n' "$C_B" "$C_0"
  info "project ref: ${SUPA_REF:-<not found>}"
  info "key:         ${PUBKEY:0:24}..."
  if [ "$QUIET" -eq 1 ]; then
    info "NOT A LEAK: Supabase lists this as safe to expose ('web page, mobile or desktop app,"
    info "GitHub actions, CLIs, source code'). DO NOT ROTATE IT AS REMEDIATION."
    info "REQUIRED CONTROL: RLS on every table in an exposed schema, with real policies, plus"
    info "least-privilege grants. Test it from outside right now — highest-value command here:"
  else
  info "Supabase lists this as safe to expose in 'web page, mobile or desktop app,"
  info "GitHub actions, CLIs, source code'. DO NOT ROTATE IT AS REMEDIATION."
  info ""
  info "The control that must exist: RLS enabled on every table in an exposed schema,"
  info "with real policies, plus least-privilege grants. Test it from outside, right now."
  info "This is the highest-value command in the whole audit:"
  info ""
  fi
  if [ -n "$SUPA_REF" ] && [ -n "$PUBKEY" ]; then
    cmd "REF=$SUPA_REF"
    cmd "ANON='$PUBKEY'"
  else
    cmd "REF=<your-project-ref>"
    cmd "ANON=<the-publishable-key-above>"
  fi
  cmd 'curl -s "https://$REF.supabase.co/rest/v1/" -H "apikey: $ANON" | jq ".definitions | keys[]"'
  if [ "$QUIET" -eq 1 ]; then
    info "   ^ PostgREST serves an OpenAPI list of every table/view/RPC on the root path"
    info "     (NOTVULN-05, inherent to PostgREST; hiding it is not a fix). Then per table:"
    cmd 'curl -s "https://$REF.supabase.co/rest/v1/<table>?select=*&limit=1" -H "apikey: $ANON"'
    info "  TRUE POSITIVE: a JSON row for a table that should be private = BAAS-01, CRITICAL."
    info "                 Fix the RLS, do not rotate the key; stop the audit and fix it first."
    info "  EXPECTED: rows from a genuinely public table, or an empty array [] (a working"
    info "            policy seen by a logged-out caller). (full triage: run without --quiet)"
  else
  info "   ^ PostgREST serves a full OpenAPI description of every table, view and RPC on"
  info "     the root path, so an attacker never has to guess names. That is NOTVULN-05 —"
  info "     inherent to PostgREST, not a bug in your app, and hiding it is not a fix."
  info "     Then, for each table it names:"
  cmd 'curl -s "https://$REF.supabase.co/rest/v1/<table>?select=*&limit=1" -H "apikey: $ANON"'
  info ""
  info "  TRUE POSITIVE: a JSON row comes back for a table that should be private."
  info "                 That is a full table read, from any browser, by anyone, with no"
  info "                 login. The finding is BAAS-01 (RLS never enabled), CRITICAL."
  info "                 Fix the RLS. Do not rotate the key."
  info "  FALSE POSITIVE: rows come back from a genuinely public table — published blog"
  info "                 posts, a public directory, a pricing table. Expected."
  info "  ALSO EXPECTED: an empty array []. That is what a working policy looks like to a"
  info "                 logged-out caller. Enumeration returning nothing is the goal."
  info ""
  info "If rows come back, stop the rest of the audit and fix that first."
  fi
  printf '\n'
fi

# --- Firebase ---------------------------------------------------------------
FB_CFG=$(grep -rhaoE '"(authDomain|databaseURL|storageBucket|messagingSenderId)"' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -4)
FB_PROJ=$(grep -rhaoE '[a-z0-9-]+\.firebaseio\.com|[a-z0-9-]+\.firebaseapp\.com' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -1)
if [ -n "$FB_CFG" ] || [ -n "$FB_PROJ" ]; then
  FOUND_PUBLIC=1
  printf '%sNOTVULN-02  Firebase web config%s\n' "$C_B" "$C_0"
  [ -n "$FB_PROJ" ] && info "host: $FB_PROJ"
  if [ "$QUIET" -eq 1 ]; then
    info "NOT A LEAK: Firebase docs say these keys 'do not need to be treated as secrets'; they"
    info "only identify the project and app. REQUIRED CONTROL: Security Rules plus App Check."
    info "TRUE POSITIVE: an unauthenticated 200 with data from RTDB/Firestore/Storage = BAAS-03"
    info "(the Tea app shape). FALSE POSITIVE: 401/403, or a collection meant to be world-readable."
    info "(the three probe curls + full wording: re-run without --quiet)"
  else
  info "Firebase's docs: keys restricted to Firebase services 'do not need to be treated"
  info "as secrets, and it's safe to include them in your code or configuration files' —"
  info "they 'only identify your Firebase project and app'."
  info ""
  info "The control that must exist: Security Rules plus App Check. Test from outside:"
  cmd 'PROJ=<your-project-id>'
  cmd 'curl -s -o /dev/null -w "%{http_code}\n" "https://$PROJ-default-rtdb.firebaseio.com/.json"'
  cmd 'curl -s "https://firestore.googleapis.com/v1/projects/$PROJ/databases/(default)/documents/users" | head'
  cmd 'curl -s "https://firebasestorage.googleapis.com/v0/b/$PROJ.appspot.com/o" | head'
  info "  TRUE POSITIVE: 200 with data on an unauthenticated request. That is BAAS-03 and"
  info "                 it is the Tea app shape — roughly 72,000 images including"
  info "                 ID-verification selfies and driver's licences, then a second"
  info "                 datastore with ~1.1M private messages."
  info "  FALSE POSITIVE: 401/403, or 200 on a collection you intended to be world-readable."
  fi
  printf '\n'
fi

# --- AIza: the one genuinely ambiguous prefix -------------------------------
AIZA=$(grep -rhaoE 'AIza[0-9A-Za-z_-]{35}' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u)
if [ -n "$AIZA" ]; then
  FOUND_PUBLIC=1
  printf '%sAIza key — you MUST check what it is restricted to%s\n' "$C_B" "$C_0"
  printf '%s\n' "$AIZA" | while IFS= read -r k; do info "  ${k:0:14}..."; done
  if [ "$QUIET" -eq 1 ]; then
    info "AMBIGUOUS BY PREFIX — the one real trap in this section. Firebase Web API key or Google"
    info "Maps key -> NOTVULN-02/04, public by design (Maps REQUIRES HTTP-referrer + API"
    info "restrictions or it is a BILLING liability; that restriction is the finding). A GEMINI"
    info "developer key has the IDENTICAL prefix and IS a real secret — rotate and move the call"
    info "server-side. Judge on the Google Cloud restriction (APIs & Services > Credentials),"
    info "never on the prefix."
  else
  info "Same prefix, two completely different answers, and this is the one real trap in"
  info "this section:"
  info "  Firebase Web API key or Google Maps key -> NOTVULN-02 / NOTVULN-04, public by"
  info "    design. Google Maps needs HTTP referrer + API restrictions, otherwise it is a"
  info "    BILLING liability rather than a data one. That restriction is the finding."
  info "  Gemini Developer API key -> a real secret. Firebase's own docs say it 'should"
  info "    never be included in your code or configuration files'. If this is a Gemini"
  info "    key, treat it as section 2: rotate, then move the call server-side."
  info "Check what it is restricted to in Google Cloud Console -> APIs & Services ->"
  info "Credentials. Judge on the restriction, never on the prefix."
  fi
  printf '\n'
fi

# --- The rest ---------------------------------------------------------------
PK_STRIPE=$(grep -rhaoE 'pk_(live|test)_[A-Za-z0-9]{16,}' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -3)
if [ -n "$PK_STRIPE" ]; then
  FOUND_PUBLIC=1
  printf '%sNOTVULN-03  Stripe / Clerk publishable key%s\n' "$C_B" "$C_0"
  printf '%s\n' "$PK_STRIPE" | while IFS= read -r k; do info "  ${k:0:20}..."; done
  if [ "$QUIET" -eq 1 ]; then
    info "NOT A LEAK: Stripe marks publishable keys 'Safe to expose: Yes'. sk_/rk_/sk_org_ are the"
    info "opposite and are never safe client-side. REQUIRED CONTROL (PAY-03): every amount, price"
    info "and authorization decided SERVER-side; never trust an amount that came from the client."
  else
  info "Stripe's own key table marks publishable keys 'Safe to expose: Yes... you can put"
  info "in front-end code.' sk_, rk_ and sk_org_ are the opposite: 'Only publishable keys"
  info "are safe to expose outside your application's backend.'"
  info "The control that must exist (PAY-03): every amount, every price and every"
  info "authorization decided SERVER-side. Never trust an amount that came from the client."
  fi
  printf '\n'
fi

ANALYTICS=$(grep -rhaoE 'phc_[A-Za-z0-9]{20,}|https://[a-z0-9]+@[a-z0-9.]*ingest[a-z0-9.]*\.sentry\.io/[0-9]+|G-[A-Z0-9]{8,}|pk\.eyJ[A-Za-z0-9_-]{20,}' "${GREP_EXCL[@]}" "${SCAN_PATHS[@]}" 2>/dev/null | sort -u | head -5)
if [ -n "$ANALYTICS" ]; then
  FOUND_PUBLIC=1
  printf '%sNOTVULN-04  Analytics / mapping public tokens%s\n' "$C_B" "$C_0"
  printf '%s\n' "$ANALYTICS" | while IFS= read -r k; do info "  ${k:0:28}..."; done
  if [ "$QUIET" -eq 1 ]; then
    info "All client-side by design. REQUIRED CONTROLS: phc_ = PostHog PROJECT key, distinct from"
    info "personal API keys, project secret keys and the feature-flags secure key (those ARE"
    info "secret); Sentry DSN accepts events only, consider inbound filters + rate limits; pk.eyJ"
    info "= Mapbox public token, needs URL restrictions (max 100 URLs) or it is billable from any"
    info "origin, sk. is server-only; G-... = GA measurement ID, public by construction."
  else
  info "All designed for client-side use. The controls that must exist:"
  info "  phc_       PostHog project key. Distinct from personal API keys, project SECRET"
  info "             keys and the feature-flags secure key — all three of those are secret."
  info "  Sentry DSN accepts events only. Consider inbound filters and rate limits."
  info "  pk.eyJ...  Mapbox PUBLIC token. Needs URL restrictions (max 100 URLs per token) so"
  info "             a scraped token cannot be billed from another origin. sk. is server-only."
  info "  G-...      Google Analytics measurement ID. Public by construction."
  fi
  printf '\n'
fi

if [ "$FOUND_PUBLIC" -eq 0 ]; then
  ok "No publishable keys found either. Nothing to explain away."
fi

# =============================================================================
# 6. Summary
# =============================================================================
hdr "Summary"

printf 'build dir      %s\n' "$BUILD_DIR"
printf 'CRITICAL       %s\n' "$CRIT_COUNT"
printf 'WARN           %s\n' "$WARN_COUNT"
printf '\n'

if [ "$CRIT_COUNT" -gt 0 ]; then
  printf '%s%sStop here and fix the CRITICAL findings before anything else.%s\n' "$C_B" "$C_RED" "$C_0"
  printf 'Rotate at the provider first, then remove from code, then rebuild, then re-run this.\n'
elif [ "$WARN_COUNT" -gt 0 ]; then
  printf '%sNo credential found in the artifact. Work the WARN list.%s\n' "$C_B" "$C_0"
else
  printf '%sThis check found nothing.%s That is not the same as "you are secure".\n' "$C_GRN" "$C_0"
fi

printf '\n'
if [ "$QUIET" -eq 1 ]; then
printf '%sWhat this script cannot see%s (a silent gap reads as a clean bill of health):\n' "$C_B" "$C_0"
printf '  1. Broken access control — static analysis structurally cannot find it (it needs the intended policy) and it is the #1 real-world failure class. Run the two-account differential test (references/authz-verification.md).\n'
printf '  2. Whether RLS is actually on — section 5 printed the curl. Run it.\n'
printf '  3. Secrets that never reached this build: git history (SECRET-05), Docker layers and CI logs (SECRET-06), agent config files (SECRET-07). -> audit-repo.sh, gitleaks git -v . , trufflehog git file://. --results=verified\n'
printf '  4. Secrets rendered at request time by a running server (SECRET-08) if the prerendered HTML is generated on the host. Check the live page with curl | grep -aoE "sk_live_[A-Za-z0-9]{16,}|service_role|password_hash".\n'
printf '  5. Anything the hosting platform adds at deploy time — re-run the .map probe and the dotfile sweep against the live host.\n'
printf '  (full text of each: re-run without --quiet)\n'
else
printf '%sWhat this script cannot see:%s\n' "$C_B" "$C_0"
printf '  - Broken access control. Static analysis structurally cannot find it, because it\n'
printf '    requires knowing the intended policy. It is also the #1 real-world failure\n'
printf '    class. Run the two-account differential test (references/authz-verification.md).\n'
printf '  - Whether RLS is actually on. Section 5 prints the curl. Run it.\n'
printf '  - Secrets that never reached this build: git history (SECRET-05), Docker layers\n'
printf '    and CI logs (SECRET-06), agent config files (SECRET-07). Use audit-repo.sh,\n'
printf '    plus: gitleaks git -v . and trufflehog git file://. --results=verified\n'
printf '  - Secrets rendered at request time by a running server (SECRET-08) if your\n'
printf '    prerendered HTML is generated on the host rather than at build time. Check the\n'
printf '    live page: curl -s https://yourapp.example | grep -aoE "sk_live_[A-Za-z0-9]{16,}|service_role|password_hash"\n'
printf '  - Anything your hosting platform adds at deploy time. Re-run the .map probe and\n'
printf '    the dotfile sweep against the live host.\n'
fi
printf '\n'

[ "$CRIT_COUNT" -gt 0 ] && exit 2
exit 0
