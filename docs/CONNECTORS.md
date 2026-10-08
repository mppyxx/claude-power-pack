# Connectors, extensions and MCP servers

Skills tell Claude *how* to do things. Connectors give it *access*: your email, files, design tools, deploys. Connections are tied to your own accounts, so they can't be copied in a package. You sign in to each one yourself, once. It takes about 15 minutes.

There are three kinds, set up in three places.

## 1. claude.ai connectors (the important ones)

Where: **claude.ai > Settings > Connectors > Browse connectors**. Search the name, click Connect, sign in. They then work in the Claude app, Claude Code and the desktop app.

| Connector | What Claude can do with it | Needs |
|---|---|---|
| Gmail | Search and read mail, write drafts, label and sort. It asks before sending anything. | Google account |
| Google Calendar | Read your schedule, create and move events, find free time | Google account |
| Google Drive | Search, read and create files | Google account |
| Notion | Search, create and update pages and databases | Notion account |
| Figma | Read designs into code, build designs and FigJam diagrams inside Figma | Figma account (free works) |
| Canva | Create, edit, resize and export designs | Canva account |
| Vercel | Deploy sites, read build and runtime logs, manage projects and domains | Vercel account (free works) |
| Make | Build and run automations between apps | Make account (free tier) |
| HubSpot | CRM contacts, deals, marketing reports | HubSpot account |
| Firecrawl | Web search and scraping straight from chat | Free Firecrawl account |
| Hugging Face | Search models, datasets, papers and Spaces | Hugging Face account |
| Higgsfield | AI image, video, audio and 3D generation, ad and UGC formats | Higgsfield account, paid credits |
| Ahrefs | SEO data: keywords, backlinks, rankings, site audits | Paid Ahrefs plan |
| Hostinger (optional) | Manage websites and hosting | Hostinger account |

Already built in, nothing to connect: **Claude Docs** (shareable documents) and **visualize** (charts and diagrams right in the chat).

Only connect what you'll use. Every connected app adds tools Claude has to look through, so twelve connectors you never touch make it slightly slower to pick the right one.

### Connectors inside the plugins

The design, marketing, brand-voice and legal plugins come with optional links to work tools: Slack, Asana, Linear, Intercom, Atlassian (Jira, Confluence), Box, Egnyte, DocuSign, Gong, Granola, Amplitude, Similarweb, Klaviyo, Supermetrics. If you use one of these, open Claude Code, type `/mcp`, pick it and sign in. Ignore the rest; they just show "needs auth" and do nothing.

## 2. Desktop extensions (Claude desktop app, Mac)

Where: **Claude desktop app > Settings > Extensions > Browse extensions**. Click install on each. These run on your computer and can reach local apps.

| Extension | What it does |
|---|---|
| Desktop Commander | Files and terminal on your machine from the chat app |
| Control your Mac | Runs AppleScript to automate Mac apps |
| MacOS-MCP | Clicks, types and reads the screen in any Mac app |
| Control Chrome | Opens, switches and reads Chrome tabs |
| Read and Write Apple Notes | Your Notes app |
| Read and Send iMessages | Your Messages app (it asks before sending) |
| pdf-viewer | Opens, searches and annotates PDFs |
| Figma | Local Figma desktop link (alongside the Figma connector) |
| Shadcn UI | shadcn/ui components, blocks and themes for web projects. Optional: a GitHub token in its settings for higher rate limits |

Optional, install if you need them: **Filesystem** (simple file access), **Apify** (thousands of ready-made scrapers, needs an Apify token), **Tableau** (needs a Tableau server), **Cloudglue** (video search and analysis, needs an API key), **Minutes** (records and searches meeting audio).

If you own **DaVinci Resolve Studio 21.1 or newer**, it ships its own Claude connection. In Resolve: File > Setup AI Assistants, pick Claude. For Claude Code, `bash install.sh --tools` also registers it. If the desktop app doesn't pick it up, run this in Terminal and confirm the install prompt in Claude: `open "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/DaVinciResolve.mcpb"`. The `resolve-editor` and `resolve-colorist` skills use it.

## 3. Built into the apps

| Feature | How to turn it on |
|---|---|
| Claude in Chrome | Install the Claude extension by Anthropic from the Chrome Web Store and sign in. Claude can then browse and act in your real Chrome. |
| Computer use | In the desktop app, Claude asks for permission per app the first time it needs to control one. Say yes per app. |
| Built-in browser | The desktop app's Code tab has its own browser pane. Nothing to set up. |
| Scheduled tasks | Built in. Ask "every weekday at 9, do X". |
| Memory | claude.ai > Settings > Capabilities > Memory: on. Claude Code also keeps its own memory per project automatically. |

## 4. MCP servers for Claude Code

The installer adds these, no keys needed:

| Server | Why |
|---|---|
| playwright | Full browser automation for testing web apps |
| chrome-devtools | Performance traces and Core Web Vitals (the `web-perf` skill uses it) |

The `firebase` plugin brings its own server. Run `npx firebase-tools login` once if you use Firebase.

Optional, add them yourself if you want them (each needs a free account and key from that site):

```bash
# 21st.dev Magic: ready-made React UI components and inspiration (key from 21st.dev)
claude mcp add --scope user --transport http 21st https://21st.dev/api/mcp --header "x-api-key: YOUR_21ST_KEY"
```

```bash
# fal.ai: hundreds of image and video models (sign in when /mcp asks)
claude mcp add --scope user --transport http fal https://mcp.fal.ai/mcp
```

To see what's connected at any time: type `/mcp` inside Claude Code.
