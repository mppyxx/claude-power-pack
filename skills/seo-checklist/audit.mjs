#!/usr/bin/env node
/* SEO checklist audit: measures a site against the 20 items in SKILL.md and
   writes a markdown report. Evidence only; the fixes live in references/fixes.md.

   usage: audit.mjs <base-url> [path ...] [--lighthouse] [--out file.md]
   With no paths: "/" plus every same-host link found on it (one level). */
import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "package.json"));
let JSDOM;
try { ({ JSDOM } = require("jsdom")); } catch { console.error(`jsdom missing: run "npm ci" in ${here}`); process.exit(2); }

const args = process.argv.slice(2);
const flags = { lighthouse: false, out: "" };
const positional = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--lighthouse") flags.lighthouse = true;
  else if (args[i] === "--out") flags.out = args[++i];
  else positional.push(args[i]);
}
const base = positional[0];
if (!base) { console.error("usage: audit.mjs <base-url> [path ...] [--lighthouse] [--out file.md]"); process.exit(2); }
const origin = new URL(base).origin;
const host = new URL(base).host;

const UA = "Mozilla/5.0 (compatible; seo-checklist-audit/1.0)";
async function get(url, opts = {}) {
  const res = await fetch(url, { redirect: "manual", headers: { "user-agent": UA, "accept-encoding": "gzip, br" }, ...opts });
  const text = opts.method === "HEAD" ? "" : await res.text();
  return { status: res.status, headers: res.headers, text, location: res.headers.get("location") || "" };
}
async function follow(url) {
  let cur = url;
  for (let i = 0; i < 5; i++) {
    const r = await get(cur);
    if (r.status >= 300 && r.status < 400 && r.location) { cur = new URL(r.location, cur).href; continue; }
    return { ...r, final: cur };
  }
  return { status: 0, headers: new Headers(), text: "", final: cur };
}

const pageRows = [];
const siteRows = [];
const bad = (s) => `❌ ${s}`;
const ok = (s) => `✅ ${s}`;
const warn = (s) => `⚠️ ${s}`;

