#!/usr/bin/env bash
#
# probe-live.sh — external live-deployment probe for an app YOU OWN.
#
# Source of truth: never-get-hacked research dossier,
#   02-AUDIT-AND-DETECTION-TOOLKIT.md section 7 (7.1 through 7.8).
#
# This runs the attacker's own external recon pipeline against your own site,
# in the order an attacker would, so you find what they would find first.
#
# SCOPE — this script implements ONLY the checks the dossier marks:
#   GREEN  (SAFE)   — passive reads, no state change, indistinguishable from
#                     normal traffic:  7.1 surface, 7.2 fingerprint/headers,
#                     7.3 exposed paths, 7.4 bundle mining, 7.6 CORS.
#   YELLOW (LOGGED) — auth-adjacent probes that appear in your logs but do not
#                     mutate data:  7.7 middleware-bypass (CVE-2025-29927).
#
# It DELIBERATELY DOES NOT run the RED staging-only checks (7.5 write probe,
# 7.8 abuse / rate-limit flooding). It prints what they are and tells you to
# run them against STAGING only. See the "STAGING-ONLY" banner near the end.
#
# Tooling: curl + standard POSIX shell only. Every request has a short timeout.
# Requests are strictly SEQUENTIAL — this never floods, so it cannot be
# mistaken for an attack against your own infrastructure.
#
# Finding IDs are dossier-stable (INFRA-09, SECRET-01, BAAS-04, INJECT-10,
# AUTHZ-03 ...). The skill routes on them; do not renumber them.

# NOTE: no `set -e`. curl and grep return non-zero on the normal "not found /
# no match" paths this script depends on; exiting on those would be a bug.
set -u
set -o pipefail 2>/dev/null || true

# ---------------------------------------------------------------------------
# Request settings — short timeouts on EVERY request, sequential only.
# ---------------------------------------------------------------------------
CONNECT_TIMEOUT=5
MAX_TIME=8
# --brief: one line per finding, narration and negative results suppressed.
# Nothing is skipped and no finding ID is lost — re-run without --brief for the
# full status tables, the explanations and the STAGING-ONLY curl commands.
BRIEF=0
UA='never-get-hacked/probe-live (owner-authorised self-audit)'

# Small helper so every curl in the script carries the same guardrails.
cget() { curl -s --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" -A "$UA" "$@"; }

# ---------------------------------------------------------------------------
# Colour / labelling (degrades to plain text when not a TTY).
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'
else
  C_RESET=; C_BOLD=; C_GREEN=; C_YELLOW=; C_RED=; C_DIM=
fi
SAFE_TAG="${C_GREEN}[SAFE]${C_RESET}"
LOGGED_TAG="${C_YELLOW}[LOGGED]${C_RESET}"

# Findings accumulate here; printed in the summary at the end.
FINDINGS_FILE="$(mktemp -t probe-live-findings.XXXXXX)"
LOOT_DIR="$(mktemp -d -t probe-live-loot.XXXXXX)"
cleanup() { rm -rf "$FINDINGS_FILE" "$LOOT_DIR" 2>/dev/null; }
trap cleanup EXIT

# add_finding <ID> <SEVERITY> <one-line description>
add_finding() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$FINDINGS_FILE"; }

# nar: narration and negative/expected results. Printed in full mode, suppressed
# by --brief. Findings NEVER go through nar() — they are always printed.
nar() { [ "$BRIEF" -eq 1 ] || printf '%s\n' "$*"; }
# Counters so --brief can still say how much was probed (a silent gap reads as
# a clean bill of health).
PATHS_PROBED=0; MW_PROBED=0; CORS_PROBED=0; HDRS_PRESENT=0

section() { printf '\n%s\n%s %s%s\n' "${C_DIM}────────────────────────────────────────────────────────────${C_RESET}" "$1" "${C_BOLD}$2${C_RESET}" ""; }

# ---------------------------------------------------------------------------
# Usage / consent gate. Refuses to run without a target AND --i-own-this.
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
${C_BOLD}probe-live.sh${C_RESET} — external live probe for an app you own.

USAGE:
  probe-live.sh --i-own-this https://yourapp.example
  probe-live.sh https://yourapp.example --i-own-this

ARGUMENTS:
  <base-url>       Required. The deployment to probe, e.g. https://yourapp.example
  --i-own-this     Required. Explicit attestation that you own, or are
                   written-authorised to test, the target. Without it this
                   script does nothing.
  --brief          ONE LINE PER FINDING. Same probes, same requests, same
                   finding IDs, same summary — nothing is skipped. Suppresses
                   narration, the per-path status table, the per-origin CORS
                   table and the negative "not vulnerable" lines, and collapses
                   the STAGING-ONLY block and the closing caveats to one line
                   each. Re-run the same command WITHOUT --brief to get every
                   word, every status code and every curl command back.
                   (The anon-key NOT-a-finding line keeps its explanation in
                    both modes — that is what stops a public key being reported
                    as a leak.)

WHAT IT RUNS (dossier section 7):
  ${SAFE_TAG}   7.2 fingerprint & security headers      -> INFRA-02, INFRA-09
  ${SAFE_TAG}   7.3 exposed-path probing (.env/.git...)  -> SECRET-03, INFRA-05, INFRA-06
  ${SAFE_TAG}   7.4 bundle mining (endpoints & keys)     -> SECRET-01, SECRET-04, BAAS-04
  ${SAFE_TAG}   7.6 CORS reflection with credentials     -> INJECT-10
  ${LOGGED_TAG} 7.7 CVE-2025-29927 middleware bypass     -> AUTHZ-03 / FRAME-03
  (optional) 7.1 CT-log surface discovery via crt.sh

WHAT IT WILL NOT RUN (you must run these against STAGING only):
  ${C_RED}[STAGING ONLY]${C_RESET} 7.5 anon-key WRITE probe (mutates data)
  ${C_RED}[STAGING ONLY]${C_RESET} 7.8 abuse / rate-limit flooding
EOF
}

