---
name: seo-checklist
description: Technical SEO audit and fix workflow for a static or mostly-static site (the 20-point "Google isn't showing my vibe-coded site" checklist: sitemap, robots, noindex, canonical, titles, descriptions, one H1, heading hierarchy, alt text, schema, internal links, broken links, image weight, Core Web Vitals, mobile, HTTPS, slugs, OG image, Search Console, backlinks). Use when a site is not showing in Google, when asked for an SEO audit, before a launch, or after a redesign. Runs a real audit against the live site first, fixes in source, re-audits, and hands the owner only the two items nobody else can do.
---

# SEO Checklist

Source: an Instagram reel by @joshtheaiguy (2026-09-12) listing what to ask an agent to add to a vibe-coded site. The list is right; the reel's "make no mistakes" is not a method. This skill turns it into one: measure, fix in source, measure again, and never claim a fix the audit cannot see.

## Workflow

1. **Audit the live site** (or a local preview) before touching anything:
   ```bash
   ~/.claude/skills/seo-checklist/audit.mjs https://example.com / /page-one /page-two
   ```
   With no paths it audits `/` and every same-host link found there. It writes `seo-audit-<host>-<date>.md` in the current directory and prints the table. Every row is evidence (lengths, counts, status codes, headers), not opinion. The script needs `jsdom`: run `npm ci` in the skill directory once.
2. **Read `references/fixes.md`** for the fix recipe of each failing item. Fix in the site's *source* (templates, data, build), never in built output. Keep the site's own conventions (its build, its data contract, its tests).
3. **Decide the deliberate exceptions and write them down**: an ad landing page or a dynamic shell may stay `noindex`; a decorative image may keep `alt=""`; a redirect target may not need a canonical. The audit report marks these `deliberate` only when the operator says so.
4. **Re-run the audit** on the built output (serve `dist/` locally) and again on the live site after deploy. Paste both tables in the PR.
5. **Core Web Vitals**: run Lighthouse mobile before and after (`audit.mjs --lighthouse`, needs `npx lighthouse`), record LCP, CLS, TBT and the performance score. Typical wins on a static site: self-hosted fonts instead of a render-blocking Google Fonts stylesheet, `width`/`height` on images, `loading="lazy"` below the fold, `preload="metadata"` on hero video, stills under 200 KB, loops under 900 KB.
6. **Hand the owner the two items only they can do**, with exact steps: Search Console verification (DNS TXT at the registrar, or a verification meta tag the build can render from a data file) and the backlink strategy (`references/fixes.md` has a template to fill with the site's real linkable assets).

## The 20 items and what "done" means

| # | Item | Done when |
|---|---|---|
| 1 | sitemap.xml | 200, `application/xml`, lists every indexable canonical URL, nothing noindex, `lastmod` optional |
| 2 | robots.txt | 200, `text/plain`, `Sitemap:` line, disallows only admin/private paths (never a page that must be crawled to see its noindex) |
| 3 | noindex | Only on pages meant to stay out (ad landers, shells, thank-you pages); the home page and money pages are indexable |
| 4 | canonical | Every indexable page has one self-referencing canonical on the one true host and URL form |
| 5 | meta titles | 30 to 60 characters, unique, brand at the end |
| 6 | meta descriptions | 70 to 160 characters, unique, a sentence a person would click |
| 7 | one H1 | Exactly one per page, the page's subject |
| 8 | heading hierarchy | No level skipped going down (h2 to h4 is a skip); footer column labels are not headings |
| 9 | alt text | Every `<img>` has an `alt` attribute; content photos describe the scene, decorative ones are `alt=""` with `aria-hidden` |
| 10 | schema markup | Valid JSON-LD that is true: Organization + WebSite on home, FAQPage where a FAQ exists, BreadcrumbList on inner pages, Article on articles |
| 11 | internal links | Every indexable page is reachable from the nav or footer; money pages link to each other and to articles |
| 12 | broken links | Zero non-200 internal links; retired URLs answer 301 |
| 13 | compress images | Stills under 200 KB, hero loops under 900 KB, unused files deleted, `width`/`height` set |
| 14 | Core Web Vitals | Lighthouse mobile: LCP under 2.5 s, CLS under 0.1, TBT under 200 ms |
| 15 | mobile | Viewport meta, no horizontal scroll at 375, 44 px targets |
| 16 | HTTPS | http answers 301 to https, one host (www or apex) answers 301 to the other, HSTS header |
| 17 | clean slugs | Lowercase, hyphens, no extensions in links, `.html` and trailing-slash variants 301 to the canonical form |
| 18 | OG image | 1200x630 `og:image` (absolute URL) plus `og:title`, `og:description`, `og:url`, `twitter:card` on every indexable page |
| 19 | Search Console | Property verified, sitemap submitted, owner has access (owner action) |
| 20 | backlinks | A written strategy with the site's linkable asset, target lists and the first ten outreach targets (owner action) |

## Rules

- No fix without a before and after row in the audit.
- Do not remove `noindex` from a page without saying which page and why; it is a publishing decision.
- Schema must describe what is on the page. No invented reviews, ratings, authors or addresses.
- Titles and descriptions are copy: match the site's voice, no keyword stuffing, no em dashes if the site bans them.
- Never edit `.htaccess` rules without testing the redirect chain (`curl -sI`) for: apex, www, http, `.html`, trailing slash, the admin path, and one retired URL.
