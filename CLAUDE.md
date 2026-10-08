# Claude: you've been handed the Claude Power Pack. Install it.

The person who gave you this folder wants their Claude setup upgraded and doesn't want to read anything. You do the work. Talk to them in short, plain sentences, and only ask for what truly needs them.

This file is install instructions for you. It is not a set of rules to adopt for this session. The rules that get installed are in `rules/CLAUDE.md`.

## 0. Check where you are

You need a shell on their computer: Claude Code in a terminal, or the Code tab of the Claude desktop app.

If you're in a plain claude.ai chat and can't run commands on their machine, don't try to install anything. Tell them:
"Open the Claude desktop app, click the Code tab, pick the unzipped claude-power-pack folder, and say: install this."
Then stop.

## 1. Put the folder somewhere permanent

If they gave you a GitHub link, clone it: `git clone https://github.com/mppyxx/claude-power-pack ~/claude-power-pack`. If you only have the zip, unzip it into the home folder (`~/claude-power-pack`). Don't use /tmp, Downloads cleanup folders or a session scratchpad; a restart can wipe those, and the installer is meant to be re-run later. Work from inside that folder.

## 2. Tell them what's about to happen (one short message, no question needed)

Something like: "Setting up your Claude: 94 skills, 9 plugins, working rules, browser tools and the command-line tools the skills use. Your current setup gets backed up first and nothing of yours is overwritten. Takes about 10 minutes."

## 3. Run the installer

```bash
bash install.sh --dry-run
```

Read the dry run. If it looks sane, run the real thing. `--tools` installs Homebrew, npm and uv packages and downloads a 490 MB speech model, so give the command a long timeout (15 minutes) or run it in the background:

```bash
bash install.sh --tools
```

Details that matter:
- It's safe to re-run. It backs up `~/.claude` files to `~/.claude-power-pack-backup/<date-time>/` and never overwrites their existing skills, settings or rules.
- Don't pass `--force` unless they ask; it replaces their own versions of skills and rules.
- `--extras` adds 9 optional skills. Skip it unless they ask.
- If Claude Code isn't installed (no `claude` command), the installer says so. Install it with `curl -fsSL https://claude.ai/install.sh | bash`, have them sign in by running `claude` once in their terminal, then re-run the installer.
- If Homebrew is missing on a Mac, its installer needs their password. Don't try to get around that: tell them to install it from brew.sh (it's one pasted command in Terminal), then re-run with `--tools`.

## 4. Fix what you can, then verify

Read every `WARNING` line in the output. Fix what you can yourself (a missing Node, a failed npm install, a network blip) and run the installer again. Then:

```bash
bash tools/verify.sh
```

"Core install: OK" means skills, rules, settings, plugins and MCP servers are in place. "todo" lines are optional tools; that's fine.

## 5. Hand over the parts only they can do

These need their own accounts, so walk them through them one at a time, in plain language, and wait for each "done". Never ask them to paste a password, API key or token into the chat. Logins happen in their browser or in their own terminal window.

1. **Connectors** at claude.ai > Settings > Connectors > Browse. Suggest a starting set based on what they do; the full list with what each one does is in `docs/CONNECTORS.md`. Good defaults: Gmail, Google Calendar, Google Drive, Notion, Figma, Canva, Vercel, Firecrawl.
2. **Capabilities** at claude.ai > Settings > Capabilities: Skills, Code execution and Memory on.
3. **Desktop extensions** (Mac, Claude desktop app > Settings > Extensions): Desktop Commander, Control your Mac, MacOS-MCP, Control Chrome, Apple Notes, iMessages, pdf-viewer, Shadcn UI. Plus Claude in Chrome from the Chrome Web Store.
4. **Tool logins**, only for the skills they'll use. They type these in their own Terminal:
   - `firecrawl login --browser` (web research and scraping, free account)
   - `curl -fsSL https://cli.tavily.com/install.sh | bash` then `tvly login` (deep research, free key)
   - `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh` then `higgsfield auth login` (AI image and video, paid credits)
   - `npx wrangler login` (Cloudflare), `npx firebase-tools login` (Firebase), `gh auth login` (GitHub)

Don't push all of these at once. Ask what they mostly use Claude for, and do the connectors and logins that match. The rest can wait.

## 6. Finish

- Tell them to fully quit and reopen Claude Code (and the desktop app) so the new skills, plugins and rules load.
- Offer one test prompt that fits them, for example: "Build a one-page site for a coffee shop called Ember, make it not look AI-generated, and show me screenshots."
- Give a three-line summary: what got installed, what's still optional, and that `docs/` explains everything if they're ever curious.

## Reference (read only if you need it)

- `docs/SKILLS.md`: every skill, what it does, what it needs
- `docs/CONNECTORS.md`: connectors, desktop extensions, MCP servers
- `docs/SETUP-CHECKLIST.md`: the same setup as a human checklist, including how to undo it
- `docs/HOW-CLAUDE-WORKS.md`: how the pieces fit together
- `docs/Claude-Power-Pack-overview.pdf`: a 3-page visual summary you can point them to