# Parse args: exactly one non-flag URL, plus the --i-own-this flag.
BASE_URL=""
OWN_FLAG=0
CRT_SH=0
for arg in "$@"; do
  case "$arg" in
    --i-own-this) OWN_FLAG=1 ;;
    --crt-sh)     CRT_SH=1 ;;
    --brief)      BRIEF=1 ;;
    -h|--help)    usage; exit 0 ;;
    -*)           printf '%s Unknown flag: %s\n\n' "${C_RED}ERROR${C_RESET}" "$arg" >&2; usage >&2; exit 2 ;;
    *)
      if [ -n "$BASE_URL" ]; then
        printf '%s More than one target URL given (%s and %s).\n' "${C_RED}ERROR${C_RESET}" "$BASE_URL" "$arg" >&2
        exit 2
      fi
      BASE_URL="$arg"
      ;;
  esac
done

if [ -z "$BASE_URL" ]; then
  printf '%s No target URL. This script refuses to run without one.\n\n' "${C_RED}REFUSING${C_RESET}" >&2
  usage >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# CONSENT BANNER — printed every run, before any request leaves the machine.
# ---------------------------------------------------------------------------
if [ "$BRIEF" -eq 1 ]; then
cat <<EOF >&2
${C_BOLD}${C_YELLOW}AUTHORISATION REQUIRED${C_RESET} — sends real HTTP requests to ${C_BOLD}${BASE_URL}${C_RESET}.
Run ONLY against a deployment you own or are authorised in writing to test;
probing infrastructure you do not control is unlawful in most jurisdictions.
This run is ${C_GREEN}SAFE${C_RESET}/${C_YELLOW}LOGGED${C_RESET} only: no writes, no flooding; the middleware-bypass
probe will appear in your access logs. (full notice: run without --brief)
EOF
else
cat <<EOF >&2
${C_BOLD}${C_YELLOW}================= AUTHORISATION REQUIRED =================${C_RESET}
This tool sends real HTTP requests to:
    ${C_BOLD}${BASE_URL}${C_RESET}

Run it ONLY against a deployment you OWN or are explicitly, in writing,
authorised to test. Probing infrastructure you do not control is unlawful
in most jurisdictions, regardless of intent.

This run stays in the ${C_GREEN}SAFE${C_RESET}/${C_YELLOW}LOGGED${C_RESET} tiers: passive reads plus one
logged auth-bypass check. It performs NO writes and NO flooding. Even so,
the ${C_YELLOW}LOGGED${C_RESET} middleware-bypass probe will appear in your access logs.
${C_BOLD}${C_YELLOW}==========================================================${C_RESET}
EOF
fi

if [ "$OWN_FLAG" -ne 1 ]; then
  printf '\n%s You did not pass --i-own-this. Re-run with that flag to attest ownership.\n' "${C_RED}REFUSING${C_RESET}" >&2
  exit 3
fi

# Normalise: strip a single trailing slash so "$APP$path" never doubles it.
APP="${BASE_URL%/}"
# Extract host (strip scheme, path, port) for fingerprint / CT-log queries.
HOST="$(printf '%s' "$APP" | sed -E 's#^[a-zA-Z]+://##; s#/.*$##; s#:[0-9]+$##')"

printf '\n%sTarget:%s %s   %sHost:%s %s\n' "$C_BOLD" "$C_RESET" "$APP" "$C_BOLD" "$C_RESET" "$HOST"

# Confirm the target is reachable before doing anything else.
ROOT_CODE="$(cget -o /dev/null -w '%{http_code}' "$APP/" 2>/dev/null)"
if [ -z "$ROOT_CODE" ] || [ "$ROOT_CODE" = "000" ]; then
  printf '\n%s Could not reach %s (no HTTP response before timeout). Check the URL and your network.\n' "${C_RED}ERROR${C_RESET}" "$APP" >&2
  exit 4
fi
printf '%sReachable%s — root returned HTTP %s\n' "$C_GREEN" "$C_RESET" "$ROOT_CODE"

# ===========================================================================
# 7.1  Surface discovery via Certificate Transparency logs (optional).  SAFE
#      Only runs with --crt-sh (queries the public crt.sh service).
# ===========================================================================
if [ "$CRT_SH" -eq 1 ]; then
  section "$SAFE_TAG" "7.1 Surface discovery — CT logs for %.$HOST"
  nar "Every TLS cert is published to public, append-only CT logs within seconds."
  nar "Vibe platforms auto-provision a cert for every preview/staging deploy."
  CRT_JSON="$(cget "https://crt.sh/?q=%25.$HOST&output=json" 2>/dev/null)"
  if [ -n "$CRT_JSON" ]; then
    # No jq dependency: pull name_value fields with grep/sed, split SANs, dedupe.
    printf '%s' "$CRT_JSON" \
      | grep -oE '"name_value":"[^"]+"' \
      | sed -E 's/"name_value":"//; s/"$//; s/\\n/\n/g' \
      | sort -u | sed 's/^/  /' | head -100
    nar ""
    echo "${C_BOLD}Open every host above in a logged-OUT private window.${C_RESET} True positive: any"
    echo "preview/staging/admin host that returns 200 with no auth wall. Manual judgement,"
    echo "so it is not auto-added to the findings list."
  else
    nar "  crt.sh returned nothing (rate-limited or no certs indexed yet)."
  fi