function auditPage(pathname, html, headers) {
  const doc = new JSDOM(html).window.document;
  const q = (s) => doc.querySelector(s);
  const all = (s) => [...doc.querySelectorAll(s)];
  const r = { path: pathname };
  const title = q("title")?.textContent.trim() || "";
  r.title = !title ? bad("none") : title.length < 30 || title.length > 60 ? warn(`${title.length} chars`) : ok(`${title.length} chars`);
  const desc = q('meta[name="description"]')?.getAttribute("content") || "";
  r.description = !desc ? bad("none") : desc.length < 70 || desc.length > 160 ? warn(`${desc.length} chars`) : ok(`${desc.length} chars`);
  const robots = q('meta[name="robots"]')?.getAttribute("content") || "";
  r.robots = /noindex/i.test(robots) ? warn("noindex") : ok("indexable");
  const canonical = q('link[rel="canonical"]')?.getAttribute("href") || "";
  const expected = origin + pathname;
  r.canonical = !canonical ? bad("none") : canonical.replace(/\/$/, "") === expected.replace(/\/$/, "") ? ok("self") : warn(canonical);
  const og = ["og:title", "og:description", "og:image", "og:url"].filter((p) => !q(`meta[property="${p}"]`)?.getAttribute("content"));
  const tw = q('meta[name="twitter:card"]');
  r.og = og.length === 0 && tw ? ok("complete") : bad(`missing ${[...og, ...(tw ? [] : ["twitter:card"])].join(", ")}`);
  const ld = all('script[type="application/ld+json"]');
  const types = [];
  let ldBad = false;
  for (const s of ld) { try { const j = JSON.parse(s.textContent); for (const g of Array.isArray(j) ? j : [j]) types.push(g["@type"]); } catch { ldBad = true; } }
  r.schema = ldBad ? bad("invalid JSON") : types.length ? ok(types.join("+")) : bad("none");
  const h1 = all("h1").length;
  r.h1 = h1 === 1 ? ok("1") : bad(String(h1));
  const levels = all("h1,h2,h3,h4,h5,h6").map((h) => Number(h.tagName[1]));
  const jumps = [];
  for (let i = 1; i < levels.length; i++) if (levels[i] > levels[i - 1] + 1) jumps.push(`h${levels[i - 1]}>h${levels[i]}`);
  r.headings = jumps.length ? bad([...new Set(jumps)].join(" ")) : ok("no skips");
  const imgs = all("img");
  const noAlt = imgs.filter((i) => !i.hasAttribute("alt")).length;
  const emptyAlt = imgs.filter((i) => i.getAttribute("alt") === "").length;
  const noDims = imgs.filter((i) => !(i.hasAttribute("width") && i.hasAttribute("height"))).length;
  r.images = imgs.length === 0 ? ok("none") : noAlt ? bad(`${noAlt}/${imgs.length} without alt attribute`) : emptyAlt === imgs.length ? warn(`${emptyAlt}/${imgs.length} alt="" (decorative?)`) : ok(`${imgs.length - emptyAlt}/${imgs.length} described`);
  r.imageDims = imgs.length === 0 ? ok("n/a") : noDims ? warn(`${noDims}/${imgs.length} lack width/height`) : ok("all sized");
  const hrefs = all("a[href]").map((a) => a.getAttribute("href")).filter((h) => h && !/^(#|mailto:|tel:|javascript:)/.test(h));
  const internal = hrefs.filter((h) => { try { return new URL(h, origin).host === host; } catch { return false; } });
  r.internalLinks = internal.length >= 3 ? ok(String(new Set(internal).size)) : warn(String(new Set(internal).size));
  r._internal = [...new Set(internal.map((h) => new URL(h, origin).href))];
  const fontCss = all('link[rel="stylesheet"][href^="http"]').map((l) => new URL(l.href).host).filter((h) => h !== host);
  r.fonts = fontCss.length ? warn(`external stylesheet: ${[...new Set(fontCss)].join(", ")}`) : ok("self-hosted");
  r.viewport = q('meta[name="viewport"]') ? ok("set") : bad("missing");
  r.lang = doc.documentElement.lang ? ok(doc.documentElement.lang) : bad("missing");
  r.weight = `${Math.round(html.length / 1024)} KB`;
  r.encoding = headers.get("content-encoding") ? ok(headers.get("content-encoding")) : warn("none");
  return r;
}

async function checkSite() {
  // 1, 2: sitemap and robots
  for (const [name, type, must] of [["sitemap.xml", /xml/, /<urlset/], ["robots.txt", /text\/plain/, /Sitemap:/i]]) {
    const r = await follow(`${origin}/${name}`);
    const ct = r.headers.get("content-type") || "";
    siteRows.push([name, r.status !== 200 ? bad(`HTTP ${r.status}`) : !type.test(ct) ? bad(`served as ${ct.split(";")[0]}`) : !must.test(r.text) ? bad("wrong content") : ok(name === "sitemap.xml" ? `${(r.text.match(/<loc>/g) || []).length} urls` : "ok")]);
  }
  // 16: https and host canonicalisation
  const httpR = await get(`http://${host}/`);
  siteRows.push(["http to https", httpR.status === 301 && /^https:/.test(httpR.location) ? ok("301") : bad(`${httpR.status} ${httpR.location}`)]);
  const other = host.startsWith("www.") ? host.slice(4) : `www.${host}`;
  try {
    const alt = await get(`https://${other}/`);
    siteRows.push([`${other} to ${host}`, alt.status === 301 && alt.location.includes(host) ? ok("301") : alt.status === 200 ? bad("200, duplicate host") : warn(`${alt.status} ${alt.location}`)]);
  } catch (e) { siteRows.push([`${other}`, warn("unreachable: " + e.message.slice(0, 40))]); }
  const hsts = (await get(`${origin}/`)).headers.get("strict-transport-security");
  siteRows.push(["HSTS", hsts ? ok(hsts) : warn("no header")]);
  // 17: url variants of the first inner path
  const inner = positional.slice(1).find((p) => p !== "/") || "";
  if (inner) {
    const clean = inner.replace(/\/$/, "");
    const htmlV = await get(`${origin}${clean}.html`);
    siteRows.push([`${clean}.html`, htmlV.status === 301 ? ok(`301 to ${htmlV.location}`) : htmlV.status === 200 ? warn("200, duplicate of the extensionless URL (needs canonical or 301)") : ok(`${htmlV.status}`)]);
    const slashV = await get(`${origin}${clean}/`);
    siteRows.push([`${clean}/`, slashV.status === 301 ? ok(`301 to ${slashV.location}`) : slashV.status === 200 ? warn("200, duplicate of the no-slash URL") : bad(`HTTP ${slashV.status}`)]);
  }
}

async function run() {
  const first = await follow(origin + (positional[1] || "/"));
  let paths = positional.slice(1);
  if (paths.length === 0) {
    const doc = new JSDOM(first.text).window.document;
    const found = new Set(["/"]);
    for (const a of doc.querySelectorAll("a[href]")) {
      try { const u = new URL(a.getAttribute("href"), origin); if (u.host === host && !/\.(jpg|png|pdf|xml|txt)$/i.test(u.pathname)) found.add(u.pathname.replace(/\.html$/, "") || "/"); } catch { /* skip */ }
    }
    paths = [...found];
  }
  paths = paths.map((p) => (p.startsWith("/") ? p : "/" + p));
  positional.splice(1, positional.length, ...paths);

  const linkSet = new Set();
  for (const p of paths) {
    const r = await follow(origin + p);
    if (r.status !== 200) { pageRows.push({ path: p, title: bad(`HTTP ${r.status}`) }); continue; }
    const row = auditPage(p, r.text, r.headers);
    for (const l of row._internal) linkSet.add(l);
    pageRows.push(row);
  }
  // 12: broken internal links (every unique link seen on the audited pages)
  const broken = [];
  for (const l of linkSet) {
    const r = await get(l, { method: "HEAD" });
    const status = r.status === 405 ? (await get(l)).status : r.status;
    if (status >= 400 || status === 0) broken.push(`${status} ${l.replace(origin, "")}`);
  }
  siteRows.push(["broken internal links", broken.length ? bad(broken.join("; ")) : ok(`0 of ${linkSet.size}`)]);
  await checkSite();

  let lh = "";
  if (flags.lighthouse) {
    const out = spawnSync("npx", ["-y", "lighthouse", origin + (paths[0] || "/"), "--quiet", "--chrome-flags=--headless=new", "--only-categories=performance", "--form-factor=mobile", "--output=json", "--output-path=stdout"], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    try {
      const j = JSON.parse(out.stdout);
      const a = j.audits;
      lh = `\n## Lighthouse (mobile, ${paths[0] || "/"})\n\n| Metric | Value |\n|---|---|\n| Performance | ${Math.round(j.categories.performance.score * 100)} |\n| LCP | ${a["largest-contentful-paint"].displayValue} |\n| CLS | ${a["cumulative-layout-shift"].displayValue} |\n| TBT | ${a["total-blocking-time"].displayValue} |\n| Speed Index | ${a["speed-index"].displayValue} |\n`;
    } catch { lh = "\n## Lighthouse\n\nnot available (npx lighthouse failed)\n"; }
  }

  const cols = ["path", "title", "description", "robots", "canonical", "og", "schema", "h1", "headings", "images", "imageDims", "internalLinks", "fonts", "viewport", "lang", "weight", "encoding"];
  const table = ["| " + cols.join(" | ") + " |", "|" + cols.map(() => "---").join("|") + "|", ...pageRows.map((r) => "| " + cols.map((c) => r[c] ?? "").join(" | ") + " |")].join("\n");
  const site = ["| Check | Result |", "|---|---|", ...siteRows.map(([k, v]) => `| ${k} | ${v} |`)].join("\n");
  const date = new Date().toISOString().slice(0, 10);
  const md = `# SEO audit: ${host} (${date})\n\nBase: ${origin}\n\n## Site\n\n${site}\n\n## Pages\n\n${table}\n${lh}\nLegend: ✅ pass, ⚠️ review (may be deliberate), ❌ fix. Recipes: ~/.claude/skills/seo-checklist/references/fixes.md\n`;
  const outFile = flags.out || `seo-audit-${host}-${date}.md`;
  writeFileSync(outFile, md);
  console.log(md);
  console.log(`written: ${outFile}`);
}

run().catch((e) => { console.error(e); process.exit(1); });
