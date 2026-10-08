#!/usr/bin/env bash
# audit-repo.sh — read-only static audit of a repository for the "never-get-hacked" skill.
#
# WHAT IT DOES
#   Runs the repo-observable checks from the dossier's audit tiers, cheapest and
#   highest-signal first, and prints a stable finding ID (BAAS-01, SECRET-01, ...)
#   next to every hit so the skill can route straight to the catalog entry.
#
# GUARANTEES
#   * Read-only. It never writes to, modifies, deletes or stages anything inside the
#     target repository. Its only writes are to a private mktemp directory it removes
#     on exit.
#   * No network. No curl, no fetch, no `git fetch`, no package installs.
#   * No execution of anything in the target repo.
#   It is safe to run against a stranger's repository.
#
# USAGE
#   audit-repo.sh [path]         # defaults to .
#   audit-repo.sh --all [path]   # show every hit instead of the first 8 per finding
#   audit-repo.sh --brief [path] # ONE LINE PER FINDING. Same checks, same finding
#                                # IDs, same counts — nothing is skipped. The
#                                # false-positive test on each finding collapses to
#                                # "(fp: ...)", section prose and the long-form
#                                # NOTVULN / gap explanations collapse to one line.
#                                # Re-run the same command WITHOUT --brief to get
#                                # every word back. Use this when output budget,
#                                # not accuracy, is the constraint.
#
# EXIT CODE
#   Always 0. This is an informational tool, not a gate.

VERSION="1.0"
MAXHITS=8
BRIEF=0

# ---------------------------------------------------------------------------
# args
# ---------------------------------------------------------------------------
ROOT=""
for arg in "$@"; do
  case "$arg" in
    --all)   MAXHITS=100000 ;;
    --brief) BRIEF=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) printf 'audit-repo.sh: unknown option %s (try --help)\n' "$arg" >&2; exit 0 ;;
    *)  ROOT="$arg" ;;
  esac
done
[ -n "$ROOT" ] || ROOT="."

if [ ! -d "$ROOT" ]; then
  printf 'audit-repo.sh: %s is not a directory. Nothing audited.\n' "$ROOT" >&2
  exit 0
fi

cd "$ROOT" 2>/dev/null || { printf 'audit-repo.sh: cannot enter %s\n' "$ROOT" >&2; exit 0; }
ABSROOT=$(pwd)

TMPD=$(mktemp -d 2>/dev/null || mktemp -d -t ngh)
[ -n "$TMPD" ] || { printf 'audit-repo.sh: cannot create a temp dir\n' >&2; exit 0; }
trap 'rm -rf "$TMPD"' EXIT INT TERM HUP
FILELIST="$TMPD/files"

# ---------------------------------------------------------------------------
# colour (only on a tty, and honour NO_COLOR)
# ---------------------------------------------------------------------------
C_RST=""; C_RED=""; C_YEL=""; C_BLU=""; C_DIM=""; C_BLD=""
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RST=$'\033[0m'; C_RED=$'\033[1;31m'; C_YEL=$'\033[1;33m'
  C_BLU=$'\033[1;36m'; C_DIM=$'\033[2m'; C_BLD=$'\033[1m'
fi

N_CRIT=0; N_HIGH=0; N_MED=0; N_NOTE=0

# ---------------------------------------------------------------------------
# file inventory  (prefer rg for the listing/matching, fall back to find + grep)
# ---------------------------------------------------------------------------
PRUNE_DIRS=".git node_modules .next dist build out .venv venv __pycache__ .turbo .cache vendor coverage .svelte-kit .nuxt .output target Pods .terraform"

build_filelist() {
  # NUL-separated, paths relative to the repo root, prefixed ./
  find . \
    \( -name .git -o -name node_modules -o -name .next -o -name dist -o -name build \
       -o -name out -o -name .venv -o -name venv -o -name __pycache__ -o -name .turbo \
       -o -name .cache -o -name vendor -o -name coverage -o -name .svelte-kit \
       -o -name .nuxt -o -name .output -o -name target -o -name Pods -o -name .terraform \) -prune \
    -o -type f ! -size +2048k \
       ! -name '*.png' ! -name '*.jpg' ! -name '*.jpeg' ! -name '*.gif' ! -name '*.webp' \
       ! -name '*.ico' ! -name '*.svg' ! -name '*.pdf' ! -name '*.zip' ! -name '*.gz' \
       ! -name '*.tgz' ! -name '*.mp4' ! -name '*.mov' ! -name '*.mp3' ! -name '*.wav' \
       ! -name '*.woff' ! -name '*.woff2' ! -name '*.ttf' ! -name '*.otf' ! -name '*.eot' \
       ! -name '*.jsonl' ! -name '*.min.js' ! -name '*.min.css' ! -name '*.map' \
       -print0 2>/dev/null > "$FILELIST"
}
build_filelist
NFILES=$(tr -dc '\0' < "$FILELIST" 2>/dev/null | wc -c | tr -d ' ')
[ -n "$NFILES" ] || NFILES=0

# A repository is thousands of files; a home directory is hundreds of thousands.
# Cap rather than run for twenty minutes, and say so out loud in both the banner
# and the "did not check" list, because a silent cap is a silent gap.
FILECAP=8000
TRUNCATED=0
if [ "$NFILES" -gt "$FILECAP" ]; then
  tr '\0' '\n' < "$FILELIST" | head -n "$FILECAP" | tr '\n' '\0' > "$FILELIST.cap" 2>/dev/null \
    && mv "$FILELIST.cap" "$FILELIST" 2>/dev/null && TRUNCATED=1
fi

# rg is preferred when present, but only if it accepts the exact flag set and the
# exact regex dialect used below. A silently-rejected pattern would be a false
# negative in a security tool, so rg has to pass a probe first.
HAVE_RG=0
MATCHER="grep"
if command -v rg >/dev/null 2>&1; then
  printf 'const x = await prisma.$queryRaw("select 1"); postMessage(d, "*"); using ( true )\n' > "$TMPD/probe"
  PROBE_PAT='\$queryRaw\(|[\"'"'"']\*[\"'"'"']|using *\( *true *\)'
  if rg --no-heading --with-filename --line-number --color never --no-messages \
       -e "$PROBE_PAT" "$TMPD/probe" 2>/dev/null | grep -q 'queryRaw' \
     && rg --files-with-matches --color never --no-messages -i -e 'PRISMA' "$TMPD/probe" >/dev/null 2>&1; then
    HAVE_RG=1
    MATCHER="rg"
  else
    MATCHER="grep (rg is installed but did not pass the pattern probe)"
  fi
fi

# scan <pattern> [extra matcher flags...]   -> "./path:line:text"
scan() {
  _pat=$1; shift
  [ -s "$FILELIST" ] || return 0
  if [ "$HAVE_RG" = 1 ]; then
    xargs -0 rg --no-heading --with-filename --line-number --color never --no-messages \
      "$@" -e "$_pat" < "$FILELIST" 2>/dev/null
  else
    xargs -0 grep -I -H -n -E "$@" -e "$_pat" /dev/null < "$FILELIST" 2>/dev/null
  fi
}

# scanl <pattern> [flags...]  -> matching file paths only
scanl() {
  _pat=$1; shift
  [ -s "$FILELIST" ] || return 0
  if [ "$HAVE_RG" = 1 ]; then
    xargs -0 rg --files-with-matches --color never --no-messages "$@" -e "$_pat" < "$FILELIST" 2>/dev/null
  else
    xargs -0 grep -I -l -E "$@" -e "$_pat" /dev/null < "$FILELIST" 2>/dev/null
  fi
}

# scan_under <dir> <pattern> [flags...]
scan_under() {
  _d=$1; _pat=$2; shift 2
  [ -e "$_d" ] || return 0
  find "$_d" \( -name node_modules -o -name .git \) -prune -o -type f -print0 2>/dev/null \
    | xargs -0 grep -I -H -n -E "$@" -e "$_pat" /dev/null 2>/dev/null
}

# only keep hits under app/ src/ pages/ lib/ server/ components/ api/
in_app() { grep -E '^\./(app|src|pages|lib|server|components|api|routes|convex|functions)/'; }

count_lines() {
  if [ -z "$1" ]; then echo 0; else printf '%s\n' "$1" | grep -c . ; fi
}

exists() { [ -e "$1" ]; }

# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
section() {
  printf '\n%s%s%s\n' "$C_BLU" "=== $1 " "$C_RST"
  [ "$BRIEF" = 1 ] && return 0
  [ -n "$2" ] && printf '%s%s%s\n' "$C_DIM" "    $2" "$C_RST"
}

sev_colour() {
  case "$1" in
    CRIT) printf '%s' "$C_RED" ;;
    HIGH) printf '%s' "$C_RED" ;;
    MED)  printf '%s' "$C_YEL" ;;
    *)    printf '%s' "$C_DIM" ;;
  esac
}

bump() {
  case "$1" in
    CRIT) N_CRIT=$((N_CRIT+1)) ;;
    HIGH) N_HIGH=$((N_HIGH+1)) ;;
    MED)  N_MED=$((N_MED+1))  ;;
    *)    N_NOTE=$((N_NOTE+1)) ;;
  esac
}

# finding <SEV> <ID> <title> <hits> [caveat]
finding() {
  _sev=$1; _id=$2; _title=$3; _hits=$4; _cav=$5
  _n=$(count_lines "$_hits")
  [ "$_n" -eq 0 ] && return 0
  bump "$_sev"
  if [ "$BRIEF" = 1 ]; then
    # one line: severity, stable ID, title, then file:line for each hit.
    # The matched source text is dropped; the LOCATIONS are not.
    _locs=$(printf '%s\n' "$_hits" | grep -v '^[[:space:]]*$' | head -n "$MAXHITS" \
            | sed 's/^\([^:]*:[0-9][0-9]*\):.*$/\1/' \
            | tr '\n' ' ' | sed 's/  */ /g; s/ *$//' | cut -c1-240)
    if [ "$_n" -gt "$MAXHITS" ]; then
      _locs="$_locs +$((_n - MAXHITS)) more (--all)"
    fi
    _fp=""
    [ -n "$_cav" ] && _fp="  (fp: run without --brief for the false-positive test)"
    printf '  %s[%s]%s %s%s%s %s :: %s%s\n' \
      "$(sev_colour "$_sev")" "$_sev" "$C_RST" "$C_BLD" "$_id" "$C_RST" \
      "$_title" "$_locs" "$_fp"
    return 0
  fi
  printf '\n  %s[%s]%s %s%s%s  %s\n' \
    "$(sev_colour "$_sev")" "$_sev" "$C_RST" "$C_BLD" "$_id" "$C_RST" "$_title"
  { printf '%s\n' "$_hits" | grep -v '^[[:space:]]*$' | head -n "$MAXHITS" | cut -c1-200 | sed 's/^/        /'; } 2>/dev/null
  if [ "$_n" -gt "$MAXHITS" ]; then
    printf '        %s... and %s more (run with --all)%s\n' "$C_DIM" "$((_n - MAXHITS))" "$C_RST"
  fi
  if [ -n "$_cav" ]; then
    printf '        %s^ FALSE POSITIVE CHECK: %s%s\n' "$C_DIM" "$_cav" "$C_RST"
  fi
}

# note <ID> <title> <body-line> [brief-body]  — informational, never a defect.
# In --brief the 4th argument is printed instead of the 3rd. A NOTVULN note MUST
# supply one: a bare ID would let the agent re-report a public-by-design key as a
# leak, which is the exact failure this output exists to prevent.
note() {
  bump NOTE
  printf '\n  %s[NOTE]%s %s%s%s  %s\n' "$C_DIM" "$C_RST" "$C_BLD" "$1" "$C_RST" "$2"
  if [ "$BRIEF" = 1 ]; then
    if [ -n "${4:-}" ]; then
      printf '%s\n' "$4" | sed 's/^/        /'
    else
      printf '        %s(detail: re-run without --brief)%s\n' "$C_DIM" "$C_RST"
    fi
    return 0
  fi
  [ -n "$3" ] && { printf '%s\n' "$3" | cut -c1-200 | sed 's/^/        /'; } 2>/dev/null
}

ok() { printf '  %s[ ok ]%s %s\n' "$C_DIM" "$C_RST" "$1"; }

