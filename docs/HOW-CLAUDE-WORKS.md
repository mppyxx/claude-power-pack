# How this setup works

A quick tour of what's going on under the hood, so you know what to ask for and what to expect.

## The layers

1. **CLAUDE.md** (your working rules, `~/.claude/CLAUDE.md`). Loaded into every session. It's where "act, don't wait", "test before you say done" and the writing style live. Edit it whenever Claude does something you don't like; that's how you train it.
2. **The superpowers plugin.** Makes Claude check for a matching skill before every task and gives it a disciplined method: brainstorm, plan, build, verify.
3. **Skills** (`~/.claude/skills`). Instructions plus scripts. Only each skill's short description is always loaded (about 10k tokens for all 94); the full instructions load when a task needs them. Claude picks them from your wording. You can force one with `/name`.
4. **Plugins.** Bundles of skills, commands, agents and sometimes connectors (superpowers, figma, firebase, design, marketing, brand-voice, legal, brag).
5. **Connectors and MCP servers.** Access to your apps and tools (Gmail, Notion, Figma, a browser, Vercel...).
6. **Subagents and workflows.** Claude can hand work to helper agents that run in parallel and report back. Workflows run many of them in a set order for big jobs.
7. **Memory.** Claude Code keeps notes per project about you and your preferences. Say "remember that..." to save something on purpose.

## What happens for common requests

| You say | What Claude does |
|---|---|
| "Build me a landing page for X" | superpowers brainstorming (a few quick questions), then a design skill (impeccable, design-taste-frontend, hallmark), builds it, opens it in a browser, screenshots desktop and mobile, fixes what looks off |
| "This is broken: ..." | systematic-debugging: reproduce, find the root cause, fix, prove the fix with a test |
| "Make this look less generic" | impeccable or anti-slop-ajonai audit, then specific fixes to type, color, spacing, motion |
| "Research X for me" | firecrawl-deep-research or tavily-research, with sources you can click |
| "What's in this reel/video?" (paste a link) | reel-reader downloads, transcribes and pulls frames, then summarizes |
| "Is my app secure?" | never-get-hacked audit, then a fix plan |
| "Why isn't my site on Google?" | seo-checklist audit against the live site, fixes in the code |
| "Deploy this" | Vercel connector or the Cloudflare skills |
| "Make ads for my product" | ad-creative-pro or hormozi-ad-factory for copy, Higgsfield for visuals |
| "Edit this footage" (with DaVinci Resolve Studio) | resolve-editor builds the cut inside Resolve, resolve-colorist grades it |
| "Explain this codebase" | `/graphify` builds a knowledge graph, then answers from it |
| "Every morning, do X" | sets up a scheduled task or routine |

You never have to name a skill. Describe the outcome; if Claude picks the wrong skill, say which one you want.

## Habits that make it work better

- **New task, new session.** Long conversations get worse well before they're full. Start fresh for each separate job.
- **Say what "done" looks like.** "Working signup with email confirmation, tested in the browser" beats "add signup".
- **Let it verify.** The rules make Claude test before it claims something works. If it ever says "done" without showing proof, ask "how did you check?"
- **Ask for a second opinion on big work.** "Run an independent review of everything you changed" catches bugs the author misses.
- **Correct it once, durably.** When something bugs you, tell Claude to add a rule to CLAUDE.md or save a memory, so you never repeat yourself.
- **Use plan mode for big changes.** Shift+Tab cycles modes in the terminal; plan mode makes Claude propose before it touches files.
- **`/compact`** with a hint ("/compact keep the API decisions") when a session gets long but you're not done.
- **Keep long jobs in a real folder**, not /tmp. The rules already say this.

## Big-job tools

- **Subagents**: Claude spins them up on its own for searches and parallel work. You can also ask: "use 3 agents to check these in parallel".
- **Workflows**: for large jobs (audit a whole codebase, research 20 competitors) say "use a workflow". They can run dozens of agents, so they eat usage; use them when the job is worth it. Workflows are switched on in the settings this pack installs.
- **/code-review**: reviews your current changes for bugs.
- **/loop** and **/schedule**: repeat a task on an interval, or run a cloud agent on a timetable.
- **Effort**: this pack sets effort to high and the model to Opus, the strongest combination. On Max you have the headroom for it; if you ever hit your limit, `/model` lets you switch for a session.

## Plans and limits

Max gives a lot of usage, but it isn't infinite. The heavy hitters are workflows with many agents, very long sessions, and looping tasks left running. Normal skill use is cheap because a skill's full instructions only load when needed.

## Making your own skills

Ask "make a skill that does X" and the built-in `skill-creator` writes one into `~/.claude/skills`. Good skills have a clear description of when to use them; that's what makes Claude pick them up. `claude-setup-tuneup` can audit them later.
