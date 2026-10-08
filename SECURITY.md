# Security

Claude Power Pack is a shell script that sets up Claude Code on your computer. You're trusting it with your Claude setup, so this page says exactly what it changes, what it never does, and how to check it yourself before you run it.

If you've set `CLAUDE_CONFIG_DIR`, read that folder wherever this page says `~/.claude`.

## What install.sh changes

Everything happens inside your own user account. Run `bash install.sh --dry-run` to see the list for your machine before anything changes.

**On every run**

- **Skills.** Copies each folder in `skills/` into `~/.claude/skills`. If you already have a skill with the same name, yours is left alone. With `--extras` it also copies the folders in `extras/`.
- **Two skills straight from their authors.** Skills whose repos don't allow copying are listed in `skills-upstream.txt`. The installer fetches them from the author's GitHub repo with `npx skills add`: `hormozi-ad-factory` by default, `remotion-best-practices` only with `--extras`. It sets `DISABLE_TELEMETRY=1` so the skills CLI doesn't report the install. npx downloads that CLI from npm and caches it in `~/.npm`, and the CLI notes where the skill came from in `~/.agents/.skill-lock.json`.
- **Working rules.** Adds the text of `rules/CLAUDE.md` to `~/.claude/CLAUDE.md`, between a `<!-- claude-power-pack:start -->` line and a `<!-- claude-power-pack:end -->` line. Anything already in the file stays above it. If the section is already there, it's left exactly as it is, including your edits.
- **Settings.** Merges `settings/settings.json` into `~/.claude/settings.json`. Any key you've already set keeps your value. For groups of entries, like plugin sources, only the missing ones are added. The pack sets the Opus model, high effort, workflows on, five plugin marketplace sources and one hook. The hook runs before Claude's shell commands: when the command is a search (grep, find, rg and similar) in a folder that has a graphify knowledge graph, it gives Claude a tip to use the graph instead. It doesn't send anything anywhere. The merged file is saved as formatted JSON. If your `settings.json` isn't valid JSON, it isn't touched.
- **Plugins** (skip with `--skip-plugins`). Through the `claude` command, it adds five plugin marketplaces (anthropics/claude-plugins-official, anthropics/knowledge-work-plugins, forrestchang/andrej-karpathy-skills, firebase/firebase-tools and latent-spaces/brag) and installs nine plugins for your user. Ones you already have are skipped.
- **MCP servers** (skip with `--skip-mcp`). Adds `playwright` (`npx @playwright/mcp@latest`) and `chrome-devtools` (`npx -y chrome-devtools-mcp@latest`) for your user, unless you already have servers with those names. Neither needs a key. When Claude Code starts them, npx fetches the latest version from npm.
- **A final check.** Runs `tools/verify.sh`, which only reads.

The plugin and MCP steps need the `claude` command. If it isn't installed, the installer says so and skips them.

**Only with `--tools`**

- On a Mac with Homebrew: `brew install` for node, git, python, uv, gh, ffmpeg, yt-dlp, whisper-cpp and auto-editor, skipping any you already have. If Homebrew is missing, it tells you where to get it and moves on. It never installs Homebrew itself. On Linux it prints the list for you to install with your package manager.
- `npm install -g` for `@playwright/cli@latest` and `firecrawl-cli@1.19.6`.
- `uv tool install --upgrade graphifyy` (the graphify command).
- `npm ci` inside the `seo-checklist` and `video-transcript-downloader` skill folders, using their lock files.
- Downloads the Whisper speech model `ggml-small.bin` (about 490 MB) from huggingface.co/ggerganov/whisper.cpp into `~/.cache/whisper-models/`, if it isn't there yet.
- If DaVinci Resolve Studio is installed in `/Applications`, it runs the setup scripts of the two Resolve skills. They create a Python environment inside each skill's own folder, install its Python packages with pip, and register Resolve's own MCP server (`davinci-resolve`) with Claude Code unless it already knows one.

## What it never does

- **No sudo.** It never asks for your password or admin rights. Anything that would need them, like installing Homebrew, is left to you with a message that says how.
- **No keys, tokens or passwords.** It never asks for them and never stores any. You log in to the tools yourself later, in your own terminal or browser (see `docs/SETUP-CHECKLIST.md`).
- **No deleting.** There is no `rm` in install.sh. Before it changes anything, it copies your `settings.json`, your `CLAUDE.md` and a list of your skill folders to `~/.claude-power-pack-backup/<date-time>/`. With `--force`, the skills it replaces are moved there too, not deleted.
- **No overwriting your choices.** Your own skills, your settings values and your edits to the rules section stay as they are, unless you pass `--force`.
- **No permission changes.** The pack's settings add no allow rules and don't turn on any mode that skips Claude Code's permission prompts.
- **No changes to your shell or system settings.** It doesn't edit your shell profile (`.zshrc`, `.bashrc`) or any system setting.

## Check it yourself first

1. **Read `install.sh`.** It's one file of about 270 lines, with a comment above each step.
2. **Do a dry run:** `bash install.sh --dry-run`. It prints every step it would take and changes nothing.
3. **Look at what gets added:** `settings/settings.json` (merged into your settings, hook included), `rules/CLAUDE.md` (the rules text) and `skills-upstream.txt` (the skills fetched from other repos).
4. **Try it in a throwaway home folder.** This installs into an empty temp folder and leaves your real setup alone:

   ```bash
   TESTHOME="$(mktemp -d)"
   HOME="$TESTHOME" bash install.sh --skip-plugins --skip-mcp
   ls -A "$TESTHOME/.claude"
   ```

   If you've set `CLAUDE_CONFIG_DIR`, unset it first, or the installer writes to that folder.
5. **See the tests.** Every push to this repo runs the installer on clean Ubuntu and macOS machines. It checks that a dry run writes nothing, that a second run changes nothing, and that your own settings and rules survive. The steps are in `.github/workflows/ci.yml`.

## Skills run with full agent permissions

A skill is instructions, and sometimes scripts, that Claude follows with the same access you've given Claude Code. It can run commands and read and write files. Treat a skill like any other code you install.

- Most skills here were written by other people. `THIRD_PARTY_NOTICES.md` lists where each one came from and its licence.
- The copies in `skills/` and `extras/` are fixed snapshots. They only change when this repo changes.
- The skills in `skills-upstream.txt`, the plugins and the two MCP servers come straight from their sources when you install (the MCP servers each time they start). They can change after this pack was put together.
- To review a skill, open its folder in `~/.claude/skills` and read `SKILL.md` and any scripts next to it. To remove one, delete its folder.

## Reporting a problem

- **Bugs and questions:** open an issue on the [Issues tab](https://github.com/mppyxx/claude-power-pack/issues).
- **Anything sensitive,** like a skill that does something harmful or hidden, or a way the installer could damage a system or leak data: please don't open a public issue. Use GitHub's private vulnerability reporting instead. Go to the repo's **Security** tab and click **Report a vulnerability**. Only you and the maintainers can see the report.
- **A problem inside a third-party skill:** please tell its author too. Their repo is linked in `THIRD_PARTY_NOTICES.md`.

It helps to include what you ran, what happened, your OS and your Claude Code version (`claude --version`). Leave out any keys or tokens.

This is a small community project, so a reply can take a few days. Fixes go into the `main` branch. Older versions don't get separate patches.