# ---------------------------------------------------------------------------
# base64url decode helper (for reading the role claim out of a bundled JWT)
# ---------------------------------------------------------------------------
b64url_decode() {
  _s=$(cat | tr -d '\n' | tr '_-' '/+')
  case $(( ${#_s} % 4 )) in
    2) _s="$_s==" ;;
    3) _s="$_s=" ;;
  esac
  printf '%s' "$_s" | base64 --decode 2>/dev/null \
    || printf '%s' "$_s" | base64 -D 2>/dev/null
}

# ---------------------------------------------------------------------------
# banner
# ---------------------------------------------------------------------------
if [ "$BRIEF" = 1 ]; then
  printf '%snever-get-hacked :: audit-repo.sh v%s (--brief)%s\n' "$C_BLD" "$VERSION" "$C_RST"
  printf 'target   : %s  |  %s files  |  matcher %s  |  READ-ONLY, no network\n' "$ABSROOT" "$NFILES" "$MATCHER"
  if [ "$TRUNCATED" = 1 ]; then
    printf '%sWARNING  : %s files found — only the first %s scanned. Point this at the project dir.%s\n' "$C_YEL" "$NFILES" "$FILECAP" "$C_RST"
  fi
else
printf '%s\n' "$C_BLD"
printf 'never-get-hacked :: audit-repo.sh v%s\n' "$VERSION"
printf '%s' "$C_RST"
printf 'target   : %s\n' "$ABSROOT"
printf 'files    : %s scanned (node_modules, build output, venv and .git pruned;\n' "$NFILES"
printf '           files over 2MB skipped — minified bundles and data dumps are not source)\n'
printf 'matcher  : %s\n' "$MATCHER"
printf 'mode     : READ-ONLY. no writes to the target, no network, nothing executed.\n'
if [ "$TRUNCATED" = 1 ]; then
  printf '%sWARNING  : %s files found — that is not one app repo. Only the first %s were\n' "$C_YEL" "$NFILES" "$FILECAP"
  printf '           scanned. Point this at the project directory instead.%s\n' "$C_RST"
fi
fi

# ===========================================================================
# INVENTORY — what this repo is. The lane decides which findings are even
# expressible, so it is printed before anything else.
# ===========================================================================
section "INVENTORY" "stack, lane, and where the trust boundary is"

PKG="package.json"
HAS_NODE=0;  [ -f "$PKG" ] && HAS_NODE=1
HAS_PY=0
if [ -f requirements.txt ] || [ -f pyproject.toml ] || [ -f Pipfile ] || [ -f manage.py ]; then HAS_PY=1; fi

dep_present() { [ "$HAS_NODE" = 1 ] && grep -qE "\"$1\"[[:space:]]*:" "$PKG" 2>/dev/null; }

STACK=""
dep_present "next"                 && STACK="$STACK next"
dep_present "vite"                 && STACK="$STACK vite"
dep_present "react"                && STACK="$STACK react"
dep_present "@remix-run/react"     && STACK="$STACK remix"
dep_present "@sveltejs/kit"        && STACK="$STACK sveltekit"
dep_present "nuxt"                 && STACK="$STACK nuxt"
dep_present "express"              && STACK="$STACK express"
dep_present "fastify"              && STACK="$STACK fastify"
dep_present "@supabase/supabase-js" && STACK="$STACK supabase"
dep_present "firebase"             && STACK="$STACK firebase"
dep_present "firebase-admin"       && STACK="$STACK firebase-admin"
dep_present "@clerk/nextjs"        && STACK="$STACK clerk"
dep_present "next-auth"            && STACK="$STACK next-auth"
dep_present "stripe"               && STACK="$STACK stripe"
dep_present "@prisma/client"       && STACK="$STACK prisma"
dep_present "drizzle-orm"          && STACK="$STACK drizzle"
dep_present "mongoose"             && STACK="$STACK mongoose"
dep_present "ai"                   && STACK="$STACK vercel-ai-sdk"
dep_present "openai"               && STACK="$STACK openai"
dep_present "@anthropic-ai/sdk"    && STACK="$STACK anthropic"
dep_present "socket.io"            && STACK="$STACK socket.io"
if [ "$HAS_PY" = 1 ]; then
  grep -rqiE '^[[:space:]]*(fastapi|django|flask)' requirements.txt pyproject.toml Pipfile 2>/dev/null && STACK="$STACK python"
  [ -f manage.py ] && STACK="$STACK django"
fi
[ -n "$STACK" ] || STACK=" (no package.json / requirements.txt found — static site or unknown)"
printf '  stack        :%s\n' "$STACK"

# server tier?
SERVER_TIER=""
for d in app/api pages/api src/app/api src/pages/api supabase/functions netlify/functions \
         functions api server src/server convex worker workers .netlify/functions amplify/backend; do
  [ -d "$d" ] && SERVER_TIER="$SERVER_TIER $d"
done
if [ "$HAS_PY" = 1 ]; then SERVER_TIER="$SERVER_TIER (python backend)"; fi

CLIENT_BAAS=$(scan '(supabase|createClient\(|firebase)' -i | in_app | grep -cE '\.(t|j)sx?:' 2>/dev/null)
[ -n "$CLIENT_BAAS" ] || CLIENT_BAAS=0

LANE="A"
if [ "$HAS_PY" = 1 ] && [ "$HAS_NODE" = 0 ]; then
  LANE="C"
elif [ -z "$SERVER_TIER" ] && [ "$CLIENT_BAAS" -gt 0 ]; then
  LANE="B"
elif [ -z "$SERVER_TIER" ]; then
  LANE="?"
fi

case "$LANE" in
  '?') printf '  lane         : %sundetermined%s — no server tier and no BaaS client calls found.\n' "$C_BLD" "$C_RST"
       printf '                 %sIf this is a static HTML site, the relevant reference is the stage-0\n' "$C_DIM"
       printf '                 migration path, not this audit.%s\n' "$C_RST" ;;
  A) printf '  lane         : %sA%s — server tier present:%s\n' "$C_BLD" "$C_RST" "$SERVER_TIER" ;;
  B) printf '  lane         : %sB%s — client-only SPA talking straight to a BaaS. No server module found.\n' "$C_BLD" "$C_RST"
     printf '                 %sPLAT-03: server-side validation, rate limiting, webhook verification and\n' "$C_DIM"
     printf '                 secret-holding API calls are STRUCTURALLY INEXPRESSIBLE here, not merely\n'
     printf '                 missing. Do not report them as "add a check to your API route".%s\n' "$C_RST" ;;
  C) printf '  lane         : %sC%s — Python backend:%s\n' "$C_BLD" "$C_RST" "$SERVER_TIER" ;;
esac

IS_GIT=0
if [ -d .git ] || git rev-parse --git-dir >/dev/null 2>&1; then IS_GIT=1; fi
printf '  git          : %s\n' "$([ "$IS_GIT" = 1 ] && echo 'yes (history checks enabled)' || echo 'no repo — SECRET-05 history checks skipped')"

# ===========================================================================
# TIER 0 — the fifteen-minute triage (repo-observable subset)
# ===========================================================================
section "TIER 0 — fifteen-minute triage" "if anything here fires, fix it before reading further"

# --- T0.3 / SECRET-01 / BAAS-04 : real secrets, by shape ------------------
SECRET_PAT='sb_secret_[A-Za-z0-9_-]{10,}|sk_live_[A-Za-z0-9]{10,}|rk_live_[A-Za-z0-9]{10,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|re_[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9]{20,}|xoxb-[0-9]{8,}|glpat-[A-Za-z0-9_-]{20,}|postgres(ql)?://[^:@[:space:]]+:[^@[:space:]]+@'
HITS=$(scan "$SECRET_PAT" | grep -vE '(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock):' )
finding CRIT "SECRET-01" "Secret-shaped credential literal in the repository" "$HITS" \
  "A placeholder in .env.example or a docs snippet is not a leak. Open the line: if the value is real, ROTATE AT THE PROVIDER FIRST — deleting the commit does not remediate (SECRET-05)."

# --- T0.3 / BAAS-04 : service_role, and JWTs decoded rather than guessed ---
HITS=$(scan 'service_role|SERVICE_ROLE' | grep -vE '\.md:|\.mdx:')
finding CRIT "BAAS-04" "service_role referenced — check every one of these is server-only" "$HITS" \
  "A server-only handler using the service role is not a leak, it is BAAS-04's cousin AUTHZ-08: that code path silently bypasses RLS, so every check RLS made must be rewritten by hand. The leak is only when it reaches client code or a NEXT_PUBLIC_/VITE_ var."

# Decode every bundled-looking JWT and read the role claim. This is the check that
# separates the real critical (service_role) from the loudest false positive (anon).
JWTS=$(scan 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{20,}' -o 2>/dev/null | sort -u | head -50)
SVC_JWT=""; ANON_JWT=""
if [ -n "$JWTS" ]; then
  OLDIFS=$IFS; IFS='
'
  for j in $JWTS; do
    tok=$(printf '%s' "$j" | sed 's/^.*:\([0-9]*\):*//' | grep -oE 'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+' | head -1)
    [ -n "$tok" ] || continue
    payload=$(printf '%s' "$tok" | cut -d. -f2 | b64url_decode)
    src=$(printf '%s' "$j" | cut -d: -f1,2)
    case "$payload" in
      *'"role":"service_role"'*|*'"role": "service_role"'*) SVC_JWT="$SVC_JWT$src -> role=service_role
" ;;
      *'"role":"anon"'*|*'"role": "anon"'*)                 ANON_JWT="$ANON_JWT$src -> role=anon
" ;;
    esac
  done
  IFS=$OLDIFS
fi
finding CRIT "BAAS-04" "Bundled JWT decodes to role=service_role — this key carries BYPASSRLS" "$SVC_JWT" \
  "None. A service_role JWT anywhere a browser can reach it is unconditionally critical: it is Postgres-superuser-equivalent and RLS stops applying. Rotate at Supabase, then find why it was client-side."
if [ -n "$ANON_JWT" ]; then
  note "NOTVULN-01" "Supabase anon/publishable JWT present — NOT a finding" \
