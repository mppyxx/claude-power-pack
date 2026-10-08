# Skills in this pack

94 skills install into `~/.claude/skills`. Claude picks the right one on its own from what you ask; you can also call one directly by typing `/` and its name (for example `/impeccable` or `/graphify`).

"Setup" says what each skill needs before it works. Most need nothing. `bash install.sh --tools` installs the Homebrew, npm and uv tools listed here (ffmpeg, yt-dlp, whisper, auto-editor, Playwright CLI, Firecrawl CLI, graphify, and the skills' own npm packages). The Tavily and Higgsfield CLIs, wrangler and all logins are yours to do (see SETUP-CHECKLIST.md).

## Working smarter

| Skill | What it does | Setup |
|---|---|---|
| `graphify` | Turns a codebase, docs, papers or videos into a knowledge graph, then answers questions from the graph instead of grepping. Type `/graphify` in a project. | `uv tool install graphifyy` (`--tools` does it). Optional richer extraction: `uv tool install --upgrade 'graphifyy[gemini]'` plus a `GEMINI_API_KEY` |
| `full-output-enforcement` | Stops truncated code and `// rest stays the same` placeholders. Forces complete files. | Nothing |
| `humanizer` | Rewrites text so it reads like a person wrote it: no em dashes, no AI filler words. | Nothing |
| `claude-setup-tuneup` | Audits your Claude Code setup (CLAUDE.md, settings, hooks, skills) against current best practices and suggests fixes. | Nothing |
| `find-skills` | Finds and installs more skills when you ask "is there a skill for X". | `npx skills` (comes with Node) |

## Design and frontend

| Skill | What it does | Setup |
|---|---|---|
| `frontend-design` | Anthropic's skill for distinctive, production-grade web UI. | Nothing |
| `impeccable` | The big one for UI: design, redesign, critique, audit, polish, typography, color, spacing, motion, accessibility, live browser iteration. | Nothing |
| `design-taste-frontend` | Reads the brief, picks a real design direction, ships landing pages and portfolios that don't look templated. | Nothing |
| `design-taste-frontend-v1` | The original version of the above, kept for projects that rely on it. | Nothing |
| `hallmark` | Anti-AI-slop design for new pages, audits, redesigns and pulling a design system out of an existing site. | Nothing |
| `anti-slop-ajonai` | Builds interfaces that don't read as machine-made, with a runnable checker for default-looking choices. | Nothing |
| `high-end-visual-design` | Agency-level type, spacing, shadows and layout rules. | Nothing |
| `minimalist-ui` | Clean editorial style: warm monochrome, strong type contrast, flat surfaces. | Nothing |
| `industrial-brutalist-ui` | Raw mechanical style: Swiss print meets terminal. | Nothing |
| `gpt-taste` | Strong UX plus advanced GSAP motion. | Nothing |
| `redesign-existing-projects` | Audits an existing site or app and upgrades it to a premium look. | Nothing |
| `stitch-design-taste` | Writes DESIGN.md files for Google Stitch. | Nothing |
| `uiux-pro-max` | Design-system advice: palettes, type pairings, styles, components, industry patterns, WCAG. | Nothing |
| `emil-design-eng` | Emil Kowalski's approach to UI polish and the small details that make software feel good. | Nothing |
| `apple-design` | Apple-style fluid motion, springs, gestures, materials and type, for the web. | Nothing |
| `pick-ui-library` | Picks the right frontend library for a job from a curated list. | Nothing |
| `prototype` | Builds several genuinely different versions of a UI piece so you can compare them. | Nothing |
| `ask-sonner` | Everything about the Sonner toast library for React. | Nothing |
| `image-to-code` | Image-first website building: generates the design as an image, then codes it. | An image generator: the Higgsfield connector or CLI (see higgsfield-generate) |
| `imagegen-frontend-web` | Generates premium website design concepts as images. | An image generator: the Higgsfield connector or CLI (see higgsfield-generate) |
| `imagegen-frontend-mobile` | Generates premium mobile app screen concepts as images. | An image generator: the Higgsfield connector or CLI (see higgsfield-generate) |
| `brandkit` | Brand-guideline boards, logo systems and identity decks as images. | An image generator: the Higgsfield connector or CLI (see higgsfield-generate) |
| `write-swift` | Modern Swift and SwiftUI: value types, Swift 6 concurrency, iOS app code. | Xcode for building iOS apps |

## Animation and motion

| Skill | What it does | Setup |
|---|---|---|
| `animate` | Builds a web animation step by step: should it animate, which property, which curve, how it exits. | Nothing |
| `animate-expo` | Animations, gestures and haptics in React Native / Expo. | Nothing |
| `animation-vocabulary` | Tells you the real name of a motion effect you can only describe. | Nothing |
| `find-animation-opportunities` | Scans a UI for places that should animate and don't. | Nothing |
| `improve-animations` | Audits all the motion code in a codebase and plans fixes. | Nothing |
| `review-animations` | Reviews animation code against a high craft bar. | Nothing |

## Web research, scraping and browsers

| Skill | What it does | Setup |
|---|---|---|
| `firecrawl` | Core Firecrawl skill: web search, scrape any page (JS too), crawl, map, interact with logged-in pages. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-search` | Web search that returns full page content, not just snippets. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-scrape` | Clean markdown from any URL. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-crawl` | Crawls a whole site or docs section. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-map` | Lists every URL on a site. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-download` | Downloads a site for offline use. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-interact` | Clicks, fills forms and paginates on pages before scraping. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-parse` | Turns local PDFs and documents into clean text. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-monitor` | Watches pages for changes. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-agent` | Hands a research goal to Firecrawl's agent and gets structured data back. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-workflows` | Router for the outcome workflows below. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-deep-research` | Multi-source research report with citations. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-competitive-intel` | Tracks competitor pricing, features and changelogs. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-company-directories` | Pulls structured company lists from directories (YC, Crunchbase-style). | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-dashboard-reporting` | Pulls numbers out of analytics dashboards you're logged in to. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-demo-walkthrough` | Walks a product's key flows and writes a UX report. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-knowledge-base` | Builds a local knowledge base or RAG corpus from web content. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-knowledge-ingest` | Ingests docs portals, including ones behind a login. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-lead-gen` | Structured lead lists from directories. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-lead-research` | Pre-meeting brief on a company or person. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-market-research` | Market, financial and industry numbers. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-qa` | QA-tests a live website and collects evidence. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-research-index` | Finds the research papers that answer a question. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-research-papers` | Finds and summarizes papers, whitepapers and reports. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-seo-audit` | SEO audit of a live site. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-shop` | Product research and buying recommendations. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-website-design-clone` | Extracts a site's design system into a DESIGN.md. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-build-onboarding` | Adds Firecrawl to your own app's code (keys, SDK). | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-build-scrape` | Firecrawl scrape inside your app's code. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-build-search` | Firecrawl search inside your app's code. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `firecrawl-build-interact` | Firecrawl browser actions inside your app's code. | Firecrawl CLI: `npm i -g firecrawl-cli@1.19.6`, then `firecrawl login --browser` (free account; works keyless with rate limits) |
| `tavily-research` | Deep research with citations through Tavily (30 to 120 seconds per report). | Tavily CLI: `curl -fsSL https://cli.tavily.com/install.sh \| bash`, then `tvly login` (free key at tavily.com) |
| `playwright-cli` | Drives a real browser from the terminal: test pages, fill forms, take screenshots, write Playwright tests. | `npm i -g @playwright/cli@latest` (`--tools` does it) |
| `video-transcript-downloader` | Downloads videos, audio, subtitles and transcripts with yt-dlp. | `brew install yt-dlp ffmpeg` plus `npm ci` in the skill folder (`--tools` does both) |
| `reel-reader` | Give it a TikTok, YouTube Short or Instagram reel link: it downloads it, transcribes it locally and pulls key frames so Claude can actually watch it. | `brew install yt-dlp whisper-cpp ffmpeg node` plus a 490 MB speech model (`--tools` does all of it) |

## Cloudflare and web platform

| Skill | What it does | Setup |
|---|---|---|
| `cloudflare` | Everything Cloudflare: Workers, Pages, KV, D1, R2, AI, networking, security, Terraform. | Free Cloudflare account; `npx wrangler login` |
| `wrangler` | The Wrangler CLI: deploy and manage Workers, KV, R2, D1 and more. | Free Cloudflare account; `npx wrangler login` |
| `workers-best-practices` | Writes and reviews Workers code against production best practices. | Free Cloudflare account; `npx wrangler login` |
| `durable-objects` | Stateful coordination with Durable Objects (chat rooms, multiplayer, booking). | Free Cloudflare account; `npx wrangler login` |
| `agents-sdk` | Builds AI agents on Cloudflare Workers (state, workflows, scheduling, MCP servers, voice). | Free Cloudflare account; `npx wrangler login` |
| `sandbox-stable` | Cloudflare Sandbox apps on the stable SDK. | Free Cloudflare account; `npx wrangler login` |
| `sandbox-next` | Cloudflare Sandbox apps on the next SDK. | Free Cloudflare account; `npx wrangler login` |
| `sandbox-migrate-to-next` | Ports a Sandbox app from stable to next. | Free Cloudflare account; `npx wrangler login` |
| `cloudflare-email-service` | Sends and receives transactional email with Cloudflare. | Free Cloudflare account; `npx wrangler login` |
| `cloudflare-one` | Zero Trust setup: Access, Gateway, WARP, Tunnel, DLP. | Free Cloudflare account; `npx wrangler login` |
| `cloudflare-one-migrations` | Plans moves from Zscaler, Palo Alto or a legacy VPN to Cloudflare One. | Free Cloudflare account; `npx wrangler login` |
| `turnstile-spin` | Adds Cloudflare Turnstile (bot protection) to a project end to end. | Free Cloudflare account; `npx wrangler login` |
| `web-perf` | Measures Core Web Vitals and finds what makes a page slow, using Chrome DevTools. | The chrome-devtools MCP server (the installer adds it) |

## Security and SEO

| Skill | What it does | Setup |
|---|---|---|
| `never-get-hacked` | Security audit and hardening for AI-built apps: auth, payments, uploads, leaked keys, deploys. | Nothing |
| `seo-checklist` | 20-point technical SEO audit of a live site, then fixes in your source. | `npm ci` inside the skill folder (`--tools` does it) |

## Marketing and writing

| Skill | What it does | Setup |
|---|---|---|
| `ad-creative-pro` | Audits ad creatives for fatigue and gaps, then writes new copy for Meta, Google, TikTok. | Nothing |
| `hormozi-ad-factory` | Generates hundreds of ad variations with Hormozi's Hook x Meat x CTA method. | Nothing. Its repo has no open licence, so it isn't copied here; `install.sh` installs it from the author's repo (needs Node) |
| `technical-blog-writing` | Technical blog posts with good structure and code examples. | Nothing |

## AI image and video generation

| Skill | What it does | Setup |
|---|---|---|
| `higgsfield-generate` | Images, video, 3D models and sound effects through Higgsfield (GPT Image 2, Seedance, Kling, Nano Banana and more), plus ad and UGC formats. | Higgsfield account with credits; CLI: `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \| sh`, then `higgsfield auth login` |
| `higgsfield-soul-id` | Trains a model on a face for consistent characters across images and video. | Higgsfield account with credits; CLI: `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \| sh`, then `higgsfield auth login` |
| `higgsfield-product-photoshoot` | Product photoshoots from a product image. | Higgsfield account with credits; CLI: `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \| sh`, then `higgsfield auth login` |
| `higgsfield-marketplace-cards` | Marketplace listing cards (Amazon, Etsy style). | Higgsfield account with credits; CLI: `curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh \| sh`, then `higgsfield auth login` |

## Video editing

| Skill | What it does | Setup |
|---|---|---|
| `resolve-editor` | Edits video inside DaVinci Resolve like an editor with an assistant team: logs footage, builds the cut, effects, QA. | DaVinci Resolve Studio 21.1+ (paid). `bash install.sh --tools` runs the skill's own setup |
| `resolve-colorist` | Full professional color grade of a Resolve timeline: per-clip matching, looks, judging, verification. | DaVinci Resolve Studio 21.1+ (paid). `bash install.sh --tools` runs the skill's own setup |
| `ai-reel-editor` | Turns raw talking-head footage into a finished vertical reel: syncs the mic, cuts dead air and retakes, adds captions and graphics, QA loop. | `brew install ffmpeg auto-editor`, whisper (see the skill) |

## Skills that come with the plugins

The installer adds these plugins. Their skills show up as `plugin:skill` (for example `superpowers:brainstorming`).

| Plugin | What you get |
|---|---|
| superpowers | The working method: brainstorming before building, written plans, test-driven development, systematic debugging, verification before saying done, parallel subagents, code review, git worktrees. It also makes Claude check for a matching skill before every task. |
| andrej-karpathy-skills | Guidelines that stop the usual LLM coding mistakes: overbuilding, unasked changes, hidden assumptions. |
| figma | Figma design to code, code to Figma, design systems, FigJam diagrams, slides, motion. Needs the Figma connector (see CONNECTORS.md). |
| firebase | Firebase projects, Firestore, auth and hosting through Firebase's own MCP server. Run `npx firebase-tools login` once. |
| brag | `/brag` turns a project's website into a short launch video. |
| design | Design critique, accessibility audits, design systems, UX copy, research synthesis, dev handoff. |
| marketing | Campaign plans, content, email sequences, SEO audits, competitor briefs, performance reports. |
| brand-voice | Finds a brand's voice from its documents, writes guidelines, checks content against them. |
| legal | Contract review, NDA triage, compliance checks, legal risk, briefs. Not legal advice. |

## Built into your Claude account

These come with a paid Claude plan, no install: Word (`docx`), PDF, PowerPoint (`pptx`), Excel (`xlsx`), `skill-creator` (make your own skills), `theme-factory`, `brand-guidelines`, `docs`, `morning` (daily brief), `google-workspace`, `import-memory`. If you don't see them, turn on Skills in claude.ai under Settings > Capabilities.

## Extras (not installed by default)

In `extras/`. Install them with `bash install.sh --extras`. Several overlap with skills above, which is why they're optional.

| Skill | What it does |
|---|---|
| `ad-creative` | Ad headlines and descriptions at scale. |
| `ads-creative` | Cross-platform ad creative quality audit. |
| `ai-seo` | Get cited by ChatGPT, Perplexity and AI Overviews. |
| `copywriting` | Marketing copy for any page. |
| `enhance-prompt` | Turns vague UI ideas into polished prompts for Google Stitch. |
| `remotion-best-practices` | Video made in React with Remotion. Not copied here (no open licence); `--extras` installs it from remotion-dev/skills. |
| `responsive-design` | Container queries, fluid type, modern responsive layouts. |
| `security-review` | Security checklist for auth, input and secrets. Note: same name as Claude Code's built-in /security-review, so installing it replaces the built-in. |
| `seo-audit` | SEO audit and diagnosis. |

## Where these came from

Most of these are open-source skills by other authors. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) lists the source and licence of every one, and the licence texts are in [`licenses/`](../licenses/). To get the newest version of a skill, run `npx skills add <owner/repo> --skill <name>` or ask Claude to update it from its source.

`resolve-editor`, `resolve-colorist`, `reel-reader`, `ai-reel-editor`, `humanizer`, `claude-setup-tuneup` and `seo-checklist` were written for this pack.