fi

# ===========================================================================
# 7.2  Fingerprint and security headers.                              SAFE
#      -> INFRA-02 (platform fingerprint)  ·  INFRA-09 (missing header set)
# ===========================================================================
section "$SAFE_TAG" "7.2 Fingerprint & security headers"

# One header fetch, reused for both fingerprint and header-presence checks.
HDRS="$(cget -sD - -o /dev/null "$APP/" 2>/dev/null)"

nar "${C_BOLD}Fingerprint${C_RESET} (what the stack advertises about itself):"
FP="$(printf '%s' "$HDRS" | grep -iE '^(x-powered-by|server|x-vercel-id|x-vercel-cache|x-nf-request-id):' )"
if [ "$BRIEF" -eq 0 ]; then
  if [ -n "$FP" ]; then
    printf '%s\n' "$FP" | sed 's/^/  /'
  else
    echo "  (no obvious x-powered-by / server / platform headers)"
  fi
fi
# True positive per dossier: x-powered-by: Next.js is sent by default.
if printf '%s' "$HDRS" | grep -iq '^x-powered-by:[[:space:]]*Next\.js'; then
  add_finding "INFRA-02" "LOW" "x-powered-by: Next.js is exposed (disable with poweredByHeader:false). Attackers pivot on this in Shodan and scan the whole cohort."
  echo "  ${C_YELLOW}->${C_RESET} x-powered-by: Next.js present. Set poweredByHeader:false. [INFRA-02]"
fi

nar ""
nar "${C_BOLD}Security headers${C_RESET} (present / MISSING). OWASP-recommended set:"
# name | grep-pattern | what it should be
check_header() {
  local label="$1" pat="$2" want="$3"
  if printf '%s' "$HDRS" | grep -iq "$pat"; then
    HDRS_PRESENT=$((HDRS_PRESENT + 1))
    [ "$BRIEF" -eq 1 ] || printf '  %spresent%s  %-28s %s\n' "$C_GREEN" "$C_RESET" "$label" "$(printf '%s' "$HDRS" | grep -iE "$pat" | head -1 | sed 's/^[[:space:]]*//')"
  else
    printf '  %sMISSING%s  %-28s want: %s\n' "$C_RED" "$C_RESET" "$label" "$want"
    return 1
  fi
}
MISSING_HDRS=""
check_header "Strict-Transport-Security" '^strict-transport-security:' 'max-age=63072000; includeSubDomains; preload' || MISSING_HDRS="$MISSING_HDRS HSTS"
check_header "X-Content-Type-Options"    '^x-content-type-options:'    'nosniff' || MISSING_HDRS="$MISSING_HDRS X-Content-Type-Options"
check_header "X-Frame-Options"           '^x-frame-options:'           'DENY (or CSP frame-ancestors)' || MISSING_HDRS="$MISSING_HDRS X-Frame-Options"
check_header "Referrer-Policy"           '^referrer-policy:'           'strict-origin-when-cross-origin' || MISSING_HDRS="$MISSING_HDRS Referrer-Policy"
check_header "Permissions-Policy"        '^permissions-policy:'        'geolocation=(), camera=(), microphone=()' || MISSING_HDRS="$MISSING_HDRS Permissions-Policy"
check_header "Content-Security-Policy"   '^content-security-policy:'   "nonce + 'strict-dynamic' + object-src 'none' + base-uri 'none'" || MISSING_HDRS="$MISSING_HDRS CSP"

# CSP false-pass: present but with unsafe-inline gives zero XSS protection.
CSP_LINE="$(printf '%s' "$HDRS" | grep -iE '^content-security-policy:' | head -1)"
if [ -n "$CSP_LINE" ] && printf '%s' "$CSP_LINE" | grep -iq 'unsafe-inline'; then
  add_finding "INFRA-09" "MEDIUM" "CSP is present but contains 'unsafe-inline' — script-src 'self' 'unsafe-inline' provides zero XSS protection. Move to nonce + 'strict-dynamic'."
  echo "  ${C_YELLOW}->${C_RESET} CSP contains 'unsafe-inline' — this is a false pass, zero XSS protection. [INFRA-09]"
fi
if [ -n "$MISSING_HDRS" ]; then
  add_finding "INFRA-09" "MEDIUM" "Missing security headers:$MISSING_HDRS. Nothing breaks when absent, so the AI feedback loop never surfaces them."
fi
[ "$BRIEF" -eq 1 ] && [ "$HDRS_PRESENT" -gt 0 ] && printf '  %s%s of 6 baseline headers present; only MISSING ones are listed above.%s\n' "$C_DIM" "$HDRS_PRESENT" "$C_RESET"
nar ""
nar "${C_DIM}Also run the site through MDN HTTP Observatory manually:"
nar "  https://developer.mozilla.org/en-US/observatory  (not scriptable with curl alone)${C_RESET}"

