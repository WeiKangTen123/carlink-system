#!/usr/bin/env node
/**
 * The side menu on phones (owner, 26 Nov 2026). At 390 px wide the menu
 * stayed open and left the page about 100 px, so every page was cramped.
 * The fix: at 900 px and below the menu folds away and the top bar's menu
 * button opens it as a drawer over the page; a link, its close button, the
 * backdrop or Esc closes it; focus moves into it and back to the button.
 * Wider screens keep the side menu exactly as before. This checks, without
 * a browser:
 *
 * lib/sideMenu.ts
 *   - the breakpoint is (max-width: 900px).
 * app/globals.css
 *   - the "Side menu" block is there once, before the Stage 1 block (which
 *     stays last: scripts/check-prices-ui.mjs);
 *   - outside any @media the menu button, the drawer's close button and the
 *     backdrop are display: none, and the one .sidebar rule is the desktop
 *     side menu as before (224px, sticky, 100vh);
 *   - in the @media for the breakpoint: the menu is fixed, solid, slid off
 *     (translateX(-100%)) and hidden until .sidebar.open; the button, close
 *     button and backdrop show; the drawer sits over the backdrop, both over
 *     the top bar;
 *   - no other @media changes .sidebar.
 * components/AppShell.tsx and components/Sidebar.tsx
 *   - the wiring: the button (aria-controls, aria-expanded), the backdrop,
 *     Esc, leaving the breakpoint, every link and the close button closing
 *     it, the page inert while it's open, focus in and back, and the dialog
 *     role only while open.
 *
 *   node apps/dashboard/scripts/check-side-menu.mjs
 *
 * Prints one line per check and exits 1 if any fails. The live check (a
 * browser opening and closing the drawer at 390x844, 768x1024 and the
 * desktop sizes) is in the PR.
 */
import { readFileSync } from "node:fs";

// lib/*.ts is imported directly (type stripping, Node 22.18+); quiet the
// "module type not specified" notice that brings, as the other checks do.
process.removeAllListeners("warning");
process.on("warning", (w) => {
  if (w.code !== "MODULE_TYPELESS_PACKAGE_JSON") console.error(w.stack ?? w.message);
});
const { SIDE_MENU_DRAWER_MAX_WIDTH, SIDE_MENU_DRAWER_QUERY, SIDE_MENU_ID } = await import("../lib/sideMenu.ts");

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const rawCss = read("../app/globals.css");
const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, "");
const shell = read("../components/AppShell.tsx");
const sidebar = read("../components/Sidebar.tsx");
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failed += 1;
};

// ---- lib/sideMenu.ts ---------------------------------------------------------
check(SIDE_MENU_DRAWER_MAX_WIDTH === 900, `breakpoint is 900px (${SIDE_MENU_DRAWER_MAX_WIDTH})`);
check(SIDE_MENU_DRAWER_QUERY === "(max-width: 900px)", `query is "(max-width: 900px)" (${SIDE_MENU_DRAWER_QUERY})`);
check(typeof SIDE_MENU_ID === "string" && /^[a-z][\w-]*$/.test(SIDE_MENU_ID), `menu id "${SIDE_MENU_ID}"`);

// ---- globals.css: the block ----------------------------------------------------
const START = "/* ---- Side menu: a drawer on narrow screens ---- */";
const END = "/* ---- end Side menu ---- */";
const S1 = "/* ---- Stage 1: prices ---- */";
check(rawCss.split(START).length === 2 && rawCss.split(END).length === 2, "globals.css: the Side menu block once");
check(rawCss.indexOf(START) < rawCss.indexOf(END) && rawCss.indexOf(END) < rawCss.indexOf(S1),
  "globals.css: the Side menu block comes before the Stage 1 block");
check(rawCss.trimEnd().endsWith("/* ---- end Stage 1 ---- */"), "globals.css: the Stage 1 block is still last");

// Top-level statements: "selector { body }" or "@media (...) { rules }".
function parse(text) {
  const out = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("{", i);
    if (open < 0) break;
    const head = text.slice(i, open).trim();
    let depth = 1;
    let j = open + 1;
    while (j < text.length && depth > 0) {
      if (text[j] === "{") depth += 1;
      else if (text[j] === "}") depth -= 1;
      j += 1;
    }
    out.push({ head, body: text.slice(open + 1, j - 1) });
    i = j;
  }
  return out;
}
const top = parse(css);
const rulesOf = (stmts) => stmts.filter((s) => !s.head.startsWith("@")).map((s) => ({ sel: s.head, body: s.body }));
const has = (sel) => (r) => r.sel.split(",").map((s) => s.trim().replace(/\s+/g, " ")).includes(sel);
const decl = (body, prop) => {
  const m = [...body.matchAll(new RegExp(`(?:^|;|\\s)${prop}\\s*:\\s*([^;]+)`, "g"))];
  return m.length ? m[m.length - 1][1].trim() : undefined;
};
const merged = (rules, sel) => rules.filter(has(sel)).map((r) => r.body).join(";");

