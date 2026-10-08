## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] I ran the local tests in CONTRIBUTING.md: syntax, ShellCheck, an install into a throwaway home folder, a second run, and the lint checks.
- [ ] New or changed skill: the `name` in SKILL.md matches its folder, it's listed in `docs/SKILLS.md`, and its licence is in `THIRD_PARTY_NOTICES.md` and `licenses/` (or it's in `skills-upstream.txt`).
- [ ] Skill count changed: I updated every place that shows it, including `EXPECTED_SKILLS` in `.github/workflows/ci.yml`.
- [ ] install.sh changed: it still works in bash 3.2 and on Linux, needs no sudo, backs up before replacing anything, changes nothing with `--dry-run`, and `SECURITY.md` still matches what it does.
- [ ] The writing is plain, with no em dashes.