# ===========================================================================
# 7.3  Exposed-path probing.                                          SAFE
#      -> SECRET-03 (.env/.git over HTTP) · INFRA-05 (debug/seed/graphql/docs)
#         INFRA-06 (Vercel /_src /_logs)
# ===========================================================================
section "$SAFE_TAG" "7.3 Exposed-path probing"
nar "Sequential GETs. A 200 on a sensitive path is the finding; the status is printed for every path."
nar ""

# path | which finding ID a 200 maps to
probe_path() {
  local path="$1" id="$2" sev="$3" desc="$4"
  local code
  code="$(cget -o /dev/null -w '%{http_code}' "$APP$path" 2>/dev/null)"
  if [ "$code" = "200" ]; then
    printf '  %s200%s  %s   %s->%s %s [%s]\n' "$C_RED" "$C_RESET" "$path" "$C_YELLOW" "$C_RESET" "$desc" "$id"
    add_finding "$id" "$sev" "$path returned 200 — $desc"
  else
    [ "$BRIEF" -eq 1 ] || printf '  %s%s%s  %s\n' "$C_DIM" "$code" "$C_RESET" "$path"
  fi
  PATHS_PROBED=$((PATHS_PROBED + 1))
}

# .env / .git family  -> SECRET-03
for p in /.env /.env.local /.env.production /.env.backup /.env.bak /.env.old \
         /.git/HEAD /.git/config /.git/index; do
  probe_path "$p" "SECRET-03" "CRITICAL" "credential/source file reachable over HTTP"
done
# debug / seed / docs / introspection  -> INFRA-05
for p in /api/debug /api/seed /api/test /api-docs /swagger /swagger-ui.html \
         /openapi.json /redoc /docs /console; do
  probe_path "$p" "INFRA-05" "HIGH" "debug/seed/docs route left enabled"
done
# admin surfaces  -> AUTHZ-02 (report only as an unauthenticated 200; see 7.7 for bypass)
for p in /admin /admin/ /api/admin; do
  code="$(cget -o /dev/null -w '%{http_code}' "$APP$p" 2>/dev/null)"
  if [ "$code" = "200" ]; then
    printf '  %s200%s  %s   %s->%s admin surface served WITHOUT auth [AUTHZ-02]\n' "$C_RED" "$C_RESET" "$p" "$C_YELLOW" "$C_RESET"
    add_finding "AUTHZ-02" "HIGH" "$p returned 200 to an unauthenticated request — admin surface with no auth wall."
  else
    [ "$BRIEF" -eq 1 ] || printf '  %s%s%s  %s %s(a redirect/401/403 here is expected; 7.7 tests whether it can be bypassed)%s\n' "$C_DIM" "$code" "$C_RESET" "$p" "$C_DIM" "$C_RESET"
  fi
  PATHS_PROBED=$((PATHS_PROBED + 1))
done
# Vercel platform source/log endpoints  -> INFRA-06
for p in /_src /_logs; do
  probe_path "$p" "INFRA-06" "HIGH" "Vercel source/log endpoint exposed (deploy was made public; existing deploys must be DELETED)"
done
# GraphQL introspection  -> INFRA-05
GQL="$(cget "$APP/graphql" -H 'Content-Type: application/json' -d '{"query":"{__schema{types{name}}}"}' 2>/dev/null | head -c 400)"
if printf '%s' "$GQL" | grep -q '__schema'; then
  printf '  %s->%s /graphql answered an introspection query [INFRA-05]\n' "$C_RED" "$C_RESET"
  add_finding "INFRA-05" "HIGH" "/graphql answered __schema introspection without auth. Set introspection off in production and allowBatchedHttpRequests:false."
else
  [ "$BRIEF" -eq 1 ] || printf '  %s--%s /graphql did not return an introspectable schema\n' "$C_DIM" "$C_RESET"
fi
[ "$BRIEF" -eq 1 ] && printf '  %s%s paths probed; only 200s shown. Full status table: run without --brief.%s\n' "$C_DIM" "$PATHS_PROBED" "$C_RESET"
nar ""
nar "${C_DIM}Note: /.well-known/security.txt is the ONE path you WANT to return 200 (see incident-response).${C_RESET}"

# ===========================================================================
# 7.4  Bundle mining — endpoints, backend URLs, and keys in the JS.   SAFE
#      -> SECRET-01 (real secret in bundle) · BAAS-04 (service_role key)
#         SECRET-04 (production source maps)
# ===========================================================================
section "$SAFE_TAG" "7.4 Bundle mining"
nar "Every route path, table name and backend URL an AI-built app uses is in the"
nar "bundle by construction. Downloading the JS the site already serves to you."
nar ""

# Collect script URLs from the homepage: /_next/static, plus any src=...js.
HOME_HTML="$(cget "$APP/" 2>/dev/null)"
{
  printf '%s' "$HOME_HTML" | grep -oE '/_next/static/[^"'"'"']+\.js'
  printf '%s' "$HOME_HTML" | grep -oE 'src="[^"]+\.js"' | sed -E 's/^src="//; s/"$//'
} 2>/dev/null | sort -u > "$LOOT_DIR/js-urls.txt"

