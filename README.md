# Claude Power Pack

> **Easiest way:** get the folder, open it in Claude Code (or the Code tab of the Claude desktop app) and say "install this". Claude follows `CLAUDE.md` and does the rest, then walks you through the few logins only you can do.
>
> ```bash
> git clone https://github.com/mppyxx/claude-power-pack ~/claude-power-pack
> ```
>
> No git? Click **Code > Download ZIP** on GitHub and unzip it into your home folder.

A complete Claude setup for a Claude Max account: 94 skills, 9 plugins, working rules that make Claude act like a careful senior teammate, and a guide to every connector worth hooking up. It's a working setup built up over a lot of daily use, with everything personal taken out.

## Install in 3 steps

1. Install the Claude desktop app and Claude Code, and sign in with your Max account (docs/SETUP-CHECKLIST.md, step 1).
2. In Terminal, inside this folder:
   ```bash
   bash install.sh --tools
   ```
3. Connect your apps at claude.ai > Settings > Connectors, and log in to the tools you'll use (docs/SETUP-CHECKLIST.md, steps 3 to 5).

Restart Claude Code and you're set. Run `bash tools/verify.sh` any time to see what's working and what's left.

## What's inside

| | |
|---|---|
| `skills/` | 94 skills: frontend design, animation, web research and scraping, Cloudflare, security, SEO, ads, AI image and video, video editing in DaVinci Resolve, and tools that make Claude itself work better |
| `extras/` | 9 optional skills (marketing, SEO, Remotion video, responsive layout, prompt writing, a security checklist), several overlapping with the main set. Add them with `--extras` |
| `CLAUDE.md` | Install instructions written for Claude, so you can just hand Claude the folder |
| `rules/CLAUDE.md` | The working rules that get installed: act without hand-holding, test before claiming done, get an independent review on big work, write like a human |
| `settings/` | Opus model, high effort, workflows on, plugin sources, a graphify hook |
| `install.sh` | Installs all of it, backs up what you had, never overwrites your own stuff (or your edits to the rules) unless you say `--force` |
| `docs/SKILLS.md` | Every skill: what it does and what it needs |
| `docs/CONNECTORS.md` | Every connector, desktop extension and MCP server, and where to turn each on |
| `docs/HOW-CLAUDE-WORKS.md` | How the pieces fit together, what to ask for, and habits that get better results |
| `docs/SETUP-CHECKLIST.md` | Step by step, with test prompts and how to undo |

## What it can't do for you

Logins. Connectors, API keys and CLI sign-ins belong to your own accounts, so you do those once by hand. The checklist lists every one, and most are free.

## Requirements

- Claude Max (Pro works too, but the Opus-heavy setup will hit limits sooner)
- macOS is the main target. Linux works for the skills, rules, settings, plugins and MCP servers; the Mac-only parts are the desktop extensions and the Homebrew tool install.
- Optional: DaVinci Resolve Studio 21.1+ for the two Resolve skills.

## Credits

Most skills here were written by other people and are shared under their open-source licences: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Two skills whose repos have no open licence aren't copied here; the installer fetches them from their authors. Everything written for this pack is MIT ([LICENSE](LICENSE)).

Not affiliated with Anthropic.
