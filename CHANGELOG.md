# Changelog

What changed in each release of Claude Power Pack. Dates are year-month-day.

## 1.0.0 (2026-10-08)

The first public release.

### What gets installed

- **94 skills** in `~/.claude/skills`: 93 copied from `skills/`, plus `hormozi-ad-factory`, which is fetched from its author's repo because it has no open licence. They cover design and frontend, animation, web research and scraping, Cloudflare, security, SEO, marketing, AI image and video, video editing in DaVinci Resolve, and tools that help Claude itself work better.
- **9 optional skills** with `--extras`: the 8 in `extras/`, plus `remotion-best-practices`, fetched from its author.
- **9 plugins** from 5 marketplaces: superpowers, figma, andrej-karpathy-skills, firebase, brag, design, marketing, brand-voice and legal.
- **Working rules** from `rules/CLAUDE.md`, added to `~/.claude/CLAUDE.md` as a marked section you can edit or remove.
- **Settings:** the Opus model, high effort, workflows on, the plugin marketplaces and a graphify hook. They're merged in, and your own values win.
- **2 MCP servers** that need no keys: playwright and chrome-devtools.
- **Command-line tools** with `--tools`: Homebrew, npm and uv packages the skills use, the Whisper speech model, and the Resolve skills' setup when DaVinci Resolve Studio is installed.

### The installer

- `install.sh`, with `--dry-run`, `--tools`, `--extras`, `--force`, `--skip-plugins` and `--skip-mcp`.
- Backs up your settings, your rules and the list of your skills to `~/.claude-power-pack-backup/<date-time>/` before it changes anything.
- Never overwrites your own skills, settings or edits to the rules unless you pass `--force`.
- Safe to run again: a second run leaves your setup as it was.
- `tools/verify.sh` shows what's installed and what's left to do.
- `CLAUDE.md` at the top of the repo lets you open the folder in Claude Code and just say "install this".

### Docs

- `docs/SKILLS.md`, `docs/CONNECTORS.md`, `docs/HOW-CLAUDE-WORKS.md`, `docs/SETUP-CHECKLIST.md` and a 3-page overview PDF.
- `THIRD_PARTY_NOTICES.md` and `licenses/` credit the author and licence of every skill.
- A website at https://mppyxx.github.io/claude-power-pack/ with an FAQ, and `llms.txt` so AI assistants can read and cite the project.
- Screenshots and diagrams in `assets/`, taken from a real first install.

### For contributors

- An install test on Ubuntu and macOS for every push and pull request. It checks the real install, a dry run, a second run and a merge with existing settings, plus the skill metadata, the skills list and the writing style.
- `SECURITY.md`, `CONTRIBUTING.md`, issue forms and a pull request template.