JS_COUNT=0
while IFS= read -r jurl; do
  [ -z "$jurl" ] && continue
  JS_COUNT=$((JS_COUNT + 1))
  [ "$JS_COUNT" -gt 20 ] && break    # cap: sequential, never floods
  case "$jurl" in
    http*) full="$jurl" ;;
    /*)    full="$APP$jurl" ;;
    *)     full="$APP/$jurl" ;;
  esac
  # Flatten the path into a safe filename.
  fname="$(printf '%s' "$jurl" | tr '/:?&=' '_____')"
  cget -o "$LOOT_DIR/$fname" "$full" 2>/dev/null
  # 7.4/SECRET-04: does a source map ship alongside this bundle?
  MAP_BODY="$(cget "$full.map" 2>/dev/null | head -c 200)"
  if printf '%s' "$MAP_BODY" | grep -q '"sourcesContent"'; then
    printf '  %s->%s source map with sourcesContent at %s.map [SECRET-04]\n' "$C_RED" "$C_RESET" "$jurl"
    add_finding "SECRET-04" "HIGH" "Production source map with sourcesContent served at $jurl.map — recovers your verbatim original source. Set sourcemap off for prod builds."
  fi
done < "$LOOT_DIR/js-urls.txt"
printf '  Downloaded %s bundle file(s) into a temp dir for offline grep.\n' "$JS_COUNT"
nar ""

nar "${C_BOLD}Endpoints & backend hosts referenced in the bundle:${C_RESET}"
API_PATHS="$(grep -rhoE '/api/[a-zA-Z0-9/_-]+' "$LOOT_DIR" 2>/dev/null | sort -u | head -40)"
BAAS_HOSTS="$(grep -rhoE 'https://[a-z0-9-]+\.supabase\.co' "$LOOT_DIR" 2>/dev/null | sort -u)"
INTERESTING="$(grep -rhoE '(admin|debug|feature[_-]?flag|internal|secret)[a-zA-Z_]*' "$LOOT_DIR" 2>/dev/null | sort -u | head -20)"
if [ "$BRIEF" -eq 1 ]; then
  # Keep the recon, drop the listing: counts plus the first ten endpoints.
  API_N="$(printf '%s' "$API_PATHS" | grep -c . 2>/dev/null || echo 0)"
  [ -n "$API_PATHS" ] && printf '%s\n' "$API_PATHS" | head -10 | sed 's/^/  /'
  [ "$API_N" -gt 10 ] && printf '  %s+%s more endpoint(s) — full list: run without --brief%s\n' "$C_DIM" "$((API_N - 10))" "$C_RESET"
  [ -n "$BAAS_HOSTS" ] && printf '%s\n' "$BAAS_HOSTS" | sed 's/^/  /'
  [ -n "$INTERESTING" ] && printf '  %s%s interesting identifier(s) (admin/debug/flag/internal/secret) — run without --brief%s\n' "$C_DIM" "$(printf '%s' "$INTERESTING" | grep -c .)" "$C_RESET"
else
  [ -n "$API_PATHS" ] && printf '%s\n' "$API_PATHS" | sed 's/^/  /'
  [ -n "$BAAS_HOSTS" ] && printf '%s\n' "$BAAS_HOSTS" | sed 's/^/  /'
  [ -n "$INTERESTING" ] && { echo "  ${C_DIM}interesting identifiers:${C_RESET}"; printf '%s\n' "$INTERESTING" | sed 's/^/    /'; }
fi
nar ""

nar "${C_BOLD}Key / secret triage (role claim, not mere visibility):${C_RESET}"
# JWTs: decode payload, read the role claim. THIS is where false positives live.
FOUND_JWT=0
grep -rhoE 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}' "$LOOT_DIR" 2>/dev/null | sort -u | while IFS= read -r tok; do
  payload="$(printf '%s' "$tok" | cut -d. -f2)"
  # base64url -> base64, pad, decode.
  b64="$(printf '%s' "$payload" | tr '_-' '/+')"
  case $(( ${#b64} % 4 )) in 2) b64="$b64==";; 3) b64="$b64=";; esac
  role="$(printf '%s' "$b64" | base64 -d 2>/dev/null | grep -oE '"role"[[:space:]]*:[[:space:]]*"[a-z_]+"' | grep -oE '"[a-z_]+"$' | tr -d '"')"
  case "$role" in
    service_role)
      printf '  %sCRITICAL%s JWT with role=service_role in the bundle [BAAS-04]\n' "$C_RED" "$C_RESET"
      add_finding "BAAS-04" "CRITICAL" "A service_role JWT is in the shipped bundle. It carries Postgres BYPASSRLS (superuser-equivalent); RLS is irrelevant while it is public. Rotate at Supabase, remove from client."
      ;;
    anon|"")
      # FALSE POSITIVE the dossier is emphatic about. This explanation survives
      # --brief on purpose: a bare ID here would let the agent report a
      # public-by-design key as a leak, which is the whole point of the line.
      if [ "$BRIEF" -eq 1 ]; then
        printf '  %sanon/publishable%s JWT — %sNOT a finding%s, public by design; do not rotate it as remediation. REQUIRED CONTROL: RLS on every table in an exposed schema + least-privilege grants (if rows come back the finding is BAAS-01, never "leaked key").\n' "$C_GREEN" "$C_RESET" "$C_BOLD" "$C_RESET"
      else
        printf '  %sanon/publishable%s JWT — %sNOT a finding.%s Public by design; do NOT rotate it as remediation.\n' "$C_GREEN" "$C_RESET" "$C_BOLD" "$C_RESET"
      fi
      ;;
    *)
      printf '  %sreview%s JWT role=%s — decode and confirm it is not privileged\n' "$C_YELLOW" "$C_RESET" "$role"
      ;;
  esac
done

# Hard secret patterns (from T0.3). ANY hit here is a real leak, not a public key.
SECRET_HITS="$(grep -rhoE 'sb_secret_[A-Za-z0-9_-]+|sk_live_[A-Za-z0-9]+|sk-ant-[A-Za-z0-9_-]+|AKIA[0-9A-Z]{16}|SG\.[A-Za-z0-9_-]{20,}|re_[A-Za-z0-9_-]{20,}' "$LOOT_DIR" 2>/dev/null | sort -u)"
if [ -n "$SECRET_HITS" ]; then
  echo "  ${C_RED}CRITICAL${C_RESET} secret-shaped strings in the bundle [SECRET-01]:"
  printf '%s\n' "$SECRET_HITS" | sed -E 's/(.{12}).*/\1…(redacted)/' | sed 's/^/    /'
  add_finding "SECRET-01" "CRITICAL" "A real secret (sb_secret_/sk_live_/sk-ant-/AKIA/SG./re_ pattern) is present in the client bundle. Rotate at the provider first, then remove."