// Outside any @media: desktop as before, the new pieces hidden.
const plain = rulesOf(top);
for (const sel of [".side-menu-button", ".sidebar-close", ".side-menu-backdrop"]) {
  check(decl(merged(plain, sel), "display") === "none", `globals.css: ${sel} is display: none outside @media (hidden on desktop)`);
}
const sidebarRules = plain.filter(has(".sidebar"));
check(sidebarRules.length === 1, `globals.css: one .sidebar rule outside @media (${sidebarRules.length})`);
const desk = sidebarRules.map((r) => r.body).join(";");
check(decl(desk, "width") === "224px" && decl(desk, "position") === "sticky" && decl(desk, "height") === "100vh",
  "globals.css: desktop .sidebar as before (width 224px, position sticky, height 100vh)");
check(!plain.some(has(".sidebar.open")), "globals.css: no .sidebar.open rule outside @media");

// The @media for the breakpoint.
const norm = (q) => q.replace(/\s+/g, " ").replace(/\(\s*/g, "(").replace(/\s*\)/g, ")").replace(/\s*:\s*/g, ": ").trim();
const drawerMedia = top.filter((s) => norm(s.head) === `@media ${norm(SIDE_MENU_DRAWER_QUERY)}`);
const drawerRules = drawerMedia.flatMap((m) => rulesOf(parse(m.body)));
const sb = merged(drawerRules, ".sidebar");
check(drawerRules.some(has(".sidebar")), `globals.css: @media ${SIDE_MENU_DRAWER_QUERY} has a .sidebar rule`);
check(decl(sb, "position") === "fixed", "drawer: .sidebar position: fixed (out of the page's flow, so the page gets the full width)");
check(decl(sb, "transform") === "translateX(-100%)" && decl(sb, "visibility") === "hidden",
  "drawer: .sidebar slid off (translateX(-100%)) and visibility: hidden (out of the tab order) until opened");
check(decl(sb, "background") === "var(--nav-bg-solid)", "drawer: .sidebar background var(--nav-bg-solid) (solid over the page)");
const sbOpen = merged(drawerRules, ".sidebar.open");
check(decl(sbOpen, "visibility") === "visible" && /^(none|translateX\(0(px|%)?\))$/.test(decl(sbOpen, "transform") || ""),
  "drawer: .sidebar.open visible and slid in");
check(!/visibility/.test(decl(sbOpen, "transition") || ""), "drawer: .sidebar.open shows at once (no delayed visibility), so it can take the focus");
for (const sel of [".side-menu-button", ".sidebar-close", ".side-menu-backdrop"]) {
  const d = decl(merged(drawerRules, sel), "display");
  check(Boolean(d) && d !== "none", `drawer: ${sel} shown (display: ${d})`);
}
const backdrop = merged(drawerRules, ".side-menu-backdrop");
check(decl(backdrop, "position") === "fixed" && decl(backdrop, "inset") === "0", "drawer: the backdrop covers the screen (fixed, inset 0)");
const z = (body) => Number(decl(body, "z-index"));
const topBarZ = z(merged(plain, ".top-bar"));
check(z(sb) > z(backdrop) && z(backdrop) > topBarZ && z(sb) < 900,
  `drawer: z-index drawer ${z(sb)} > backdrop ${z(backdrop)} > top bar ${topBarZ}, under dialogs (900+)`);
check(decl(merged(drawerRules, "body.side-menu-open"), "overflow") === "hidden", "drawer: the page behind doesn't scroll (body.side-menu-open)");

// Nothing else moves the side menu at another width.
const otherMedia = top.filter((s) => s.head.startsWith("@media") && !drawerMedia.includes(s));
const stray = otherMedia.filter((m) =>
  rulesOf(parse(m.body)).some((r) => r.sel.split(",").some((s) => /^\.sidebar(\.open)?$/.test(s.trim())) && !/^\s*transition\s*:[^;]+;?\s*$/.test(r.body)),
);
check(stray.length === 0, `globals.css: no other @media changes .sidebar (${stray.map((m) => m.head).join(" | ") || "none"})`);

