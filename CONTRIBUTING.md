# Contributing

Thanks for wanting to help. Bug reports, skill suggestions, doc fixes and installer fixes are all welcome. For a bug or an idea, the forms on the [Issues tab](https://github.com/mppyxx/claude-power-pack/issues/new/choose) are the quickest way in.

## Suggest a skill

You can fill in the Skill suggestion form, or add the skill yourself in a pull request:

1. **Check the licence.** The pack only copies skills under an open licence that allows sharing, like MIT, Apache-2.0, ISC or BSD.
2. **Read the whole skill,** scripts included. Skills run with full agent permissions. A skill that downloads and runs code from unknown places, sends data somewhere it doesn't explain, or hides what it does won't be added.
3. **Add the folder** to `skills/`, or to `extras/` if it's niche or overlaps a skill that's already in the pack. Its `SKILL.md` needs front matter with a `name` that matches the folder name, and a `description`.
4. **List it in `docs/SKILLS.md`** in the right section: the name in backticks, what it does, and what it needs before it works.
5. **Credit the author** in `THIRD_PARTY_NOTICES.md` with the source repo and licence, and put the licence text in `licenses/` as `owner__repo.txt`. If the skill ships its own licence file inside its folder, point to that instead.
6. **Update the skill count** if it changed. It's in README.md, CLAUDE.md, docs/SKILLS.md, docs/HOW-CLAUDE-WORKS.md, llms.txt, a few images in `assets/`, and `EXPECTED_SKILLS` in `.github/workflows/ci.yml`. Searching the repo for the old number finds the text ones.

**No open licence?** Then the skill can't be copied here, but the installer can still fetch it from its author. Add a line to `skills-upstream.txt` with the skill name and the GitHub repo:

```text
skill-name owner/repo
```

Add the word `extra` at the end of the line if it should only install with `--extras`. Then list it in `docs/SKILLS.md` as usual, and add a row to `THIRD_PARTY_NOTICES.md` that says "none published".

## Test your change

These are the same commands CI runs on Ubuntu and macOS (see `.github/workflows/ci.yml`). Run them from the repo folder.

**Syntax and ShellCheck**

```bash
bash -n install.sh
bash -n tools/verify.sh
python3 -m py_compile tools/merge_settings.py
shellcheck -S error install.sh tools/verify.sh
```

No ShellCheck yet? `brew install shellcheck` on a Mac, `sudo apt-get install shellcheck` on Ubuntu.

**A real install into a throwaway home folder**

Always point `HOME` at an empty folder when you test, and always pass `--skip-plugins --skip-mcp`. That way your own setup is never touched. If you've set `CLAUDE_CONFIG_DIR`, unset it first, or the installer writes there.

```bash
TESTHOME="$(mktemp -d)"
HOME="$TESTHOME" bash install.sh --dry-run --skip-plugins --skip-mcp
ls -A "$TESTHOME"
HOME="$TESTHOME" bash install.sh --skip-plugins --skip-mcp
HOME="$TESTHOME" bash install.sh --skip-plugins --skip-mcp
```

What you should see:

- After the dry run, `ls -A` prints nothing. A dry run writes nothing.
- The first real run says `added 93` and `fetched 1`, and the check at the end says all 94 pack skills are in place. Node.js has to be installed for the fetch.
- The second run says `added 0` and `fetched 0`, and `$TESTHOME/.claude/CLAUDE.md` still has exactly one pack section.
- A warning that Claude Code isn't installed is fine here. If you do have it, the check lists the plugins and MCP servers as MISSING because the test skipped them, and Claude Code may rename the model in the test folder's `settings.json` to a variant like `opus[1m]`. Both are expected.

**Your own settings and rules survive**

```bash
TESTHOME="$(mktemp -d)"
mkdir -p "$TESTHOME/.claude"
echo '{"model":"sonnet","enabledPlugins":{"x@y":true}}' > "$TESTHOME/.claude/settings.json"
echo '# mine' > "$TESTHOME/.claude/CLAUDE.md"
HOME="$TESTHOME" bash install.sh --skip-plugins --skip-mcp
cat "$TESTHOME/.claude/settings.json"
head -n 1 "$TESTHOME/.claude/CLAUDE.md"
```

The model should still be `sonnet`, `x@y` should still be `true`, and the first line of `CLAUDE.md` should still be `# mine`.

**The lint checks**

CI also checks that every `SKILL.md` has a `name` matching its folder and a `description`, that every skill is listed in `docs/SKILLS.md`, and that there are no em dashes in the pack's own writing. Those checks are short Python scripts inside `ci.yml`. This runs all three, straight from the workflow file:

```bash
python3 - <<'EOF'
import re, subprocess, sys, textwrap
lint = open(".github/workflows/ci.yml").read().split("\n  lint:\n", 1)[1]
checks = re.findall(r"python3 - <<'PY'\n(.*?)\n *PY\n", lint, re.S)
codes = [subprocess.run([sys.executable, "-c", textwrap.dedent(c)]).returncode for c in checks]
sys.exit(max(codes))
EOF
```

## Changing install.sh

- Keep it working in bash 3.2 (the version macOS ships) and on Linux. On a Mac, test with `/bin/bash`. Avoid GNU-only habits like `sed -i` without a suffix, `grep -P` and `readlink -f`.
- No sudo, and nothing deleted. Back up anything before you replace it.
- `--dry-run` must change nothing. Wrap commands that change things in `run`, or check `$DRY` first.
- Don't overwrite what someone already has unless they pass `--force`.
- If you change what the installer does, update `SECURITY.md` so it still matches.

## How to write

The people reading this just want their setup to work. Write for them.

- Plain words and short sentences. Say what something does, not how amazing it is.
- No em dashes (the long dash, U+2014). Use a comma, colon, period or parentheses instead. CI checks README.md, CLAUDE.md, `rules/`, `docs/`, install.sh, `tools/`, `.github/`, SECURITY.md, CONTRIBUTING.md and CHANGELOG.md.
- Skip the filler: seamless, elevate, unlock, leverage, robust, delve, "it's worth noting", game-changer.
- Every claim has to match the code. If a doc says the installer does something, it does exactly that.
- Talk to the reader as "you".

## Pull requests

- One change per pull request: one skill, or one fix.
- Fill in the checklist in the pull request template.
- Add a line to `CHANGELOG.md` under a new "Unreleased" heading if people will notice the change.
- By contributing, you agree that your work is shared under the pack's MIT licence (`LICENSE`). Skills keep their own author's licence.