else
  nar "  No hard-secret patterns (sb_secret_ / sk_live_ / sk-ant- / AKIA / SG. / re_) found in the bundle."
fi
echo "  ${C_DIM}Reminder: a visible Supabase/Firebase ANON key is NOT a finding. Triage on the role claim.${C_RESET}"

# ===========================================================================
# 7.6  CORS testing — origin reflection with credentials.            SAFE
#      -> INJECT-10
# ===========================================================================
section "$SAFE_TAG" "7.6 CORS testing (origin reflection + credentials)"
nar "Sending forged Origins to an API path and reading what comes back. curl ignores"
nar "CORS itself, so this reads the raw headers a browser would enforce."
nar ""

# Pick an API path to test: first /api/* seen in the bundle, else /api/me.
CORS_PATH="$(grep -rhoE '/api/[a-zA-Z0-9/_-]+' "$LOOT_DIR" 2>/dev/null | sort -u | head -1)"
[ -z "$CORS_PATH" ] && CORS_PATH="/api/me"
echo "Testing path: ${C_BOLD}$CORS_PATH${C_RESET}"

cors_test() {
  local origin="$1" label="$2"
  local resp acao acac
  resp="$(cget -sD - -o /dev/null "$APP$CORS_PATH" -H "Origin: $origin" 2>/dev/null)"
  acao="$(printf '%s' "$resp" | grep -iE '^access-control-allow-origin:' | head -1 | sed 's/^[^:]*:[[:space:]]*//; s/[[:space:]]*$//')"
  acac="$(printf '%s' "$resp" | grep -iqE '^access-control-allow-credentials:[[:space:]]*true' && echo true || echo false)"
  CORS_PROBED=$((CORS_PROBED + 1))
  [ "$BRIEF" -eq 1 ] || printf '  Origin %-38s -> ACAO: %-30s ACAC: %s\n' "$label" "${acao:-<none>}" "$acac"
  # True positive: our forged origin is reflected back verbatim.
  if [ -n "$acao" ] && [ "$acao" = "$origin" ]; then
    if [ "$acac" = "true" ]; then
      add_finding "INJECT-10" "HIGH" "CORS reflects the request Origin ($origin) AND sends Access-Control-Allow-Credentials: true on $CORS_PATH — same-origin policy is effectively off; any site can make credentialed reads. Use an exact-string allowlist (a Set), never reflection."
      echo "    ${C_RED}->${C_RESET} reflected origin + credentials:true — same-origin policy is OFF. [INJECT-10]"
    else
      add_finding "INJECT-10" "MEDIUM" "CORS reflects the request Origin ($origin) on $CORS_PATH (no credentials). Still a misconfiguration for token-auth APIs. Use an exact-string allowlist."
      echo "    ${C_YELLOW}->${C_RESET} reflected origin (no credentials) — still a misconfiguration. [INJECT-10]"
    fi
  fi
  if [ "$origin" = "null" ] && [ "$acao" = "null" ]; then
    add_finding "INJECT-10" "HIGH" "CORS accepts Origin: null on $CORS_PATH — forgeable from a sandboxed data: iframe. Remove 'null' from the allowlist."
    echo "    ${C_RED}->${C_RESET} 'null' origin accepted — forgeable from a data: iframe. [INJECT-10]"
  fi
}
cors_test "https://evil.example" "https://evil.example (attacker)"
cors_test "null" "null (sandboxed-iframe forgeable)"
cors_test "https://$HOST.evil.tld" "https://$HOST.evil.tld (suffix bypass)"
cors_test "https://evil$HOST" "https://evil$HOST (prefix bypass)"
[ "$BRIEF" -eq 1 ] && printf '  %s%s forged origins tested; only reflections are reported. Full ACAO/ACAC table: run without --brief.%s\n' "$C_DIM" "$CORS_PROBED" "$C_RESET"
# Vary: Origin sanity note when a specific origin is echoed.
if printf '%s' "$(cget -sD - -o /dev/null "$APP$CORS_PATH" -H 'Origin: https://evil.example' 2>/dev/null)" | grep -iqE '^access-control-allow-origin: https' && \
   ! printf '%s' "$(cget -sD - -o /dev/null "$APP$CORS_PATH" -H 'Origin: https://evil.example' 2>/dev/null)" | grep -iq '^vary:.*origin'; then
  echo "  ${C_DIM}Note: a specific origin is echoed without 'Vary: Origin' — add it to prevent cache poisoning.${C_RESET}"
