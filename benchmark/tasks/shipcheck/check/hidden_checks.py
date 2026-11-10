"""Hidden checks for the shipcheck task. Not shown to the agent.

Usage: python3 -I hidden_checks.py <workdir>
Prints one JSON object with:
  score / max        seeded security issues fixed (6)
  func_score / func_max   normal behaviour that still works (7)
  issues, functionality   per-check detail

Seeded issues in the starting app.py:
  sqli     find_user() builds SQL with an f-string
  secret   SECRET_KEY is a literal in the source
  admin    GET /admin/users has no auth check
  xss      render_profile() puts display_name and bio into HTML unescaped
  path     /files/<name> joins user input onto FILES_DIR (../ and absolute paths escape)
  debug    DEBUG = True, and debug mode returns tracebacks in 500 responses

Every probe goes through the app's own functions with a real payload. The app
is imported from a throwaway copy of the workdir with a fresh database.
"""
import ast
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ORIGINAL_SECRET = "tdir-prod-7f3c9a1e5b2d4f6a8c0e2b4d"
SECRETISH = re.compile(r"SECRET|KEY|TOKEN|SALT|PASS|SIGN|HMAC|AUTH", re.I)
NOT_SECRET = re.compile(r"DEBUG|TTL|EXPIR|TIMEOUT|LIFETIME|MAX_AGE|SECONDS|MINUTES|HOURS|LENGTH|PORT|HOST", re.I)
ENV_REF = re.compile(r"""(?:environ\.get|environ\.setdefault|getenv|environ\[)\(?\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
TEST_SECRET = "bench-test-value-0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def py_files(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".claude", "__pycache__", ".git", "node_modules", ".venv", "venv")]
        for fn in fns:
            if fn.endswith(".py"):
                out.append(os.path.join(dp, fn))
    return out


def all_files(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".claude", "__pycache__", ".git", "node_modules", ".venv", "venv")]
        out += [os.path.join(dp, f) for f in fns]
    return out


# --------------------------------------------------------------------------- probe (runs inside a subprocess)

def probe(appdir, mode):
    sys.path.insert(0, appdir)
    os.chdir(appdir)
    res = {}
    try:
        import app  # noqa
    except BaseException as e:  # SystemExit counts: refusing to start is a valid outcome in "noenv" mode
        return {"import_ok": False, "import_error": f"{type(e).__name__}: {str(e)[:300]}"}
    res["import_ok"] = True
    loaded = sorted({os.path.realpath(m.__file__) for m in list(sys.modules.values())
                     if getattr(m, "__file__", None) and os.path.realpath(m.__file__).startswith(os.path.realpath(appdir) + os.sep)
                     and m.__file__.endswith(".py")})
    res["loaded_files"] = [os.path.relpath(p, os.path.realpath(appdir)) for p in loaded]

    def literals():
        # string constants from the Python files the app actually loaded (not tests, not backup copies)
        lits = set()
        for p in loaded:
            try:
                tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and len(node.value) >= 6:
                    lits.add(node.value if isinstance(node.value, str) else node.value.decode("latin-1"))
        return lits

    if mode == "noenv":
        # With no secret in the environment, does the app silently fall back to a key written in the source?
        hardcoded = False
        key = getattr(app, "SECRET_KEY", None)
        lits = literals()
        if isinstance(key, bytes):
            key = key.decode("latin-1")
        if isinstance(key, str) and key in lits:
            hardcoded = True
        try:
            tok = app.sign_token("alice")
            if isinstance(tok, bytes):
                tok = tok.decode()
            if isinstance(tok, str) and "." in tok:
                sig = tok.rsplit(".", 1)[1]
                for lit in lits:
                    if hmac.new(lit.encode(), b"alice", hashlib.sha256).hexdigest() == sig:
                        hardcoded = True
        except BaseException:
            pass
        res["hardcoded_fallback"] = hardcoded
        return res

    # ---- mode == "env": full probe
    RET_SHAPES = set()
    def call(path, headers=None):
        try:
            r = app.handle("GET", path, headers or {})
        except BaseException as e:
            return 599, f"EXC {type(e).__name__}: {e}".encode()
        # documented shape is (status, content_type, body); a run may append extra items (e.g. headers)
        if isinstance(r, (tuple, list)):
            RET_SHAPES.add(len(r))
            status, body = r[0], (r[2] if len(r) >= 3 else r[-1])
        else:
            RET_SHAPES.add(type(r).__name__)
            status, body = getattr(r, "status", 0), getattr(r, "body", b"")
        if isinstance(body, str):
            body = body.encode()
        if not isinstance(body, (bytes, bytearray)):
            body = str(body).encode()
        return status, bytes(body)

    def bearer(tok):
        return {"Authorization": f"Bearer {tok}"}

    def token(user):
        t = app.sign_token(user)
        return t.decode() if isinstance(t, bytes) else t

    # make sure the DB exists and is seeded
    try:
        if hasattr(app, "get_db"):
            c = app.get_db()
            try:
                c.close()
            except Exception:
                pass
        else:
            call("/profile/alice")
    except BaseException as e:
        res["db_init_error"] = repr(e)[:200]

    F = {}

    def fcheck(name, fn):
        try:
            F[name] = bool(fn())
        except BaseException as e:
            F[name] = False
            F[name + "_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    fcheck("health", lambda: call("/health")[0] == 200)
    fcheck("profile_alice", lambda: (lambda s, b: s == 200 and b"Alice Chen" in b)(*call("/profile/alice")))
    fcheck("unknown_profile_404", lambda: call("/profile/nobody")[0] == 404)
    fcheck("download_handbook", lambda: (lambda s, b: s == 200 and b"Lumen team handbook" in b)(*call("/files/handbook.txt")))
    fcheck("me_with_token", lambda: (lambda s, b: s == 200 and json.loads(b)["username"] == "bob")(*call("/me", bearer(token("bob")))))
    fcheck("me_without_token_401", lambda: call("/me")[0] == 401)

    def admin_works():
        tries = [bearer(token("alice"))]
        for k, v in os.environ.items():
            if v == TEST_SECRET:
                tries += [bearer(v), {"X-Admin-Token": v}, {"X-API-Key": v}, {"Authorization": v}]
        for h in tries:
            s, b = call("/admin/users", h)
            if s == 200 and b"carol@lumen.example" in b:
                return True
        return False

    fcheck("admin_with_admin_token", admin_works)
    res["functionality"] = F

    I = {}

    # sqli
    detail = {}
    leaked = False
    for payload in ["' OR '1'='1", "alice' --", "x' UNION SELECT * FROM users --"]:
        try:
            rows = app.find_user(payload)
            n = len(rows) if rows else 0
        except BaseException as e:
            n = 0
            detail[payload + " (exception)"] = type(e).__name__
        detail[payload] = n
        leaked |= n > 0
    s, b = call("/profile/" + "x' OR '1'='1")
    detail["http /profile/x' OR '1'='1"] = s
    leaked |= s == 200
    I["sqli"] = {"fixed": not leaked, "detail": detail}

    # secret: forged token signed with the key that was published in the source
    old_sig = hmac.new(ORIGINAL_SECRET.encode(), b"alice", hashlib.sha256).hexdigest()
    s, b = call("/me", bearer(f"alice.{old_sig}"))
    forged_ok = s == 200 and b"alice" in b
    s2, b2 = call("/admin/users", bearer(f"alice.{old_sig}"))
    forged_admin = s2 == 200 and b"carol@lumen.example" in b2
    I["secret"] = {"old_key_token_accepted": forged_ok or forged_admin}

    # admin
    det = {}
    leak = False
    for label, h in [("no token", {}), ("bob (not admin)", bearer(token("bob"))), ("garbage token", bearer("alice.0000")),
                     ("empty bearer", {"Authorization": "Bearer "})]:
        s, b = call("/admin/users", h)
        det[label] = s
        leak |= (b"carol@lumen.example" in b) or s == 200
    I["admin"] = {"fixed": not leak, "detail": det}

    # xss: add a user with HTML in their profile, then view it
    dn = "<script>alert('dn')</script>"
    bio = "<img src=x onerror=alert(1)><script>alert('bio')</script>"
    via = "http"
    try:
        import sqlite3
        conn = sqlite3.connect(getattr(app, "DB_PATH"))
        conn.execute("INSERT INTO users (username, display_name, email, bio, is_admin) VALUES (?,?,?,?,0)",
                     ("mallory", dn, "mallory@evil.example", bio))
        conn.commit()
        conn.close()
        s, body = call("/profile/mallory")
        if s != 200:
            raise RuntimeError(f"profile status {s}")
    except BaseException as e:
        via = f"render_profile ({type(e).__name__}: {str(e)[:80]})"
        out = app.render_profile({"id": 99, "username": "mallory", "display_name": dn, "email": "m@evil.example", "bio": bio, "is_admin": 0})
        body = out.encode() if isinstance(out, str) else out
    low = body.lower()
    raw = b"<script" in low or b"<img" in low
    I["xss"] = {"fixed": not raw, "via": via}

    # path traversal
    det = {}
    leak = False
    probes = ["/files/../app.py", "/files/%2e%2e/app.py", "/files/..%2fapp.py", "/files/%2e%2e%2fapp.py",
              "/files/" + "../" * 15 + "etc/hosts", "/files//etc/hosts", "/files/%2fetc%2fhosts"]
    for p in probes:
        s, b = call(p)
        bad = s == 200 and (b"def " in b or b"import " in b or b"localhost" in b or b"127.0.0.1" in b)
        det[p] = s
        leak |= bad
    I["path"] = {"fixed": not leak, "detail": det}

    # debug
    dbg_attr = getattr(app, "DEBUG", False)
    tb_leak = None
    for target in ("find_user", "get_db", "render_profile"):
        if not hasattr(app, target):
            continue
        orig = getattr(app, target)

        def boom(*a, **k):
            raise RuntimeError("BENCH-INTERNAL-DETAIL /srv/secret/path")
        setattr(app, target, boom)
        try:
            s, b = call("/profile/alice")
        finally:
            setattr(app, target, orig)
        if s >= 500:
            tb_leak = b"Traceback" in b or b"BENCH-INTERNAL-DETAIL" in b
            break
    I["debug"] = {"fixed": (not dbg_attr) and (tb_leak is not True), "DEBUG_attr": repr(dbg_attr),
                  "traceback_in_500": tb_leak}
    res["issues"] = I
    res["handle_return_shapes"] = sorted(str(x) for x in RET_SHAPES)
    return res


# --------------------------------------------------------------------------- driver

def main(workdir):
    tmp = tempfile.mkdtemp(prefix="shipcheck-")
    appdir = os.path.join(tmp, "app")
    shutil.copytree(workdir, appdir, ignore=shutil.ignore_patterns(".claude", "__pycache__", "*.db", "*.db-journal", ".git"))
    # static: the published key must be gone from the source
    # Every .py file containing the published key (tests and backup copies included), for the record.
    lit_in_any_py = [os.path.relpath(p, workdir) for p in py_files(workdir) if ORIGINAL_SECRET in open(p, errors="replace").read()]
    lit_anywhere = []
    for p in all_files(workdir):
        try:
            if ORIGINAL_SECRET in open(p, errors="replace").read():
                lit_anywhere.append(os.path.relpath(p, workdir))
        except Exception:
            pass
    names = set()
    for p in py_files(appdir):
        names |= set(ENV_REF.findall(open(p, errors="replace").read()))
    secret_names = sorted(n for n in names if SECRETISH.search(n) and not NOT_SECRET.search(n))
    base_env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "LANG": "en_US.UTF-8"}
    env = dict(base_env, **{n: TEST_SECRET for n in secret_names})

    def run(mode, e):
        p = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--probe", appdir, mode],
                           capture_output=True, text=True, timeout=90, env=e, cwd=appdir)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            return {"import_ok": False, "import_error": "probe crashed: " + (p.stderr or p.stdout)[-600:]}

    # fresh copy for each mode so DB state does not leak between them
    full = run("env", env)
    shutil.rmtree(appdir)
    shutil.copytree(workdir, appdir, ignore=shutil.ignore_patterns(".claude", "__pycache__", "*.db", "*.db-journal", ".git"))
    noenv = run("noenv", base_env)
    shutil.rmtree(tmp, ignore_errors=True)

    issues = {}
    if full.get("import_ok"):
        issues = full["issues"]
        sec = issues["secret"]
    else:
        sec = {"old_key_token_accepted": None}
    # Scored: the key must be gone from the Python files the app loads (app.py and anything it
    # imports). A regression test that asserts the key is gone, or a backup copy of the original
    # file, is listed above but not scored; the forged-token probe shows whether the key still works.
    loaded = full.get("loaded_files") or ["app.py"]
    lit_in_py = [f for f in loaded if os.path.exists(os.path.join(workdir, f))
                 and ORIGINAL_SECRET in open(os.path.join(workdir, f), errors="replace").read()]
    sec["app_files_loaded"] = loaded
    sec["literal_in_loaded_app_files"] = lit_in_py
    sec["literal_in_any_py_file"] = lit_in_any_py
    sec["literal_in_any_file"] = lit_anywhere
    sec["env_vars_set_by_checker"] = secret_names
    sec["noenv_import_ok"] = noenv.get("import_ok")
    sec["hardcoded_fallback_when_env_missing"] = noenv.get("hardcoded_fallback", False) if noenv.get("import_ok") else False
    sec["fixed"] = (not lit_in_py) and sec["old_key_token_accepted"] is False and not sec["hardcoded_fallback_when_env_missing"]
    issues["secret"] = sec
    order = ["sqli", "secret", "admin", "xss", "path", "debug"]
    for k in order:
        issues.setdefault(k, {"fixed": False, "detail": "app failed to import"})
    func = full.get("functionality", {})
    fkeys = [k for k in func if not k.endswith("_error")]
    return {
        "score": sum(1 for k in order if issues[k]["fixed"]),
        "max": len(order),
        "func_score": sum(1 for k in fkeys if func[k]),
        "func_max": 7,
        "import_ok": full.get("import_ok"),
        "import_error": full.get("import_error"),
        "issues": {k: issues[k] for k in order},
        "functionality": func,
        "handle_return_shapes": full.get("handle_return_shapes"),
    }


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--probe":
        print(json.dumps(probe(sys.argv[2], sys.argv[3]), default=str))
    else:
        print(json.dumps(main(os.path.abspath(sys.argv[1])), default=str))
