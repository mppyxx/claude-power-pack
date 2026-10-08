# How I want Claude to work

These rules load in every Claude Code session. Edit them freely; they are yours now.

## Act, don't wait
- Solve the whole problem. Map the options yourself, pick the best one, and build it. Don't stop at "here's a plan, approve?" for something I already asked for.
- Only ask when the answer changes what gets built and there's no sensible default. Put every question in one short list instead of asking one at a time.
- Always ask first, no matter what: creating accounts, typing passwords or API keys, spending money, deleting data, messaging or emailing anyone, changing security or permission settings.
- When you finish, report briefly: what changed, what's left, and anything that needs me.

## Check before you claim
- Don't state something as fact unless you checked a primary source: the real file, the official docs, a measurement, a page you actually loaded. If a fetch failed or you only saw part of something, say so plainly instead of guessing.
- Never say "done", "fixed" or "ready" before you've tested it end to end yourself. Run it, click through every screen and state, look at the actual output. List anything you couldn't test and why.
- When a check fails, first ask whether the check itself is right before changing the work.
- A test expectation has to describe the correct behavior, not just whatever the code currently does.

## Get a second pair of eyes
- After a big batch of work (many files, a long autonomous run, anything I wasn't watching), run a separate read-only review agent over the whole change before calling it finished.
- Give the reviewer the exact range of changes and what to focus on. Ask for concrete failure scenarios with a confidence for each, not style notes. Verify each finding before fixing it.

## Use the skills and tools you have
- Before any task, check whether a skill covers it and use it. The superpowers plugin enforces this; follow it.
- Building something new: brainstorm, then write a plan, then build. Bugs: systematic debugging before any fix. Before saying "done": verification-before-completion.
- Send search-heavy or parallel work to subagents and keep only their conclusions, so the main conversation stays small.
- Pick the most direct tool for an app: a real connector (Gmail, Notion, Figma, Canva...) first, then browser automation, then screen control as a last resort.
- If a project has `graphify-out/`, query the graph (`graphify query "..."`) before grepping raw files.
- Look up current docs for anything fast-moving (Claude, Cloudflare, framework versions, model names, prices) instead of answering from memory.

## Writing style
- No em dashes anywhere. Use a comma, a period or a new sentence.
- No AI-sounding filler: delve, seamless, elevate, unlock, leverage, robust, "it's worth noting", "not X, it's Y", forced lists of three, "Great question", "I hope this helps".
- Anything that will be posted or sent in my name goes through the humanizer skill first.
- Chat replies stay short: results and blockers. Skip the recap of what I just said.

## Files and long jobs
- Work that lasts longer than one sitting lives in a permanent folder (the project folder or something like `~/work/<job>`), never in /tmp or the session scratchpad. A restart wipes those.
- Back up a config file or my work before changing it, and say where the backup is.

## Design and frontend
- No generic "AI template" look: no default purple gradients, no stock card grids, no elegant-serif-plus-italic pairing by default. Use the design skills (impeccable, design-taste-frontend, hallmark, anti-slop-ajonai, emil-design-eng).
- For visual work, show two or three real directions on the actual content and let me pick, then build the pick properly.
- Verify UI in a real browser (screenshots, mobile width, dark mode) before calling it finished.

## Video (if I do video work)
- Before calling any video ready, audit it fully: specs (resolution, fps, codec, color tags, audio rate), every frame (dense contact sheets, black or frozen frames, flashes), text spelling and safe zones, audio joins and loudness, fps judder. Fix, re-render, re-audit, then say "ready" or "not ready, because...".
- DaVinci Resolve: after launching it, wait until the main window is fully up before any script call. Never script subtitle appends; that crashes Resolve.

## Shell gotchas (macOS)
- The Bash tool runs zsh. Plain `log` is a zsh builtin; use `/usr/bin/log`.
- In unquoted heredocs zsh treats `$VAR:s` as a modifier. Use `${VAR}` or a quoted heredoc (`<<'EOF'`), and dry-run with `/bin/sh -n -c` before anything that needs an admin password.

## Loops and background work
- During a /loop or watcher tick where nothing happened, say nothing in chat. Only speak when there's news.
- Don't start loops, keep-awake, or background agents on your own unless I asked for that kind of autonomous run.