fi

# ===========================================================================
# 7.7  Auth-bypass — CVE-2025-29927 middleware bypass.               LOGGED
#      -> AUTHZ-03 / FRAME-03
#      Differential: compare a CONTROL request to a PROBE request. Only report
#      a finding when they DIFFER in a way that shows the guard was skipped —
#      never merely on an unexpected status code.
# ===========================================================================
section "$LOGGED_TAG" "7.7 Middleware-bypass probe (CVE-2025-29927)"
nar "This is the ONE logged check in this run. It sends the x-middleware-subrequest"
nar "header and compares against a control request with no header. A finding is"
nar "reported ONLY when the header turns a blocked/redirected response into a 200."
nar ""

# "Blocked" = the guard did its job on the control (redirect to login / 401 / 403 / 404).
is_blocked() { case "$1" in 301|302|303|307|308|401|403|404) return 0 ;; *) return 1 ;; esac; }

mw_probe() {
  local path="$1"
  local control p1 p5
  control="$(cget -o /dev/null -w '%{http_code}' "$APP$path" 2>/dev/null)"
  # Payload 1: single token (pre-13.2 shape). Payload 2: 5x repeat (13.2+).
  p1="$(cget -o /dev/null -w '%{http_code}' "$APP$path" -H 'x-middleware-subrequest: middleware' 2>/dev/null)"
  p5="$(cget -o /dev/null -w '%{http_code}' "$APP$path" -H 'x-middleware-subrequest: middleware:middleware:middleware:middleware:middleware' 2>/dev/null)"
  MW_PROBED=$((MW_PROBED + 1))
  [ "$BRIEF" -eq 1 ] || printf '  %-14s control=%s  header-1x=%s  header-5x=%s\n' "$path" "$control" "$p1" "$p5"
  # Differential rule: control was blocked, probe became 200 => bypass confirmed.
  if is_blocked "$control" && { [ "$p1" = "200" ] || [ "$p5" = "200" ]; }; then
    add_finding "AUTHZ-03" "CRITICAL" "CVE-2025-29927 middleware bypass on $path: control returned $control (blocked) but the x-middleware-subrequest header returned 200. Upgrade Next.js (12.3.5/13.5.9/14.2.25/15.2.3+), strip the header at the proxy, AND re-check auth() inside the route."
    echo "    ${C_RED}->${C_RESET} BYPASS: $control (blocked) became 200 with the header. [AUTHZ-03 / FRAME-03]"
  elif [ "$control" = "200" ]; then
    nar "    ${C_DIM}-- control already 200 (path is public); nothing to bypass, no finding.${C_RESET}"
  elif [ "$control" = "$p1" ] && [ "$control" = "$p5" ]; then
    nar "    ${C_GREEN}--${C_RESET} status unchanged by the header — not vulnerable via this probe."
  else
    nar "    ${C_DIM}-- statuses differ but no blocked->200 transition; not a confirmed bypass.${C_RESET}"
  fi
}
for p in /admin /admin/ /api/admin /dashboard; do mw_probe "$p"; done
if [ "$BRIEF" -eq 1 ]; then
  printf '  %s%s paths probed; a bypass is reported only on a blocked->200 transition. A clean\n' "$C_DIM" "$MW_PROBED"
  printf '  result proves nothing: middleware was never a boundary — re-check auth() inside every\n'
  printf '  handler/action/server-component; on Next.js 16+ the file is proxy.ts. (AUTHZ-03)%s\n' "$C_RESET"
else
echo
echo "${C_DIM}Even a clean result here does not mean you are safe: middleware was never a boundary."
echo "Re-check auth() inside every handler/action/server-component regardless. On Next.js 16+"
echo "the file is proxy.ts — grep for both. (AUTHZ-03)${C_RESET}"
fi

# ===========================================================================
# STAGING-ONLY checks this script deliberately does NOT run.          RED
# ===========================================================================
section "${C_RED}[STAGING ONLY]${C_RESET}" "Checks intentionally skipped here"
if [ "$BRIEF" -eq 1 ]; then
cat <<EOF
These are RED — run them ONLY against staging; they mutate data or flood the service.
  1. 7.5 anon-key WRITE probe (mass assignment / missing write policy): PATCH a known row
     with the anon key. True positive: a 2xx or a Content-Range header means the write LANDED.
     (Legacy-key projects: the anon key is itself a project-signed JWT, so Bearer <ANON_KEY>
     passes verify_jwt. Resolve the caller in code and fail closed.)
  2. 7.8 abuse / rate-limit probing (this FLOODS): 50 sequential POSTs to the login route, and
     ?limit=100000 on a list endpoint. True positive: you never see a 429, or 100,000 rows come back.
  3. Not automated (needs YOUR test accounts, and an inbox for password-reset): open signup on an
     invite-only app; password-reset host poisoning via a forged Host header; login user-enumeration
     oracle (status AND timing). Run these by hand per dossier 7.7.
  The exact curl commands for all three: re-run this command without --brief.