"$(printf '%s' "$ANON_JWT")
This key is designed to be public (Supabase docs: safe on 'web page, mobile or
desktop app, GitHub actions, CLIs, source code'). Do NOT report it as a leak and
do NOT rotate it as remediation.
THE CONTROL THAT MUST THEREFORE EXIST: RLS enabled on every table in an exposed
schema, plus least-privilege grants. Verify with the live probe, not by guessing:
  curl \"https://<ref>.supabase.co/rest/v1/<table>?select=*&limit=3\" -H \"apikey: <publishable_key>\"
If rows come back the finding is BAAS-01 (missing RLS), never 'leaked key'." \
"$(printf '%s' "$ANON_JWT")
NOT A LEAK: Supabase designs this key to be public (safe in source, CLIs, apps).
Do not report it and do not rotate it as remediation.
REQUIRED CONTROL: RLS on every table in an exposed schema + least-privilege
grants — verify by curl; if rows come back the finding is BAAS-01, not 'leaked
key'. (curl one-liner + full wording: re-run without --brief)"
fi

# --- T0.4 / SECRET-03 + SECRET-05 : .env in the repo and in git -----------
ENVFILES=$(find . -maxdepth 3 \( -name node_modules -o -name .git \) -prune -o \
  -type f -name '.env*' ! -name '.env.example' ! -name '.env.sample' ! -name '.env.template' -print 2>/dev/null | sort)
if [ "$IS_GIT" = 1 ]; then
  TRACKED_ENV=$(git ls-files 2>/dev/null | grep -E '(^|/)\.env' | grep -vE '\.(example|sample|template)$')
  finding CRIT "SECRET-05" "A .env file is TRACKED IN GIT — it is in the history for good" "$TRACKED_ENV" \
    "Only if the file holds nothing real. Deleting the commit is not remediation: forks share an object pool, so the blob stays reachable at /commit/<hash>. GitHub's own wording: removing the secret, pushing a new commit, or deleting the repo 'do not prevent the secret from being exploited.' Rotate at the provider first."
  UNIGNORED=""
  for f in $ENVFILES; do
    if ! git check-ignore -q "$f" 2>/dev/null; then
      git ls-files --error-unmatch "$f" >/dev/null 2>&1 || UNIGNORED="$UNIGNORED$f (present, not gitignored, not yet committed)
"
    fi
  done
  finding HIGH "SECRET-03" ".env present and NOT covered by .gitignore — one 'git add .' from exposure" "$UNIGNORED" \
    "None worth arguing with. Add a .gitignore line before the next commit."
fi

# --- deployed-artifact leakage of dotfiles (repo-side signal only) --------
PUBENV=$(find . -maxdepth 4 \( -name node_modules -o -name .git \) -prune -o \
  -type f \( -path '*/public/.env*' -o -path '*/static/.env*' -o -path '*/public/*.sql' -o -path '*/public/*.bak' \) -print 2>/dev/null)
finding CRIT "SECRET-03" "Sensitive file inside a statically-served directory — it will be fetchable over HTTP" "$PUBENV" \
  "None. Everything under public/ or static/ is served verbatim. Unit 42 documented an extortion crew whose entire initial access was 'curl http://target/.env'."

# --- T0.6 / AUTHZ-10 : does the test suite ever authenticate as user B? ----
HAS_APP=0
if [ "$HAS_NODE" = 1 ] || [ "$HAS_PY" = 1 ]; then HAS_APP=1; fi
TESTDIRS=""
for d in test tests e2e __tests__ cypress playwright spec src/__tests__ app/__tests__; do
  [ -d "$d" ] && TESTDIRS="$TESTDIRS $d"
done
if [ -n "$TESTDIRS" ]; then
  DIFF_HITS=""
  for d in $TESTDIRS; do
    h=$(scan_under "$d" 'user(B|2)|otherUser|other_user|secondUser|tenantB|otherOrg|otherTenant|attacker|unauthorized' -i -l 2>/dev/null)
    DIFF_HITS="$DIFF_HITS$h"
  done
  if [ -z "$DIFF_HITS" ]; then
    finding HIGH "AUTHZ-10" "Tests exist but NONE of them authenticates as a second user" \
"searched:$TESTDIRS — no reference to a second user, tenant or attacker identity" \
      "None. Every generated test is A-reads-A's-own-data, which passes in a completely broken app. Static analysis cannot find broken access control either: run the differential harness in references/authz-verification.md."
  else
    ok "AUTHZ-10  a second identity appears in the test suite (verify it asserts DENIAL, not just success)"
  fi
elif [ "$HAS_APP" = 1 ]; then
  finding HIGH "AUTHZ-10" "No test directory at all — there is no differential authorization test" \
"looked for: test/ tests/ e2e/ __tests__/ cypress/ playwright/ spec/" \
    "None. OWASP Top 10:2025 A01 reports some form of broken access control in 100% of applications tested, and no scanner in any recommended stack can detect a missing ownership check."
fi

# --- T0.1 / BAAS-01 : migrations that create a table and never enable RLS --
SQLLIST="$TMPD/sql"
find . \( -name node_modules -o -name .git \) -prune -o -type f -name '*.sql' -print 2>/dev/null > "$SQLLIST"
if [ -s "$SQLLIST" ]; then
  ALLSQL="$TMPD/allsql"
  : > "$ALLSQL"
  while IFS= read -r f; do cat "$f" >> "$ALLSQL" 2>/dev/null; done < "$SQLLIST"
  TABLES=$(grep -oiE 'create +table +(if +not +exists +)?[A-Za-z0-9_."]+' "$ALLSQL" 2>/dev/null \
    | sed -E 's/.*[[:space:]]//; s/"//g; s/^public\.//' | tr 'A-Z' 'a-z' | sort -u | grep -E '^[a-z_][a-z0-9_]*$')
  NORLS=""
  for t in $TABLES; do
    if ! grep -qiE "alter +table +(only +)?(\"?public\"?\.)?\"?${t}\"? +enable +row +level +security" "$ALLSQL" 2>/dev/null; then
      loc=$(tr '\n' '\0' < "$SQLLIST" | xargs -0 grep -liE "create +table +(if +not +exists +)?(\"?public\"?\.)?\"?${t}\"?" /dev/null 2>/dev/null | head -1)
      NORLS="$NORLS${t}   (declared in ${loc:-a migration}, never 'enable row level security')
"
    fi
  done
  finding CRIT "BAAS-01" "Table created in SQL with no ENABLE ROW LEVEL SECURITY anywhere" "$NORLS" \
    "A table in a schema that is NOT exposed to PostgREST, or a table in a self-hosted Postgres with no REST layer, is fine. On Supabase, RLS is on by default ONLY for tables made in the Table Editor — raw SQL migrations (what an agent writes) leave it off, and Supabase's own wording is that such a table 'is readable and writable by anyone with your publishable key'."

  PERM=$(scan 'using *\( *true *\)|with +check *\( *true *\)' -i 2>/dev/null | grep -E '\.sql:')
  finding CRIT "BAAS-02" "RLS policy is a no-op: USING (true) / WITH CHECK (true)" "$PERM" \
    "A genuinely public table (published blog posts, a public directory) is a real case — but say so explicitly. Everything else: this passes an 'is RLS on?' audit with a green checkmark while authorizing everyone. Supabase advisor lint 0024_permissive_rls_policy."
else
  ok "no .sql files — BAAS-01/BAAS-02 must be checked in the Supabase SQL editor instead (see TIER 2)"
fi

# --- T0.2 / BAAS-03 : Firebase rules -------------------------------------
RULEFILES=$(find . \( -name node_modules -o -name .git \) -prune -o -type f \
  \( -name '*.rules' -o -name 'firestore.rules' -o -name 'storage.rules' -o -name 'database.rules.json' \) -print 2>/dev/null)
if [ -n "$RULEFILES" ]; then
  H=$(scan 'allow +(read|write|create|update|delete)[^;]*: *if +true' -i | grep -E '\.rules:|rules\.json:')
  finding CRIT "BAAS-03" "Firebase rule 'allow ...: if true' — test mode that was never tightened" "$H" \
    "None on a private collection. This is the Tea app shape: ~72,000 images including ID-verification selfies, then a second datastore with ~1.1M private messages."
  H=$(scan 'if +request\.auth *!= *null' | grep -E '\.rules:')
  finding HIGH "BAAS-03" "Firebase rule authorizes on 'request.auth != null' — logged-in is not authorized" "$H" \
    "Acceptable only on data every signed-in user may genuinely read. With open sign-up (AUTHN-07) an attacker mints their own token and reads everything. Also remember Firestore rules do NOT cascade to subcollections: a rule on /users/{uid} does not cover /users/{uid}/messages."
else
  ok "no Firebase rules files in the repo"
fi

# --- T0.9 / AI-01 : unauthenticated LLM or email endpoint ----------------
ROUTES=$(find app pages src/app src/pages supabase/functions netlify/functions api 2>/dev/null \
  -type f \( -name 'route.ts' -o -name 'route.js' -o -name 'route.tsx' -o -name '*.ts' -o -name '*.js' \) 2>/dev/null \
  | grep -iE '(chat|complet|generate|llm|/ai/|agent|prompt|embed|summar|transcri|image|mail|email|send|sms|otp|invite|upload|admin|webhook)' \
  | grep -vE 'node_modules|\.test\.|\.spec\.' | sort -u)
UNAUTH=""
for r in $ROUTES; do
  if ! grep -qiE 'auth|session|getUser|currentUser|clerk|requireUser|verifyJwt|verify_jwt|jwt|signature|constructEvent|rateLimit|ratelimit' "$r" 2>/dev/null; then
    UNAUTH="$UNAUTH$r
"
  fi
done
finding CRIT "AI-01" "Cost-bearing or privileged route with no auth/verification token anywhere in the file" "$UNAUTH" \
  "A route guarded by middleware.ts, or one whose auth lives in an imported wrapper, will show up here. Open it. But note AUTHZ-03: a middleware-only guard is itself a finding — CVE-2025-29927 was exactly a middleware bypass, and every Next.js route must re-check inside the handler. The default AI-SDK scaffold ships POST /api/chat with no auth, no size cap and no quota; Sysdig documented the LLMjacking resale economy for exactly that (up to ~\$46k/day of victim spend)."

# ===========================================================================
# TIER 1 — secrets and the client bundle
# ===========================================================================
section "TIER 1 — secrets and the client bundle" "credentials that are already public, or one build away from it"

# --- 2.1 / SECRET-01 : public env prefix in front of a secret name --------
# The allowlist below is subtracted from the hits BEFORE anything is reported.
# These variables are public by design (NOTVULN-01..04); listing them as findings
# is the exact failure mode that makes a builder ignore the finding that matters.
SAFE_PUBLIC_ENV='(SUPABASE_ANON_KEY|SUPABASE_PUBLISHABLE_KEY|SUPABASE_URL|PUBLISHABLE_KEY|POSTHOG_KEY|POSTHOG_HOST|SENTRY_DSN|FIREBASE_(API_KEY|AUTH_DOMAIN|PROJECT_ID|STORAGE_BUCKET|MESSAGING_SENDER_ID|APP_ID|MEASUREMENT_ID|DATABASE_URL)|MAPBOX_[A-Z_]*TOKEN|(GOOGLE_)?MAPS_API_KEY|SITE_KEY|ALGOLIA_SEARCH_[A-Z_]*KEY|GA_MEASUREMENT_ID|GTM_ID|PLAUSIBLE_DOMAIN)'
ALLHITS=$(scan '(NEXT_PUBLIC_|VITE_|REACT_APP_|EXPO_PUBLIC_|NUXT_PUBLIC_|GATSBY_|PUBLIC_)[A-Z0-9_]*(SERVICE_ROLE|SECRET|PRIVATE|_KEY|TOKEN|PASSWORD|WEBHOOK)')
H=$(printf '%s\n' "$ALLHITS" | grep -vE "$SAFE_PUBLIC_ENV")
SAFE_SEEN=$(printf '%s\n' "$ALLHITS" | grep -E "$SAFE_PUBLIC_ENV")
finding CRIT "SECRET-01" "A public build-time env prefix in front of a secret-sounding name" "$H" \
  "NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY, NEXT_PUBLIC_POSTHOG_KEY, NEXT_PUBLIC_SENTRY_DSN and the Firebase web config are all CORRECT and must not be reported (NOTVULN-01..04). The finding is a prefixed variable whose value is genuinely secret: SERVICE_ROLE, OPENAI, ANTHROPIC, RESEND, SENDGRID, STRIPE_SECRET, DATABASE_URL, JWT_SECRET, WEBHOOK signing secrets. Next.js inlines these 'at build time'; Expo says they are 'visible in plain-text in your compiled application'."

# The specific laundering path the dossier calls out.
H=$(scan '(NEXT_PUBLIC_|VITE_|EXPO_PUBLIC_)[A-Z0-9_]*(OPENAI|ANTHROPIC|CLAUDE|GEMINI|GROQ|MISTRAL|REPLICATE|HUGGING|RESEND|SENDGRID|POSTMARK|TWILIO|DATABASE_URL|MONGO|REDIS)')
finding CRIT "SECRET-02" "A server-only provider credential behind a client-visible prefix" "$H" \
  "None. This is the debugging loop the dossier names as the dominant root cause: process.env.OPENAI_API_KEY is undefined in a client component, the model adds the prefix to clear the error, and the key becomes a literal in a public chunk. The Next.js Vite-migration guide literally instructs 'Change all environment variables with the VITE_ prefix to NEXT_PUBLIC_'."

# --- SECRET-02 : server SDK dragged into the browser ---------------------
H=$(scan 'dangerouslyAllowBrowser')
finding CRIT "SECRET-02" "dangerouslyAllowBrowser — a server-only SDK is being constructed in the browser" "$H" \
  "None in shipped code. The flag exists to let the SDK run client-side; the API key it is constructed with then ships to every visitor. A test file or a local script is the only benign case, and it still puts the key on disk."

# --- 2.2 / SECRET-04 : source maps -------------------------------------
H=$(scan 'productionBrowserSourceMaps *: *true|sourcemap: *true|sourceMap: *true|devtool: *.source-map' | grep -E '(next|vite|webpack|rollup|nuxt|svelte|astro)\.config|\.config\.(t|j)s:')
finding HIGH "SECRET-04" "Production source maps enabled in the build config" "$H" \
  "A dev-only conditional (mode === 'development') on the same line is fine — read it. Otherwise sourcesContent holds your verbatim original files: Sentry Security's worked example recovered an unreferenced updateUserData() taking email/password/accessToken and chained it to account takeover. 'hidden' only strips the trailing comment — you must also delete the .map files from the deployed artifact."
MAPS=$(find . -maxdepth 5 \( -name node_modules -o -name .git \) -prune -o -type f -name '*.js.map' -print 2>/dev/null | head -20)
finding MED "SECRET-04" "Built .js.map files present on disk — check they are not in the deploy artifact" "$MAPS" \
  "A local build directory that is gitignored and never uploaded is not a finding. Confirm with: curl -sI \"\$APP/_next/static/chunks/main.js.map\" — you want a 404."

# --- 2.4 / SECRET-07 + AGENT-01 : secrets in agent context files ---------
AGENTPATHS=""
for p in .claude .cursor .mcp.json CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md .aider.conf.yml; do
  [ -e "$p" ] && AGENTPATHS="$AGENTPATHS $p"
done
if [ -n "$AGENTPATHS" ]; then
  H=""
  for p in $AGENTPATHS; do
    h=$(scan_under "$p" 'sk-[A-Za-z0-9]{20,}|sk_live_|sb_secret_|ghp_[A-Za-z0-9]{20,}|postgres(ql)?://[^:@[:space:]]+:[^@[:space:]]+@|eyJ[A-Za-z0-9_-]{20,}\.eyJ' 2>/dev/null)
    H="$H$h"
  done
  finding HIGH "SECRET-07" "Credential material inside an AI agent config file" "$H" \
    "A documented placeholder is fine; a real value is not. .claude/settings.local.json stores previously-approved shell commands verbatim, so a token you once typed inline in a curl persists in a file no default ignore list covers. GitGuardian measured a 3.2% secret-leak rate on Claude Code-assisted commits vs a 1.5% baseline, and 24,008 unique secrets in MCP config files (2,117 valid, 8.8%)."

  if [ "$IS_GIT" = 1 ]; then
    NOTIG=""
    for p in .claude/settings.local.json .mcp.json .cursor; do
      [ -e "$p" ] || continue
      git check-ignore -q "$p" 2>/dev/null || NOTIG="$NOTIG$p (not gitignored)
"
    done
    finding MED "AGENT-01" "Agent config not gitignored — treat these files as executable code, not documentation" "$NOTIG" \
      ".mcp.json is meant to be committed when it is deliberately shared; the point is that whoever can write it can steer your agent. settings.local.json holding approved commands should not be shared."
  fi
fi

# --- 2.5 / AGENT-02 : invisible-Unicode instructions ---------------------
# Byte-level check: the LC_ALL=C grep path is used deliberately here, since this
# must match raw UTF-8 byte sequences rather than decoded characters.
UNI_PAT=$(printf '\342\200[\213-\217]|\342\200[\252-\256]|\342\201[\240-\244]|\363\240[\200-\201]')
UNIHITS=""
for p in $AGENTPATHS .github; do
  [ -e "$p" ] || continue
  h=$(find "$p" -type f -print0 2>/dev/null | LC_ALL=C xargs -0 grep -l -E -e "$UNI_PAT" /dev/null 2>/dev/null)
  UNIHITS="$UNIHITS$h
"
done
UNIHITS=$(printf '%s' "$UNIHITS" | grep -v '^$')
finding HIGH "AGENT-02" "Invisible Unicode (zero-width / bidi / tag characters) in an agent instruction file" "$UNIHITS" \
  "Emoji sequences and some CJK text legitimately use zero-width joiners, and a UTF-8 BOM is benign — open the file with 'cat -v' before concluding. Pillar Security's Rules File Backdoor (disclosed to Cursor 2025-02-26, GitHub 2025-03-12) hid instructions in exactly these characters: invisible to a human AND in the PR diff, read by the model, payload injected an attacker-domain script and told the agent to suppress mention of the change."

# --- 2.6 / CLIENT-04 : session credentials in localStorage ---------------
H=$(scan 'localStorage\.setItem' | in_app | grep -iE 'token|jwt|session|auth|credential|apikey|api_key')
finding MED "CLIENT-04" "A session credential is being written to localStorage" "$H" \
  "Supabase's JS client stores its session in localStorage by default and that is the vendor's own design — report it as a blast-radius property, not a bug, and pair it with 'do you have an XSS sink?' (INJECT-01). The real point: any JS on the origin reads localStorage, so one XSS becomes a bearer credential replayable offline until expiry, whereas an httpOnly cookie reduces the same attacker to session-riding inside the victim's browser. httpOnly does NOT defend against browser extensions — Chrome's isolated world separates the JS heap, not the DOM."

# --- 2.3 / SECRET-06 : Docker and CI leakage ----------------------------
if [ -f Dockerfile ] || ls Dockerfile* >/dev/null 2>&1; then
  H=$(scan '^ *(ARG|ENV) +[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|DSN)' | grep -iE 'dockerfile')
  finding HIGH "SECRET-06" "Secret-named ARG/ENV in a Dockerfile — the value persists in image metadata forever" "$H" \
    "An ARG with no default that is passed at build time still lands in 'docker history'. A build-time-only secret belongs in a BuildKit secret mount (RUN --mount=type=secret), not ARG. 'RUN rm .env' only writes a whiteout in a later layer; the earlier layer still has the file."
  H=$(scan '^ *COPY \. \.' | grep -iE 'dockerfile')
  finding MED "SECRET-06" "'COPY . .' in a Dockerfile — .env, .git and local creds go into the image" "$H" \
    "Harmless if a .dockerignore excludes .env and .git — check for one. Truffle Security found 9% of ~400,000 scanned Docker Hub images contained leaked secrets (52,107 private keys), plus 289,000 GitHub Dockerfiles using this exact line."
fi
H=$(scan 'echo +.*\$\{?[A-Z_]*(SECRET|TOKEN|KEY|PASSWORD)' | grep -E '^\./\.github/')
finding MED "SECRET-06" "A CI step echoes a secret-named variable" "$H" \
  "GitHub Actions masks only the exact secret literal, so 'echo \"\$TOKEN\" | base64' prints in cleartext to a world-readable log. If the line only echoes a non-secret it is noise — read it."

# --- NOTVULN block : keys that are SUPPOSED to be public -----------------
PUBKEYS=""
PUBKEYS_BRIEF=""
if [ -n "$SAFE_SEEN" ]; then
  SAFE_LIST=$(printf '%s\n' "$SAFE_SEEN" | grep -oE '(NEXT_PUBLIC_|VITE_|REACT_APP_|EXPO_PUBLIC_|NUXT_PUBLIC_|GATSBY_|PUBLIC_)[A-Z0-9_]+' | sort -u | tr '\n' ' ')
  PUBKEYS="${PUBKEYS}Public-prefixed env vars   -> $SAFE_LIST
                              (excluded from the SECRET-01 hit list above on purpose)
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Public-prefixed env vars -> $SAFE_LIST(excluded from SECRET-01 on purpose)
"
fi
if [ -n "$(scan 'sb_publishable_[A-Za-z0-9_-]{10,}')" ]; then
  PUBKEYS="${PUBKEYS}Supabase publishable key   -> control that must exist: RLS on every table in an exposed schema + least-privilege grants (verify by curl, see BAAS-01)
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Supabase publishable key -> CONTROL: RLS on every table in an exposed schema + least-privilege grants (verify by curl; BAAS-01).
"
fi
if [ -n "$(scan 'pk_(test|live)_[A-Za-z0-9]{10,}')" ]; then
  PUBKEYS="${PUBKEYS}Stripe/Clerk pk_ key       -> control: every amount, price and entitlement decided server-side (PAY-03). sk_/rk_/sk_org_ are the opposite and are never safe client-side.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Stripe/Clerk pk_ key -> CONTROL: every amount, price and entitlement decided server-side (PAY-03). sk_/rk_/sk_org_ are never safe client-side.
"
fi
if [ -n "$(scan 'phc_[A-Za-z0-9]{20,}')" ]; then
  PUBKEYS="${PUBKEYS}PostHog phc_ project key   -> control: it is distinct from PostHog personal API keys, project secret keys and the feature-flags secure key, all of which ARE secret. Confirm you did not ship one of those.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}PostHog phc_ project key -> CONTROL: confirm it is not a PostHog personal API key, project secret key or feature-flags secure key — those ARE secret.
"
fi
if [ -n "$(scan 'https://[a-z0-9]+@[a-z0-9.-]*sentry\.io')" ]; then
  PUBKEYS="${PUBKEYS}Sentry public DSN          -> control: it accepts events only. Consider inbound filters and rate limits.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Sentry public DSN -> CONTROL: it accepts events only; consider inbound filters and rate limits.
"
fi
if [ -n "$(scan 'pk\.eyJ[A-Za-z0-9_-]{10,}')" ]; then
  PUBKEYS="${PUBKEYS}Mapbox public token pk.    -> control: URL restrictions (max 100 URLs per token) so a scraped token cannot be billed from another origin. sk. tokens are server-only.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Mapbox public token pk. -> CONTROL: URL restrictions (max 100 URLs/token) so a scraped token cannot be billed elsewhere. sk. tokens are server-only.
"
fi
if [ -n "$(scan 'AIza[A-Za-z0-9_-]{30,}')" ]; then
  PUBKEYS="${PUBKEYS}AIza... key                -> AMBIGUOUS BY PREFIX. A Firebase web apiKey only identifies the project and is safe (control: Security Rules + App Check). A Google Maps browser key is safe but is a BILLING liability without HTTP-referrer + API restrictions. A GEMINI DEVELOPER API KEY HAS THE IDENTICAL PREFIX and Firebase's docs say it 'should never be included in your code or configuration files'. Check what the key is restricted to in Google Cloud, not the prefix.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}AIza... key -> AMBIGUOUS BY PREFIX: Firebase web apiKey is safe (CONTROL: Security Rules + App Check); a Maps browser key is safe but a BILLING liability without referrer+API restrictions; a GEMINI developer key has the SAME PREFIX and is secret. Check the Google Cloud restriction, not the prefix.
"
fi
if [ -n "$(scan '(authDomain|storageBucket|messagingSenderId) *:')" ]; then
  PUBKEYS="${PUBKEYS}Firebase web config        -> control: Security Rules + App Check. Firebase's docs: these keys 'do not need to be treated as secrets'.
"
  PUBKEYS_BRIEF="${PUBKEYS_BRIEF}Firebase web config -> CONTROL: Security Rules + App Check. Firebase docs: these 'do not need to be treated as secrets'.
"
fi
if [ -n "$PUBKEYS" ]; then
  note "NOTVULN-01..04" "Public-by-design keys are present. THIS IS NOT A FINDING." \
"$(printf '%s' "$PUBKEYS")
Governing rule: flag the missing control, not the visible key. Reporting one of
these as a 'critical leak' is the fastest way to make the real finding get ignored
- the dossier's own fact-checkers caught the research filing the Moltbook breach
under 'leaked secrets' when the exposed credential was a publishable key that was
supposed to be there and the real defect was missing RLS." \
"$(printf '%s' "$PUBKEYS_BRIEF")
Governing rule: flag the MISSING CONTROL, not the visible key. Reporting one of
these as a 'critical leak' is the fastest way to make the real finding get
ignored. (sources + full wording: re-run without --brief)"
fi

# ===========================================================================
# TIER 2 — the data layer
# ===========================================================================
section "TIER 2 — the data layer" "RLS, rules, grants, and the writes a policy cannot express"

# --- BAAS-05 : policies that trust user-writable metadata ---------------
H=$(scan 'user_metadata|raw_user_meta_data' | grep -iE '\.sql:|supabase/')
finding CRIT "BAAS-05" "SQL references user_metadata — if this is inside a policy, users can grant themselves the role" "$H" \
  "A trigger copying raw_user_meta_data into a server-controlled column ON INSERT ONLY is a legitimate pattern; a policy reading it is not. Any user can run supabase.auth.updateUser({data:{role:'admin'}}) from the browser console. Nastier variant: on SSO/SAML logins the IdP's group claims land in the same user-writable bag, so 'check the IdP group claim in RLS' is self-defeating. Advisor lint 0015_rls_references_user_metadata."

# --- BAAS-06 : SECURITY DEFINER views and functions ---------------------
H=$(scan 'security +definer' -i | grep -E '\.sql:')
finding HIGH "BAAS-06" "SECURITY DEFINER function/view — it runs as its creator and bypasses the caller's RLS" "$H" \
  "SECURITY DEFINER is the correct tool for a deliberately-privileged operation, and Supabase's own helper functions use it. It is a finding when the body has no explicit authorization check of its own, or when 'search_path' is not pinned. Views are worse by default: a view without 'security_invoker = on' runs as postgres and is REST-exposed."
H=$(scan 'create +(or +replace +)?view' -i | grep -E '\.sql:')
if [ -n "$H" ]; then
  NOINV=$(printf '%s\n' "$H" | grep -viE 'security_invoker')
  finding HIGH "BAAS-06" "View created without 'with (security_invoker = on)' — views default to running as their creator" "$NOINV" \
    "The setting may be applied in a later ALTER VIEW statement — grep for it. The worst instance is a view over auth.users: Splinter lint 0002_auth_users_exposed, 'a public security definer view referencing auth.users exposes all user records to all API users'."
fi
H=$(scan 'auth\.users' | grep -E '\.sql:')
finding CRIT "BAAS-06" "SQL references auth.users — a public view over it exposes every user record" "$H" \
  "A trigger on auth.users or a foreign key to auth.users(id) is normal and correct. The finding is a VIEW in the public or graphql_public schema whose definition selects from auth.users."

# --- BAAS-07 : grants that were never revoked ---------------------------
H=$(scan 'grant +(all|select|insert|update|delete)' -i | grep -iE '\.sql:' | grep -iE 'anon|authenticated|public')
finding MED "BAAS-07" "Explicit grant to anon/authenticated — policies do not take grants back" "$H" \
  "Supabase's default grants already exist, so an explicit GRANT is often just making the default visible. It matters because PostgREST mirrors grants onto HTTP verbs: Supabase's own wording is 'adding policies doesn't take those grants back'. The real fix for column-level immutability is REVOKE UPDATE table-wide then GRANT UPDATE(col-list)."

# --- BAAS-08 / AUTHZ-05 : mass assignment ------------------------------
H=$(scan '\.\.\.(req\.body|request\.body|body|data|formData|payload|input)\b' | in_app)
finding CRIT "AUTHZ-05" "A request object is being spread straight into a write" "$H" \
  "A spread of an already-validated 'parsed.data' from Zod is correct — check what the identifier actually holds. Otherwise POST {\"role\":\"admin\",\"credits\":999999} sets those columns. Note for Lane B: this is BAAS-08 and RLS CANNOT fix it — a correct owner-scoped UPDATE policy still allows PATCH {\"role\":\"admin\"} on your own row, because WITH CHECK sees only the new row and there is no OLD in a policy."
H=$(scan '\.update\(|\.upsert\(' | in_app | grep -iE 'role|is_admin|isAdmin|credits|balance|plan|tier|is_premium|isPro|subscription')
finding CRIT "BAAS-08" "A client-reachable update touches a privilege or balance column" "$H" \
  "Server-side code that owns this write (a webhook handler, an admin route with its own check) is the correct place for it. The finding is a client-side .update() or a route that takes the column value from the request. Three real controls: REVOKE UPDATE then GRANT UPDATE(col-list); a BEFORE UPDATE trigger forcing new.role := old.role; or move privileged attributes to an entitlements table with no client write policy. Firestore equivalent: request.resource.data.role == resource.data.role."

# --- BAAS-09 / AUTHZ-07 : identity taken from the request --------------
H=$(scan '(body|req\.body|request\.body|params|searchParams|query)[\.\[][\"'"'"']?(user_?[Ii]d|userid|owner_?id|account_?id|tenant_?id|org_?id)')
finding CRIT "AUTHZ-07" "Identity read out of the request instead of the session" "$H" \
  "An admin route that legitimately acts on another user's id is a real case — it needs its own authorization check, which is then what you audit. The lethal shape is the '??' fallback: 'const userId = session?.user?.id ?? body.userId' authenticates nobody the moment the session is absent."
H=$(scan '\?\? *(body|req\.body|request\.body|params|searchParams)')
finding CRIT "AUTHZ-07" "'??' fallback from session identity to request-supplied identity" "$H" \
  "None. This is fail-open authentication written to make an error stop."

# --- BAAS-10 : storage, realtime, edge functions -----------------------
H=$(scan 'public: *true|getPublicUrl|ACL: *[\"'"'"']public-read')
finding HIGH "BAAS-10" "Public storage bucket / public object URL" "$H" \
  "A bucket that genuinely holds public assets (avatars you intend to be public, marketing images) is correct. The finding is user-uploaded private content — the Tea breach was ~72,000 images including ID-verification selfies and driver's licences in a bucket anyone could list."
H=$(scan 'verify_jwt *= *false')
finding CRIT "BAAS-10" "Edge function with verify_jwt = false — it is an open, unauthenticated endpoint" "$H" \
  "A Stripe/webhook receiver MUST have verify_jwt = false, because the caller is Stripe and has no Supabase JWT — but it must then verify the provider signature itself (PAY-01). Check for constructEvent in the same function before reporting."
H=$(scan '\.channel\(' | in_app | grep -v 'private')
finding MED "BAAS-10" "Realtime channel subscribed without private/authorized mode" "$H" \
  "Broadcast of genuinely public state (a live visitor count, a public leaderboard) is fine. Realtime authorization is opt-in: a public channel delivers every change to every subscriber regardless of RLS on the underlying read path."
H=$(scan 'SERVICE_ROLE|service_role' | grep -E '^\./supabase/functions/')
finding HIGH "AUTHZ-08" "Edge function uses the service role — RLS is off for every query in it" "$H" \
  "This is often the correct and necessary design. It is a finding only if the function does not do its own ownership check, because the service key carries BYPASSRLS and every check RLS used to make must now be written by hand. A green pgTAP suite proves nothing about this route."

# --- Lane B structural note --------------------------------------------
if [ "$LANE" = "B" ]; then
  note "PLAT-03" "Lane B: some defenses are inexpressible here, not missing" \
"There is no server module in this repo, so server-only DAL, Zod at the trust
boundary, @upstash/ratelimit, import 'server-only', Stripe webhook verification
and Turnstile siteverify cannot be implemented at all — the attacker curls the
BaaS REST API with the public key and none of this app's JS is in the path.
PostgREST hands them a query builder, not just parameters: select=, eq/gt/like/
in/is/fts filters, order=, an unfiltered GET returning the full table, and
resource embedding (?select=*,actors(*)) which pivots through foreign keys into
tables whose RLS was forgotten.
Seven triggers that END the client-only architecture — any single YES means it
cannot be made correct, only improved: (1) any secret-authenticated third-party
API; (2) anything outside your app must POST to you; (3) a value on the user's
own row the user must not set; (4) any per-invocation cost; (5) a rule depending
on state the caller may not read; (6) email beyond the built-in provider's
2/hour; (7) per-user rate limits on data writes." \
"No server module: server-only DAL, Zod at the boundary, rate limiting, webhook
verification and Turnstile are STRUCTURALLY INEXPRESSIBLE here, not missing — do
not report them as 'add a check to your API route'. PostgREST gives the attacker
a query builder (filters, order, unfiltered GET, ?select=*,actors(*) embedding
that pivots into tables whose RLS was forgotten). Seven triggers that END the
client-only architecture: re-run without --brief for the list."
fi

# ===========================================================================
# TIER 3 — application code, class by class
# ===========================================================================
section "TIER 3 — application code" "the classes an LLM writes wrong by default"

# --- 4.1 / INJECT-13 : no schema validation at the trust boundary -------
H=$(scan 'as string' | in_app | grep -iE 'formdata|searchparams|params|req\.|request\.|json\(\)')
finding HIGH "INJECT-13" "'as string' on request-derived data — a compile-time assertion that enforces nothing at runtime" "$H" \
  "'as const' and an assertion on a value already parsed upstream are fine — read the line above. Otherwise this single gap underlies NoSQL operator injection, prototype pollution, mass assignment, ReDoS and much of the XSS. Fix: zod .strict() (rejects unknown keys instead of silently stripping), explicit .max() on every string, safeParse, early return, and only parsed.data reaching the DB call."
H=$(scan 'await (req|request)\.json\(\)' | grep -v 'safeParse\|\.parse(')
finding HIGH "INJECT-13" "req.json() with no parse on the same line" "$H" \
  "The parse is very often on the NEXT line — this check cannot see it. Confirm before reporting. The finding is a handler where the raw object flows into a DB call."

# --- 4.2 / INJECT-02 : SQL injection through the escape hatches ---------
H=$(scan 'queryRawUnsafe|executeRawUnsafe|sql\.raw\(|knex\.raw\(|sequelize\.query\(')
finding CRIT "INJECT-02" "Raw SQL escape hatch in use" "$H" \
  "A hardcoded constant string passed to it is safe. The finding is any user input reaching an identifier. Prisma documents that 'variables cannot be used for identifiers such as column names, table names or database names', so a dynamic ORDER BY forces the unsafe API — the fix is a hardcoded allowlist map, never escaping."
H=$(scan '\$queryRaw\(|\$executeRaw\(')
finding CRIT "INJECT-02" "\$queryRaw( with PARENTHESES — a string argument, not the safe tagged template" "$H" \
  "None if it really is a paren call. This is the subtle one: \$queryRaw\`...\` with backticks is the safe tagged-template form; \$queryRaw(...) with parentheses is a function call taking a string. Prisma's caution about string building attaches to \$queryRaw/\$executeRaw themselves, not only to the Unsafe twins."
H=$(scan 'sql\.identifier\(')
finding MED "INJECT-02" "Drizzle sql.identifier() — check the Drizzle version" "$H" \
  "Fine on a patched version. CVE-2026-39356 (CVSS 7.5, <=0.45.1 / <=1.0.0-beta.19, fixed 0.45.2 / 1.0.0-beta.20): escapeName did not double the quote delimiter, so id\"; DROP TABLE users; -- broke out."

# --- 4.3 / INJECT-01 : XSS ---------------------------------------------
H=$(scan 'dangerouslySetInnerHTML|\.innerHTML *=|document\.write\(|v-html|\{\@html' | in_app)
UNSAN=$(printf '%s\n' "$H" | grep -viE 'DOMPurify|sanitize')
finding CRIT "INJECT-01" "HTML sink with no sanitizer on the same line" "$UNSAN" \
  "NOTVULN-09: dangerouslySetInnerHTML wrapped in a real sanitizer is the correct fix, not a residual vulnerability, and grepping the API name alone is mostly noise. A build-time constant is also fine. The real findings are an unsanitized __html sink, a REGEX 'sanitizer', rehype-raw without rehype-sanitize AFTER it in the same array, or an out-of-date DOMPurify (pin >= 3.2.7; CVE-2025-15599 is unpatched on 2.x). Veracode measured a 13.53% pass rate for CWE-80 in 2025 across 100+ models — their worst class."
H=$(scan 'rehype-raw|rehypeRaw')
if [ -n "$H" ]; then
  SAN=$(scan 'rehype-sanitize|rehypeSanitize')
  if [ -z "$SAN" ]; then
    finding CRIT "INJECT-01" "rehype-raw is used and rehype-sanitize is nowhere in the repo" "$H" \
      "None. rehype-raw re-enables raw HTML inside markdown; without rehypeSanitize after it in the same plugin array, any markdown you render (including model output — AI-04) is an HTML injection sink."
  fi
fi
H=$(scan 'href=\{[^\"'"'"'/]' | in_app)
finding HIGH "INJECT-01" "href={...} built from a variable — React does NOT block javascript: URLs" "$H" \
  "An internal route constant or a template literal starting with '/' is fine. The finding is a URL that can come from user or model input. Validate the scheme against an allowlist of http/https/mailto before rendering."
H=$(scan 'dangerouslyAllowSVG')
finding HIGH "INJECT-01" "dangerouslyAllowSVG in the Next.js image config" "$H" \
  "None worth defending. SVG is XML parsed by the HTML/JS engine, so an SVG served from your own origin is stored XSS."

# --- 4.4 / INJECT-04, INJECT-05, INJECT-06 -----------------------------
H=$(scan 'path\.join\(' | in_app | grep -iE 'req|params|body|filename|file\.|upload|name')
finding CRIT "INJECT-04" "path.join() with request-derived input — join normalizes but does NOT confine" "$H" \
  "A join of two server constants is fine. path.join('/safe','../../evil.js') resolves to '/evil.js'. CVE-2026-32731 (@apostrophecms/import-export <=3.5.2, CVSS 9.1) is exactly fs.createWriteStream(path.join(exportPath, header.name)) with no canonical-path check. Fix: path.resolve, then path.relative(base, p), rejecting '', '..'-prefixed, or absolute results."
H=$(scan 'child_process|execSync\(|exec\(|spawnSync\(' | in_app)
finding CRIT "INJECT-06" "Child-process execution in application code" "$H" \
  "A hardcoded command with no interpolation is fine, and execFile/spawn with an argument ARRAY is the correct shape. exec() spawns /bin/sh -c, so an interpolated filename carrying ';', '|', '\$()' or backticks executes — and quoting is not a fix, since \$(...) and backticks survive double quotes."
H=$(scan 'shell: *true')
finding CRIT "INJECT-06" "shell: true — reintroduces the shell into spawn/execFile" "$H" \
  "None. It converts the safe argument-array form back into a shell command line."
H=$(scan '(file|upload)\.(type|mimetype)|contentType: *(file|req)' | in_app)
finding HIGH "INJECT-05" "Upload validated on the client-supplied Content-Type / file.type" "$H" \
  "Fine as a UX pre-filter alongside a real server-side check. OWASP: Content-Type 'is provided by the user, and as such cannot be trusted'. Validate by sniffing magic bytes, re-encode images, store outside the web root or on a separate origin, and never serve an uploaded SVG from your app origin (FileRise CVE-2025-66403 is stored XSS via an uploaded SVG)."

# --- 4.5 / INJECT-11 : races on credits, coupons and quotas ------------
H=$(scan '(credits|balance|quota|tokens_left|redeemed|usage_count)' -i | in_app | grep -E 'findUnique|findFirst|\.single\(|select\(|SELECT ')
finding HIGH "INJECT-11" "Read-check-write on a balance — a TOCTOU race the whole population ships" "$H" \
  "A read for display only is fine. The finding is the shape 'if (user.credits > 0) { ...expensive call...; update(credits - 1) }' — fifty parallel POSTs all read credits=1 and all proceed. Invisible in single-request testing, which is all a vibe coder does. Fix: one atomic conditional statement, UPDATE users SET credits = credits - \$1 WHERE id = \$2 AND credits >= \$1 RETURNING credits, treating zero rows as insufficient, plus CHECK (credits >= 0) and UNIQUE(coupon_id, user_id) for one-time actions."

# --- 4.6 / INJECT-12 : insecure randomness -----------------------------
H=$(scan 'Math\.random\(\)' | grep -iE 'token|session|secret|otp|reset|invite|code|password|nonce|salt|id')
finding HIGH "INJECT-12" "Math.random() used for something that looks like a credential" "$H" \
  "Math.random() for a UI animation, a shuffled demo list or a cache-buster is fine — that is most of the hits, so read each one. V8's own blog: xorshift128+ with 128 bits of state, 'still not cryptographically secure', and algebraically invertible from a handful of consecutive outputs. The attack: request several reset tokens yourself, recover the state, predict the victim's token. Fix: crypto.randomUUID() or crypto.getRandomValues()."

# --- 4.7 / PAY-01, PAY-02 : webhooks -----------------------------------
WEBHOOKFILES=$( { scan 'webhook' -l -i 2>/dev/null
                  find . \( -name node_modules -o -name .git \) -prune -o -type f -print 2>/dev/null | grep -i webhook
                } | grep -iE '(app|src|pages|api|supabase|netlify|functions)/' \
                  | grep -E '\.(ts|tsx|js|jsx|mjs|py|rb|go)$' \
                  | grep -vE '\.test\.|\.spec\.|\.d\.ts$' | sort -u)
if [ -n "$WEBHOOKFILES" ]; then
  NOVERIFY=""
  for f in $WEBHOOKFILES; do
    grep -qiE 'constructEvent|verifyHeader|Webhook\(|svix|verify.*signature|createHmac|timingSafeEqual' "$f" 2>/dev/null \
      || NOVERIFY="$NOVERIFY$f
"
  done
  finding CRIT "PAY-01" "A file named for webhooks with no signature verification in it" "$NOVERIFY" \
    "A client-side file that merely mentions the word, or a type definition, is noise — check it is really the receiving handler. Stripe states the risk verbatim: without verification 'an attacker could send fake webhook events... to trigger actions like fulfilling orders, granting account access'."
fi
H=$(scan 'express\.json\(\)|bodyParser\.json\(\)' | in_app)
finding MED "PAY-02" "express.json() present — it must NOT be mounted before the webhook route" "$H" \
  "Ordinary API routes need it. Signature verification needs the RAW body, so a global express.json() mounted first mutates the bytes, the HMAC never matches, and the usual 'fix' is deleting verification. Mount express.raw({type:'application/json'}) on the webhook path before the global JSON parser."
H=$(scan '(hmac|signature|digest|hash)' -i | in_app | grep -E '(!==|===)' | grep -viE 'timingSafeEqual')
finding HIGH "PAY-02" "A signature/HMAC compared with === or !==" "$H" \
  "A comparison against a constant string, or an equality check on something that is not a secret, is noise. === short-circuits at the first differing byte, reducing brute force from 256^N to 256xN. Use crypto.timingSafeEqual on equal-length buffers. Keep Stripe's default 5-minute tolerance — 'Don't use a tolerance value of 0' — and during a secret roll iterate ALL v1= signatures, since Stripe sends one per active secret for up to 24 hours."

# --- 4.8 / PAY-06 : entitlement enforced only in the UI ----------------
H=$(scan '(isPro|is_pro|isPremium|is_premium|plan ===|tier ===|hasSubscription)' | in_app | grep -E '\.(tsx|jsx|vue|svelte):')
finding HIGH "PAY-06" "Entitlement checked in a component — that is a UI gate, not authorization" "$H" \
  "Correct and necessary for rendering. It is a finding only if the corresponding server route does not repeat the check: route names are string literals in the JS bundle, so {user.isPro && ...} hiding a button does nothing to POST /api/generate. Fix: one requireEntitlement(feature) helper reading status + current_period_end from YOUR DB as the first line of every gated handler."
H=$(scan 'checkout\.session\.completed')
if [ -n "$H" ]; then
  DRIFT=$(scan 'customer\.subscription\.(updated|deleted)|invoice\.payment_failed|charge\.(refunded|dispute)')
  if [ -z "$DRIFT" ]; then
    finding HIGH "PAY-05" "Only checkout.session.completed is handled — subscription state will drift permanently" "$H" \
      "None. Handling only the first event means isPro is never set back to false: subscribe once, charge back, keep the product forever. Store Stripe's status verbatim plus current_period_end, not a boolean, and handle customer.subscription.*, invoice.paid, invoice.payment_failed, charge.refunded and charge.dispute.*. Stripe gives NO ordering guarantee, so guard writes with AND updated_from_event_created <= event.created."
  fi
fi
H=$(scan '(amount|price|unit_amount|quantity) *[:=] *(req|body|request|params|searchParams)')
finding CRIT "PAY-03" "Price or amount taken from the client" "$H" \
  "A quantity the user legitimately chooses is fine IF the unit price is looked up server-side. The finding is any monetary value that originates in the browser."

# --- 4.9 / RT-01, RT-02, RT-04 : realtime ------------------------------
H=$(scan 'cors: *\{' | in_app | grep -iE 'socket|io\(')
finding HIGH "RT-01" "Socket.IO cors option — it does NOT protect the WebSocket transport" "$H" \
  "None, and the reasoning matters: Socket.IO's docs state verbatim that 'WebSocket connections are not subject to CORS restrictions'. The cors option governs only the long-polling transport, so cors:{origin:'*'} (the AI's reflex fix for a CORS error) is non-protective on ws. The WebSocket handshake is a plain HTTP GET carrying the session cookie and RFC 6455 only says a server MAY check Origin. Use the transport-independent allowRequest."
H=$(scan 'io\.use\(|socket\.on\(' | in_app)
finding MED "RT-02" "Socket.IO handshake middleware / event handlers — authorization must be re-checked per event" "$H" \
  "Handshake middleware is correct and necessary; the finding is when it is the ONLY check. Socket.IO middleware runs 'only once per connection', so an attacker authenticates legitimately then sends {\"event\":\"join\",\"room\":\"org:other-tenant\"}. CVE-2026-70490 (Open WebUI) is exactly this: the WS route reimplemented authentication inline and silently dropped the role check."
H=$(scan '(searchParams\.get\(|query\.)[\"'"'"']?(userId|user_id|token|session)' | in_app)
finding HIGH "RT-04" "Identity or token taken from the query string" "$H" \
  "A public, non-identifying filter parameter is fine. curl -N '/api/stream?userId=<victim>' tails the victim's feed; and middleware runs once at stream open while the stream lives for minutes, so revocation never lands. URLs also land in access logs, referrers and browser history."

# --- 4.10 / CLIENT-01, CLIENT-02, INJECT-10 : origins ------------------
H=$(scan 'addEventListener\( *[\"'"'"']message' | in_app)
finding HIGH "CLIENT-01" "A 'message' listener — every one must check event.origin against strict equality" "$H" \
  "A listener that checks event.origin on the next line is correct — read it. Without the check, any page that can open or frame yours can drive it."
H=$(scan 'postMessage\([^)]*[\"'"'"']\*[\"'"'"']')
finding HIGH "CLIENT-01" "postMessage(..., '*') — the message is readable by any origin currently in that frame" "$H" \
  "Fine for data that is genuinely public. Name the exact target origin instead."
H=$(scan 'origin\.(indexOf|startsWith|endsWith|includes)|new RegExp\([^)]*origin')
finding HIGH "CLIENT-02" "Origin validated by substring or regex" "$H" \
  "None. indexOf('yourapp.com') matches yourapp.com.evil.tld; endsWith matches notyourapp.com; an unanchored regex with unescaped dots matches yourappXcom. Any origin comparison that is not strict equality or Set membership is a finding."
H=$(scan 'origin: *true|origin: *[\"'"'"']\*[\"'"'"']|Access-Control-Allow-Origin[\"'"'"']? *[,:] *[\"'"'"']\*')
finding HIGH "INJECT-10" "Wildcard or reflected CORS origin" "$H" \
  "NOTVULN-11: a CORS error in the console is the browser doing its job, and a correctly-scoped CORS policy is not a vulnerability. CORS is also not authorization — curl ignores it entirely. The findings are origin REFLECTION with credentials (cors({origin:true, credentials:true}) reflects any Origin and PortSwigger's PoC exfiltrates the victim's authenticated response), substring matching, and a whitelisted 'null', which is forgeable from a sandboxed data: iframe."

# --- 4.11 / INFRA-05 : verbose errors and debug surface ---------------
H=$(scan '(e|err|error)\.(stack|message)' | in_app | grep -E 'json\(|res\.send|res\.json|NextResponse|new Response')
finding HIGH "INFRA-05" "An exception message or stack is being returned in an HTTP response" "$H" \
  "Returning a message you wrote yourself is fine; returning the caught exception is not. This is a stock AI pattern, written deliberately because it makes the human-AI debug loop work, then never removed. It leaks the failing SQL (a schema map for a follow-up injection), connection strings and file paths. Log the detail server-side with a correlation id; return the id."
DEBUGROUTES=$(find app/api pages/api src/app/api src/pages/api 2>/dev/null -type d 2>/dev/null | grep -iE 'debug|seed|test|internal|_admin|migrate|reset')
finding HIGH "INFRA-05" "A debug/seed/internal route directory exists" "$DEBUGROUTES" \
  "It may be guarded — open it. These routes are written to unblock development and are almost never removed, and their names are guessable."
H=$(scan 'introspection: *true|graphiql|swagger-ui|swaggerUi\.serve|docs_url')
finding MED "INFRA-05" "API documentation / GraphQL introspection surface" "$H" \
  "NOTVULN-05: schema enumeration is not itself the vulnerability and hiding it is not a fix — it matters only when the underlying objects lack authorization. Report the missing authorization, not the introspection. FastAPI serves /docs and /openapi.json by default; set docs_url=None in production if the API is not public."
H=$(scan 'console\.log\((req|request|session|user|body)\b|JSON\.stringify\(req')
finding MED "OPS-06" "A whole request/session/user object is being logged" "$H" \
  "Debug logging in a local script is fine. OWASP's never-log list: session identification values, access tokens, authentication passwords, database connection strings, encryption keys, payment data, sensitive PII. Logs, error trackers and analytics are a second, uncontrolled copy of your data. Conversely, auth failures and authorization denials are usually not logged AT ALL, so incidents cannot be scoped (OPS-01)."

# --- AUTHZ-01 / AUTHZ-03 / AUTHZ-04 / AUTHN-01 -------------------------
H=$(scan 'findUnique\(\{ *where: *\{ *id|\.eq\([\"'"'"']id[\"'"'"'], *(params|req|body|searchParams)')
finding CRIT "AUTHZ-01" "Object fetched by id alone — no ownership predicate inside the query" "$H" \
  "Fine when the row is genuinely public or the ownership predicate is on the next line — read it. UUIDs are NOT authorization: they stop enumeration, not leakage, and one leaked id is full access. A very common partial fix checks ownership on read and forgets it on write. Fix: put the predicate INSIDE the query, findFirst({where:{id, orgId: user.orgId}}), not findUnique plus an if. Return 404, not 403."
if [ -f middleware.ts ] || [ -f middleware.js ] || [ -f src/middleware.ts ]; then
  note "AUTHZ-03" "middleware.ts exists — a perimeter guard is necessary but never sufficient" \
"Next.js middleware runs before the route but is not the route. CVE-2025-29927 was
a middleware bypass via a crafted header; the family did not end there. Every
route handler, every Server Action and every data function must re-check identity
and ownership itself. Same for a layout: a layout guard does not protect the page
below it, and it does not protect the route handler at all." \
"Middleware runs before the route, it is not the route (CVE-2025-29927 bypassed it
via a crafted header, and the family did not end there). Every route handler,
Server Action, data function and page below a guarded layout must re-check
identity and ownership itself."
fi
H=$(scan '^ *[\"'"'"']use server[\"'"'"']')
if [ -n "$H" ]; then
  SAFILES=$(printf '%s\n' "$H" | cut -d: -f1 | sort -u)
  NOAUTH=""
  for f in $SAFILES; do
    grep -qiE 'auth|session|getUser|currentUser|clerk|requireUser' "$f" 2>/dev/null || NOAUTH="$NOAUTH$f
"
  done
  finding CRIT "AUTHZ-04" "'use server' file with no identity check — Server Actions are public POST endpoints" "$NOAUTH" \
    "A file whose actions are all genuinely public (a newsletter signup) is a real case, and it still needs rate limiting. Otherwise: every exported Server Action gets a stable public id in the bundle and is callable by anyone with curl. Not importing it into a component does not make it unreachable."
fi
H=$(scan 'getSession\(\)' | in_app)
finding HIGH "AUTHN-01" "getSession() — in server code this trusts a cookie the client can write" "$H" \
  "In client components getSession() is the normal API. The finding is server-side use: use getUser() (Supabase) or the equivalent that revalidates against the auth server, because the cookie payload is attacker-controlled."

# --- AUTHN-07 / ABUSE-01 : signup and rate limiting -------------------
RL=$(scan 'ratelimit|rateLimit|rate-limit|Ratelimit|express-rate-limit|Arcjet|arcjet|turnstile|recaptcha|hcaptcha' -i)
if [ -z "$RL" ] && [ "$LANE" = "A" ] && [ "$HAS_APP" = 1 ]; then
  finding HIGH "ABUSE-01" "No rate limiting or bot-check library appears anywhere in the repo" \
"searched for: @upstash/ratelimit, express-rate-limit, arcjet, turnstile, recaptcha, hcaptcha" \
    "A platform-level WAF rule or a Cloudflare rate-limiting rule configured in a dashboard will not appear in the repo — ask before reporting. Otherwise: no rate limit on login is credential stuffing, and no rate limit on a cost-bearing route is denial of wallet. In-memory counters do not survive serverless; the limiter must be shared state."
fi

# --- 4.12 / PY-* : Python backends -------------------------------------
if [ "$HAS_PY" = 1 ]; then
  H=$(scan 'debug *= *True')
  finding CRIT "PY-01" "debug=True — Flask's debug console is unauthenticated RCE; Django's traceback dumps every setting" "$H" \
    "A local dev entrypoint that is never the production command is the benign case, but the pattern of shipping it is exactly the finding. Flask's own docs call the debugger 'a major security risk' and the PIN is derivable offline from uuid.getnode() and /etc/machine-id given any file-read or SSRF primitive. Django's settings docs: the DEBUG traceback includes 'all the currently defined Django settings (from settings.py)' — database credentials and custom-named API keys."
  H=$(scan 'SECRET_KEY *= *os\.environ\.get\([^)]*,')
  finding CRIT "PY-04" "SECRET_KEY with a fallback default — this fails OPEN" "$H" \
    "None. A missing env var in production silently yields a publicly known key that underwrites all sessions, all CookieStorage messages and all password-reset tokens. Use os.environ['SECRET_KEY'] so a missing value crashes the boot."
  H=$(scan 'fields *= *[\"'"'"']__all__[\"'"'"']')
  finding HIGH "PY-04" "fields = '__all__' — accepts a POST parameter for every editable field" "$H" \
    "Django's own docs say this 'has led to serious exploits on major websites (e.g. GitHub)'. It is a time bomb: correct when written, vulnerable the moment someone adds is_premium to the model. List fields explicitly."
  H=$(scan 'yaml\.load\(|pickle\.loads\(|pickle\.load\(|torch\.load\(')
  finding CRIT "PY-03" "Deserializing untrusted bytes" "$H" \
    "yaml.safe_load and a torch.load with weights_only=True are the safe forms — check which one this is. Otherwise these are arbitrary code execution by design, not a bug."
  H=$(scan 'shell *= *True')
  finding CRIT "PY-07" "subprocess with shell=True" "$H" \
    "A fully hardcoded command string is survivable; any interpolation is command injection. Pass an argument list instead."
  H=$(scan 'render_template_string\(')
  finding CRIT "PY-07" "render_template_string() — server-side template injection into Jinja2" "$H" \
    "Safe only if the template is a constant. With user input it is RCE, not just XSS."
  H=$(scan 'ALLOWED_HOSTS *= *\[[^]]*\*')
  finding HIGH "PY-04" "ALLOWED_HOSTS = ['*'] — Host-header attacks and cache poisoning become possible" "$H" \
    "A platform that terminates and rewrites Host for you narrows this, but it is still the wrong default. Password-reset links built from the Host header are the classic consequence (AUTHN-04)."
  H=$(scan 'requests\.(get|post|put|delete)\(' | grep -v 'timeout')
  finding MED "PY-07" "requests call with no timeout — the default is to wait forever" "$H" \
    "The timeout may be set via a session object — check. Without one, a slow upstream ties up a worker until the process is exhausted."
  H=$(scan 'app\.run\(')
  finding HIGH "PY-08" "app.run() — the Flask/Django dev server is not a production server" "$H" \
    "Fine in a __main__ guard used only locally. In production it should be gunicorn/uvicorn behind a real proxy, not bound to 0.0.0.0 as root."
fi

# ===========================================================================
# TIER 4 — supply chain, CI, and agent configuration
# ===========================================================================
section "TIER 4 — supply chain, CI, agent config" "the code you did not write, running as you"

if [ "$HAS_NODE" = 1 ]; then
  # --- SUPPLY-01 : install-time execution -------------------------------
  H=$(scan '\"(preinstall|postinstall|prepare|prepublish)\" *:' | grep -E '^\./package\.json:')
  finding MED "SUPPLY-01" "This package.json runs an install-time script" "$H" \
    "A husky 'prepare' hook is normal. The point is the mechanism: npm preinstall/install/postinstall/prepare run arbitrary shell as your user for EVERY package in the transitive tree, not just yours. Microsoft's first-line mitigation for the May 2026 typosquat campaign is literally 'npm install --ignore-scripts'."
  if [ -f .npmrc ]; then
    grep -qE 'ignore-scripts *= *true' .npmrc 2>/dev/null \
      && ok "SUPPLY-01  .npmrc sets ignore-scripts=true" \
      || finding MED "SUPPLY-01" ".npmrc exists but does not set ignore-scripts=true" ".npmrc" \
           "Some packages genuinely need their build step (native modules), so this is a deliberate trade — but it should be a decision, not a default. Shai-Hulud escalated from postinstall (wave 1, Sept 2025, 500+ packages) to preinstall in wave 2 (Nov 24 2025: 796 packages / 1,092 versions / ~20M weekly downloads)."
  else
    finding MED "SUPPLY-01" "No .npmrc — install scripts from the whole transitive tree run as your user" \
"(no .npmrc in the repo root)" \
      "Many teams set this globally in ~/.npmrc instead — check there before reporting. Add 'ignore-scripts=true' to the repo .npmrc so it applies to everyone and to CI."
  fi

  # --- SUPPLY-06 : install cooldown ------------------------------------
  COOLDOWN=$(grep -rlE 'minimumReleaseAge|min-release-age|cooldown:|minimumReleaseAgeStrict' \
    .npmrc package.json pnpm-workspace.yaml renovate.json .github/dependabot.yml .renovaterc* 2>/dev/null)
  if [ -z "$COOLDOWN" ]; then
    finding HIGH "SUPPLY-06" "No install cooldown configured — you will install a compromised version within minutes of its publication" \
"checked: .npmrc, package.json, pnpm-workspace.yaml, renovate.json, .github/dependabot.yml" \
      "None, and this is the control that actually works: npm audit structurally cannot flag a package published minutes ago. A 24-hour cooldown would have blocked both Shai-Hulud waves and all 18 chalk/debug versions (live ~2.5 hours). Options: pnpm minimumReleaseAge (default 1440 minutes since v11), npm min-release-age in .npmrc, Dependabot's cooldown block, Renovate's minimumReleaseAge. Prepare the escape hatch first: exclude by EXACT VERSION, never by package name — minimumReleaseAgeExclude: ['next@16.2.11'] — because under a real 9.8 RCE the panic response is deleting the cooldown permanently. Note Dependabot's default 3-day cooldown does NOT apply to security updates."
  else
    ok "SUPPLY-06  an install cooldown is configured ($COOLDOWN)"
  fi

  # --- SUPPLY-07 : lockfile discipline ---------------------------------
  if [ ! -f package-lock.json ] && [ ! -f pnpm-lock.yaml ] && [ ! -f yarn.lock ] && [ ! -f bun.lockb ] && [ ! -f bun.lock ]; then
    finding HIGH "SUPPLY-07" "No lockfile — every install resolves fresh and can differ from the one you tested" \
"checked: package-lock.json, pnpm-lock.yaml, yarn.lock, bun.lockb" \
      "A library published to npm may deliberately not commit one; an application always should. Without it, CI installs a different tree than your machine did."
  fi
  if [ -f package-lock.json ]; then
    H=$(grep -oE '"resolved": *"[^"]*"' package-lock.json 2>/dev/null | grep -v 'registry.npmjs.org' | sort -u)
    finding MED "SUPPLY-07" "Lockfile resolves packages from a non-npmjs source" "$H" \
      "A deliberate private registry or a git dependency you chose is fine. An unexpected host is dependency confusion or a substituted tarball."
  fi
  H=$(scan '\"overrides\"' | grep -E '^\./package\.json:')
  if [ -n "$H" ]; then
    ALIAS=$(grep -A20 '"overrides"' package.json 2>/dev/null | grep 'npm:')
    finding MED "SUPPLY-07" "An 'overrides' block aliases a package name to a different package" "$ALIAS" \
      "Overrides are a legitimate way to force a patched transitive version. The finding is an 'npm:' alias silently redirecting a trusted name to something else."
  fi

  # --- SUPPLY-02 : known worm payload artefacts -------------------------
  WORM=$(find . -maxdepth 6 \( -name .git -o -name node_modules \) -prune -o -type f \
    \( -name 'setup_bun.js' -o -name 'bun_environment.js' -o -name 'shai-hulud-workflow.yml' -o -name 'discussion.yaml' \) -print 2>/dev/null)
  finding CRIT "SUPPLY-02" "A file matching a known Shai-Hulud payload name is present" "$WORM" \
    "'discussion.yaml' is a plausible innocent filename — open it before panicking. The others are not. If these are real, treat every credential that has ever been on this machine as compromised and rotate at the provider."
fi

# --- SUPPLY-03 : GitHub Actions ---------------------------------------
if [ -d .github/workflows ]; then
  H=$(scan_under .github/workflows 'pull_request_target')
  finding CRIT "SUPPLY-03" "pull_request_target — runs with repository secrets in the context of the base branch" "$H" \
    "It is the correct trigger for a workflow that must label or comment on a fork PR AND checks out nothing from the fork. It is critical the moment it checks out or executes PR content. Nx's own s1ngularity postmortem names three converging conditions: a PR-title workflow on pull_request_target echoing \${{ github.event.pull_request.title }} unsanitized into a run: step, default read/write Actions permissions, and workflow_dispatch enabled."
  H=$(scan_under .github/workflows '\$\{\{ *github\.event\.' | grep -viE '^\S+: *[0-9]+: *#')
  finding HIGH "SUPPLY-03" "github.event.* interpolated directly into a workflow — attacker-controlled text becomes shell" "$H" \
    "Interpolation inside an 'env:' block and then referencing \$VAR in the script is the SAFE form and will also appear here — check which one this is. A PR title, branch name or issue body interpolated into a run: step is code execution."
  H=$(scan_under .github/workflows 'uses: *[^ ]+@v?[0-9]')
  finding HIGH "SUPPLY-03" "Action pinned to a mutable tag instead of a 40-character commit SHA" "$H" \
    "Pinning to a tag is what almost every repo does and what the docs show, so this fires everywhere — report it as hardening, not as a live compromise. tj-actions/changed-files (CVE-2025-30066, CVSS 8.6, CISA KEV) proved the mechanism: an attacker re-pointed existing tags at a malicious commit that scanned Runner Worker memory for secrets and printed them into world-readable logs. 23,000+ repos affected, and 'uses: action@v44' became malicious with ZERO change on the consumer side. Fix: npx -y pinact@latest run."
  NOPERM=""
  for wf in .github/workflows/*.yml .github/workflows/*.yaml; do
    [ -f "$wf" ] || continue
    grep -qE '^ *permissions:' "$wf" 2>/dev/null || NOPERM="$NOPERM$wf
"
  done
  finding MED "SUPPLY-03" "Workflow with no explicit 'permissions:' block — inherits the repository default" "$NOPERM" \
    "If the repository default is already read-only this is much less severe — check Settings > Actions > Workflow permissions. Declare 'permissions: contents: read' at the top and widen per-job."
  H=$(scan_under .github/workflows 'actions/cache')
  if [ -n "$H" ]; then
    note "SUPPLY-03" "Actions cache is in use — never run untrusted code in a workflow that shares it" \
"Ultralytics (Dec 2024) shows why provenance alone is insufficient: draft PRs with
branch names containing a shell payload gave code execution in a job SHARING the
Actions cache with the trusted publishing workflow, so the legitimate publish job
built and signed a poisoned artifact. Valid provenance, clean public repo." \
"Ultralytics (Dec 2024): a draft-PR branch name carrying a shell payload got code
execution in a job SHARING the Actions cache with the trusted publish workflow,
which then built and signed a poisoned artifact — valid provenance, clean repo.
Never run untrusted code in a workflow that shares the cache."
  fi
fi

# --- SUPPLY-08 : curl | bash -------------------------------------------
H=$(scan 'curl[^|]*\| *(sudo )?(ba)?sh|wget[^|]*\| *(sudo )?(ba)?sh')
finding MED "SUPPLY-08" "curl-pipe-to-shell in a script, Dockerfile or README" "$H" \
  "It is how half the tooling ecosystem is installed, so treat it as a decision to review rather than a defect. The content is fetched at run time and is not what you reviewed; the server can serve different bytes to curl than to a browser."

# --- AGENT-03 / AGENT-05 : MCP and agent permissions -------------------
MCPFILES=""
for p in .mcp.json .cursor/mcp.json .vscode/mcp.json; do [ -f "$p" ] && MCPFILES="$MCPFILES $p"; done
if [ -n "$MCPFILES" ]; then
  H=$(grep -nE '\-y|@latest' $MCPFILES /dev/null 2>/dev/null)
  finding HIGH "AGENT-03" "MCP server launched with 'npx -y' or '@latest' — it re-fetches the newest version on every launch" "$H" \
    "None; this is a rug-pull channel, not a hypothetical. The postmark-mcp backdoor arrived exactly this way: versions 1.0.0-1.0.15 were clean at ~1,500 weekly downloads, then v1.0.16 on 2025-09-17 added one line BCC'ing every outgoing email to an attacker domain. No CVE was assigned because it is a behavioural backdoor, not a code flaw, so NO CVE-BASED SCANNER COULD EVER HAVE CAUGHT IT. Use 'npx --no --' or an exact-pinned local devDependency."
  H=$(grep -nE 'mcp\.supabase\.com' $MCPFILES /dev/null 2>/dev/null)
  if [ -n "$H" ]; then
    BAD=$(printf '%s\n' "$H" | grep -vE 'read_only=true.*project_ref=|project_ref=.*read_only=true')
    finding CRIT "AGENT-03" "Supabase MCP server configured without read_only=true AND project_ref=<dev project>" "$BAD" \
      "The 2025-vintage --read-only / --project-ref CLI-flag guidance is stale; the hosted server at https://mcp.supabase.com/mcp is configured by URL query parameters. Supabase's own guidance leads with 'Don't connect to production' and names prompt injection as 'the primary attack vector unique to LLMs'. read_only blocks the write half but NOT exfiltration, which is why dev-project scoping is the primary control."
  fi
fi
H=$(scan 'dangerously-skip-permissions|--yolo|--trust-all-tools|bypassPermissions')
finding HIGH "AGENT-05" "An unattended-permissions agent flag is committed to the repo" "$H" \
  "A documented one-off in a README is different from a CI job that runs it. The Nx s1ngularity payload invoked the victim's locally installed AI CLIs with exactly these flags (claude --dangerously-skip-permissions -p, gemini --yolo -p, q chat --trust-all-tools --no-interactive) to enumerate secrets."

if [ "$HAS_PY" = 1 ]; then
  H=$(grep -rlE 'exclude-newer|uploaded-prior-to|--require-hashes' pyproject.toml requirements*.txt uv.toml pip.conf 2>/dev/null)
  if [ -z "$H" ]; then
    finding HIGH "PY-02" "No PyPI install cooldown or hash pinning — and there is no --ignore-scripts equivalent for pip" \
"checked: pyproject.toml, requirements*.txt, uv.toml, pip.conf" \
      "None. CPython's site module executes lines in a .pth file that start with 'import ', 'at every Python startup, regardless of whether a particular module is actually going to be used'. PyPI's own incident report: malicious litellm and telnyx releases 'ran on install, harvesting sensitive credentials'; litellm==1.82.8 shipped litellm_init.pth stealing env vars, AWS/GCP/Azure creds, SSH keys and kube configs — live 2h32m, over 119,000 downloads (GHSA-98x5-vq43-vc5p). Controls: [tool.uv] exclude-newer = \"P3D\" or pip 26.1's uploaded-prior-to; hashed lockfiles (uv lock / pip-compile --generate-hashes); pip install --require-hashes. pip-audit documents its own limit — it 'is not a static code analyzer' and 'cannot defend against malicious packages'."
  fi
fi

# ===========================================================================
# SUMMARY
# ===========================================================================
TOTAL=$((N_CRIT + N_HIGH + N_MED))
section "SUMMARY" ""
printf '  %sCRITICAL %s%s   %sHIGH %s%s   %sMEDIUM %s%s   %snotes %s%s\n' \
  "$C_RED" "$N_CRIT" "$C_RST" "$C_RED" "$N_HIGH" "$C_RST" \
  "$C_YEL" "$N_MED" "$C_RST" "$C_DIM" "$N_NOTE" "$C_RST"
if [ "$BRIEF" = 1 ]; then
  printf '  %s findings across %s files. Each is a CHECK that fired, not a confirmed exploit.\n' "$TOTAL" "$NFILES"
  printf '  %sOrder fixes by CHAIN, not by the severity column%s: INFRA-05 verbose error -> NOTVULN-05\n' "$C_BLD" "$C_RST"
  printf '  PostgREST OpenAPI root maps every table -> BAAS-01 one table missing RLS -> full export\n'
  printf '  with the publishable key. Two of those three links are "not findings" alone.\n'
else
printf '  %s findings across %s files. Every count above is a CHECK that fired, not a\n' "$TOTAL" "$NFILES"
printf '  confirmed exploit — each one carries the false-positive test that decides it.\n'
printf '\n'
printf '  %sOrder the fixes by CHAIN, not by this severity column.%s Every real breach in the\n' "$C_BLD" "$C_RST"
printf '  research record is a chain: verbose error reveals the ORM and schema (INFRA-05)\n'
printf '  -> the PostgREST OpenAPI root maps every table (NOTVULN-05) -> one table missing\n'
printf '  RLS (BAAS-01) -> full export with the publishable key. No authentication anywhere\n'
printf '  in that chain, and two of the three links are "not findings" on their own.\n'
fi

# ===========================================================================
# WHAT THIS DID NOT CHECK
# ===========================================================================
section "WHAT THIS SCRIPT DID NOT CHECK" "a silent gap reads as a clean bill of health, so here they are"
if [ "$BRIEF" = 1 ]; then
cat <<'GAPSBRIEF'
  1. AUTHORIZATION — static analysis structurally cannot find broken access control (AUTHZ-10); OWASP Top 10:2025 A01: some form of it in 100% of apps tested, 1,839,701 occurrences. THE #1 FAILURE CLASS AND THIS SCRIPT IS BLIND TO IT. -> references/authz-verification.md
  2. THE LIVE DEPLOYMENT — no network request was made: anon-key reads, /.env and /.git over HTTP, public previews, source maps, stale deployments, headers, unauthenticated LLM endpoints. -> scripts/probe-live.sh <url> --i-own-this
  3. THE SHIPPED BUNDLE — build output pruned and >2MB files skipped; a secret absent from source can be inlined into the build. -> scripts/audit-bundle.sh
  4. BUSINESS LOGIC — prices, quotas, refunds, trials, referrals, role transitions. No grep expresses "the user should not be able to do this."
  5. THE DATABASE ITSELF — RLS state, policy predicates, grants, views and bucket visibility live in Postgres; migrations only show intent. -> toolkit section 3.1 SQL + Supabase Dashboard > Advisors > Security lint
  6. GIT HISTORY — working tree only. A secret removed in a later commit is still in history and reachable through forks (SECRET-05). -> gitleaks git -v .  |  trufflehog git file://. --results=verified
  7. DEPENDENCY VULNERABILITIES — no CVE database consulted, nothing installed. "0 vulnerabilities" from npm audit is a filtered view (SUPPLY-05); Dependabot auto-dismisses dev-scoped alerts on public repos.
  8. ACCOUNTS AND BILLING — MFA, session/token inventory, spend caps with an automatic ACTION not an alert (ABUSE-04 / OPS-08), backup restore tests. Dashboard checks; no script can do them.
  (each item's full text: re-run this command without --brief)
GAPSBRIEF
printf '\n'
exit 0
fi
cat <<'GAPS'
  1. AUTHORIZATION.  Static analysis structurally cannot find broken access
     control (AUTHZ-10): it requires knowing the intended policy, which is not
     in the code. OWASP Top 10:2025 A01 reports some form of broken access
     control in 100% of applications tested, across 1,839,701 occurrences.
     THIS IS THE #1 REAL-WORLD FAILURE CLASS AND THIS SCRIPT IS BLIND TO IT.
     Run the differential harness: two users, two tenants, replay every request
     as owner / other-tenant / anonymous, and diff the responses.
     -> references/authz-verification.md

  2. THE LIVE DEPLOYMENT.  This script makes no network request, so it cannot
     tell you whether your data is actually readable right now. Not checked:
     whether the anon key returns rows (BAAS-01), whether /.env or /.git/HEAD
     serve over HTTP (SECRET-03), whether preview deployments are public and
     pointed at production (INFRA-01), whether source maps are fetchable
     (SECRET-04), whether old deployments still serve the vulnerable build
     (INFRA-07), security headers (INFRA-09), or whether an LLM endpoint
     answers a cookie-less POST (AI-01).
     -> scripts/probe-live.sh <url> --i-own-this   (only against what you own)

  3. THE SHIPPED BUNDLE.  Build output was pruned and files over 2MB were
     skipped, so nothing here read your actual deployed JavaScript. Source is
     not the artifact: a secret can be absent from the repo and present in the
     build output because the build inlined it.
     -> scripts/audit-bundle.sh

  4. BUSINESS LOGIC.  Whether prices, quotas, refunds, trials, referrals and
     role transitions can be driven to a state you did not intend. No grep
     expresses "the user should not be able to do this."

  5. THE DATABASE ITSELF.  RLS state, policy predicates, grants, views and
     bucket visibility live in Postgres, not in this repo. The SQL in section
     3.1 of the toolkit (and the Supabase Dashboard > Advisors > Security lint
     set) is the authority; migrations only show what was intended.

  6. GIT HISTORY.  Only the working tree and the tracked-file list were read.
     A secret removed in a later commit is still in the history and still
     reachable through forks (SECRET-05). Run: gitleaks git -v . and
     trufflehog git file://. --results=verified

  7. DEPENDENCY VULNERABILITIES.  No CVE database was consulted and nothing
     was installed. Note that "0 vulnerabilities" from npm audit is a filtered
     view (SUPPLY-05), and Dependabot auto-dismisses dev-scoped alerts by
     default on public repos without notifying you (they still run with full
     filesystem, network and secret access on your CI runner).

  8. ACCOUNTS AND BILLING.  MFA on your GitHub/Supabase/Vercel/Stripe/domain
     accounts, session and token inventory, spend caps with an automatic
     ACTION rather than an alert (ABUSE-04 / OPS-08), and backup restore
     tests. These are dashboard checks and no script can do them for you.
GAPS

printf '\n'
exit 0