// ---- AppShell.tsx --------------------------------------------------------------
const flat = (s) => s.replace(/\s+/g, " ");
// The JSX opening tag around index `at`: from its "<" to the ">" that closes
// it, skipping any ">" inside {...} (an arrow function) or quotes.
function openingTag(src, at) {
  const start = src.lastIndexOf("<", at);
  if (at < 0 || start < 0) return "";
  let depth = 0;
  let quote = null;
  for (let i = start + 1; i < src.length; i += 1) {
    const c = src[i];
    if (quote) {
      if (c === quote) quote = null;
    } else if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "{") depth += 1;
    else if (c === "}") depth -= 1;
    else if (c === ">" && depth === 0) return src.slice(start, i + 1);
  }
  return "";
}
const sh = flat(shell);
check(/import \{[^}]*SIDE_MENU_DRAWER_QUERY[^}]*\} from "@\/lib\/sideMenu"/.test(sh) && /import \{[^}]*SIDE_MENU_ID[^}]*\} from "@\/lib\/sideMenu"/.test(sh),
  "AppShell: uses the breakpoint and id from lib/sideMenu.ts");
const btnAt = sh.indexOf('className="side-menu-button"');
const btnTag = openingTag(sh, btnAt);
check(btnAt > sh.indexOf('<header className="top-bar">') && sh.indexOf('<header className="top-bar">') > 0, "AppShell: the menu button is in the top bar");
check(btnTag.includes('type="button"') && btnTag.includes("aria-controls={SIDE_MENU_ID}") && btnTag.includes("aria-expanded={menuOpen}") && /aria-label="[^"]+"/.test(btnTag),
  "AppShell: the menu button has type, aria-controls, aria-expanded and aria-label");
check(btnTag.includes("onClick={() => setMenuOpen(true)}") && btnTag.includes("ref={menuButtonRef}"), "AppShell: the menu button opens the drawer and is the focus target on close");
check(/<Sidebar [^>]*open=\{menuOpen\}[^>]*onClose=\{closeMenu\}/.test(sh), "AppShell: Sidebar gets open={menuOpen} and onClose={closeMenu}");
check(/\{menuOpen && <div className="side-menu-backdrop" onClick=\{closeMenu\}/.test(sh), "AppShell: the backdrop shows only while open and closes it when tapped");
check(/<div className="main-column" inert=\{menuOpen\}>/.test(sh), "AppShell: the page is inert while the drawer is open");
check(/e\.key === "Escape"\) setMenuOpen\(false\)/.test(sh), "AppShell: Esc closes it");
check(/window\.matchMedia\(SIDE_MENU_DRAWER_QUERY\)/.test(sh) && /if \(!narrow\.matches\) setMenuOpen\(false\)/.test(sh),
  "AppShell: widening past the breakpoint closes it");
check(/if \(menuPath !== pathname\) \{ setMenuPath\(pathname\); setMenuOpen\(false\);/.test(sh), "AppShell: a page change closes it");
check(/if \(menuWasOpen\.current && !menuOpen\) menuButtonRef\.current\?\.focus\(\)/.test(sh), "AppShell: focus goes back to the menu button on close");
check(/document\.body\.classList\.add\("side-menu-open"\)/.test(sh) && /document\.body\.classList\.remove\("side-menu-open"\)/.test(sh),
  "AppShell: body.side-menu-open set while open and cleared after");
check(sh.includes('className="top-bar-tagline"'), "AppShell: the strapline has its class (hidden on phones)");

// ---- Sidebar.tsx ---------------------------------------------------------------
const sd = flat(sidebar);
check(/import \{ SIDE_MENU_ID \} from "@\/lib\/sideMenu"/.test(sd) && sd.includes("id={SIDE_MENU_ID}"), "Sidebar: the aside has the menu id");
check(sd.includes('className={open ? "sidebar open" : "sidebar"}'), 'Sidebar: class "sidebar" closed (desktop as before), "sidebar open" open');
check(/\{\.\.\.\(open \? \{ role: "dialog", "aria-modal": true, "aria-label": "[^"]+" \} : \{\}\)\}/.test(sd),
  "Sidebar: a modal dialog only while open (a plain aside on desktop)");
const links = [...sd.matchAll(/<Link\b/g)].map((m) => openingTag(sd, m.index + 1));
check(links.length >= 2 && links.every((l) => l.includes("onClick={onClose}")), `Sidebar: every link closes the drawer (${links.length} <Link>s)`);
const closeAt = sd.indexOf('className="sidebar-close"');
const closeTag = openingTag(sd, closeAt);
check(closeTag.includes("onClick={onClose}") && closeTag.includes("ref={closeRef}") && /aria-label="[^"]+"/.test(closeTag) && closeTag.includes('type="button"'),
  "Sidebar: the close button closes it, is labelled, and is the focus target on open");
check(/if \(open\) closeRef\.current\?\.focus\(\)/.test(sd), "Sidebar: focus moves into the drawer when it opens");

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