EOF
else
cat <<EOF
The dossier marks these RED — run them ONLY against a staging deployment,
never against production, because they mutate data or flood the service:

  7.5  Anon-key WRITE probe (mass-assignment / missing write policy):
       curl -i -X PATCH "https://\$REF.supabase.co/rest/v1/<table>?id=eq.<known-id>" \\
         -H "apikey: \$ANON" -H "Content-Type: application/json" -d '{"role":"admin"}'
       True positive: a 2xx or a Content-Range header means the write LANDED.
       (Read verify_jwt carefully: on legacy-key projects the anon key is itself
        a project-signed JWT, so Bearer <ANON_KEY> passes the gate. Resolve the
        caller in code and fail closed.)

  7.8  Abuse / rate-limit probing (this FLOODS — staging only):
       for i in \$(seq 1 50); do curl -s -o /dev/null -w '%{http_code} ' \\
         -X POST "\$STAGING/api/auth/login" -H 'content-type: application/json' \\
         -d '{"email":"a@b.c","password":"x"}'; done; echo
       curl -s "\$STAGING/api/listings?limit=100000" | jq 'length'
       True positive: you never see a 429, or ?limit=100000 returns 100,000 rows.

  Other 7.7 LOGGED probes not automated here (they need YOUR test accounts and,
  for password-reset, access to the inbox that receives the email):
    - Open signup on an invite-only app (POST /auth/v1/signup).
    - Password-reset host poisoning (forged Host header) — then READ the email.
    - Login user-enumeration oracle (known-good vs known-bad; status AND timing).
  Run these by hand per dossier 7.7 against your own test identities.
EOF
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
section "${C_BOLD}==${C_RESET}" "Summary"
if [ -s "$FINDINGS_FILE" ]; then
  N="$(wc -l < "$FINDINGS_FILE" | tr -d ' ')"
  printf '%s%s finding(s)%s from SAFE/LOGGED probes against %s:\n\n' "$C_RED" "$N" "$C_RESET" "$APP"
  # CRITICAL first, then HIGH, MEDIUM, LOW.
  for sev in CRITICAL HIGH MEDIUM LOW; do
    awk -F'\t' -v s="$sev" '$2==s' "$FINDINGS_FILE" | sort -u \
      | awk -F'\t' '{ printf "  [%s] %-10s %s\n", $2, $1, $3 }'
  done
else
  printf '%sNo findings%s from the SAFE/LOGGED probes.\n' "$C_GREEN" "$C_RESET"
fi

if [ "$BRIEF" -eq 1 ]; then
cat <<EOF

${C_BOLD}A clean run proves only:${C_RESET} no secrets or source maps in the JS served here; the probed
.env/.git/debug/admin paths did not return 200; CORS did not reflect a forged origin on the
tested path; the x-middleware-subrequest header did not turn a blocked route into a 200; and
any baseline header not flagged above is present.

${C_BOLD}What a clean run does NOT prove:${C_RESET}
  1. ${C_BOLD}NOTHING about authorization / IDOR / BOLA (AUTHZ-01, AUTHZ-06).${C_RESET} External black-box probing structurally cannot find "user A can read user B's row" — that needs the two-account differential test (dossier section 6).
  2. Nothing about RLS on tables this probe never named (BAAS-01/02) — run the anon-key READ probe (T0.1) with your own project ref + anon key.
  3. Nothing about write-side flaws (mass assignment, missing write policy) — that is the 7.5 STAGING-ONLY write probe above.
  4. Nothing about rate limiting or bulk export — that is 7.8, STAGING ONLY.
  5. Only the paths in THIS script were tested; an unlisted /api/internal route is invisible to a fixed path list — mine the bundle output above.
  6. Header grading beyond presence — run the site through MDN HTTP Observatory by hand.
  (full text: re-run this command without --brief)

Finding IDs above are dossier-stable. Feed them back to the skill to get the
prioritised fix plan (section 9 triage: chain, not severity column).
EOF
else
cat <<EOF

${C_BOLD}What a clean run DOES prove:${C_RESET}
  - No secrets or source maps were in the JS this deployment served you.
  - The .env/.git/debug/admin paths probed did not return 200.
  - CORS did not reflect a forged origin on the tested API path.
  - The x-middleware-subrequest header did not turn a blocked route into a 200.
  - Your baseline security headers are present (if none were flagged).

${C_BOLD}What a clean run does NOT prove:${C_RESET}
  - ${C_BOLD}Nothing about authorization / IDOR / BOLA (AUTHZ-01, AUTHZ-06).${C_RESET} External
    black-box probing structurally cannot find "user A can read user B's row."
    That needs the two-account differential test (dossier section 6).
  - Nothing about RLS on tables this probe never named (BAAS-01/02). Run the
    anon-key READ probe (T0.1) with your own project ref + anon key.
  - Nothing about write-side flaws (mass assignment, missing write policy) —
    that is the 7.5 STAGING-ONLY write probe above.
  - Nothing about rate limiting or bulk export — that is 7.8, STAGING ONLY.
  - Only the paths in THIS script were tested. An unlisted /api/internal route
    is invisible to a fixed path list; mine the bundle output above for more.

Finding IDs above are dossier-stable. Feed them back to the skill to get the
prioritised fix plan (section 9 triage: chain, not severity column).
EOF
fi

# Exit non-zero if any finding fired, so CI / callers can gate on it.
if [ -s "$FINDINGS_FILE" ]; then exit 1; fi
exit 0
