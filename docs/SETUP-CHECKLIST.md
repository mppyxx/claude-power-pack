# Setup checklist

Do these in order. The first three steps are the core; the rest you can do when you need them.

## 1. Install the apps (5 min)

- [ ] **Claude desktop app** from claude.ai/download. Sign in with your Max account.
- [ ] **Claude Code** (the terminal version): open Terminal and run
  ```bash
  curl -fsSL https://claude.ai/install.sh | bash
  ```
  then run `claude` once and sign in with the same account.
- [ ] **Homebrew** (Mac package manager), if you don't have it: see brew.sh.

## 2. Run the installer (5 to 15 min)

Unzip the pack anywhere, open Terminal in that folder and run:

```bash
bash install.sh --tools
```

`--tools` also installs the command-line tools the skills use (ffmpeg, yt-dlp, whisper, auto-editor, Playwright, Firecrawl CLI, graphify) and downloads a 490 MB speech model. Leave it off for a lighter install; you can run it again later with `--tools`.

It backs up your current setup first and never overwrites skills, settings or rules you already have, including your own edits to the pack's rules when you run it again (add `--force` to reset skills and rules to the pack's versions). To see what it would do without changing anything: `bash install.sh --dry-run`.

When it finishes it runs a check. "Core install: OK" means skills, rules, settings, plugins and MCP servers are in place.

## 3. Turn things on in claude.ai (5 min)

At **claude.ai > Settings**:
- [ ] **Capabilities**: Skills on, Code execution on, Memory on.
- [ ] **Connectors**: connect the ones you'll use from docs/CONNECTORS.md. Good starting set: Gmail, Google Calendar, Google Drive, Notion, Figma, Canva, Vercel, Firecrawl.

## 4. Desktop extensions (5 min, Mac)

- [ ] Claude desktop app > Settings > Extensions: install Desktop Commander, Control your Mac, MacOS-MCP, Control Chrome, Apple Notes, iMessages, pdf-viewer, Shadcn UI (list in docs/CONNECTORS.md).
- [ ] Install **Claude in Chrome** from the Chrome Web Store (the Claude extension by Anthropic) and sign in.

## 5. Log in to the skill tools you'll use

Each is a free account unless noted. Skip any you don't need.

| For | Run | Get an account at |
|---|---|---|
| Firecrawl skills (web scraping, research) | `firecrawl login --browser` | firecrawl.dev |
| tavily-research | `curl -fsSL https://cli.tavily.com/install.sh \| bash` then `tvly login` | tavily.com |
| Higgsfield skills (image and video AI, paid credits) | `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \| sh` then `higgsfield auth login` | higgsfield.ai |
| Cloudflare skills | `npx wrangler login` | cloudflare.com |
| Firebase plugin | `npx firebase-tools login` | firebase.google.com |
| GitHub work (PRs, repos) | `gh auth login` | github.com |
| graphify (optional, richer graphs) | `uv tool install --upgrade 'graphifyy[gemini]'`, then add `export GEMINI_API_KEY=...` to `~/.zshrc` | aistudio.google.com |

Never paste a key into a chat with Claude. Type logins yourself in Terminal.

## 6. Only if you have DaVinci Resolve Studio

- [ ] Resolve Studio 21.1 or newer installed.
- [ ] `bash install.sh --tools` sets up both Resolve skills and registers Resolve with Claude Code.
- [ ] For the desktop app, add Resolve's extension (command in docs/CONNECTORS.md).

## 7. Check it works

Restart Claude Code, then:

- [ ] Type `/plugin`: you should see superpowers, figma, firebase, brag, design, marketing, brand-voice, legal and andrej-karpathy-skills.
- [ ] Type `/mcp`: playwright and chrome-devtools connected, plus your claude.ai connectors.
- [ ] Type `/` and start typing `impeccable`: the skill should show up.
- [ ] Run `bash tools/verify.sh` from the pack folder any time to re-check.

Try a few real prompts:

- "Build a one-page site for a coffee shop called Ember, make it not look AI-generated, and show me screenshots."
- "Research the top 5 note-taking apps and compare pricing, with sources."
- "Summarize this video: <a YouTube Shorts link>"
- "What's on my calendar this week?" (needs the Google Calendar connector)

## Undo

To stop automatic updates without uninstalling anything: `bash ~/.claude/power-pack/auto-update.sh off`.


Your previous `settings.json`, `CLAUDE.md` and any replaced skills are in `~/.claude-power-pack-backup/<date-time>/`. To remove the pack's rules, delete everything between the `claude-power-pack:start` and `claude-power-pack:end` lines in `~/.claude/CLAUDE.md`. Plugins: `claude plugin uninstall <name>`. MCP servers: `claude mcp remove <name>`.
