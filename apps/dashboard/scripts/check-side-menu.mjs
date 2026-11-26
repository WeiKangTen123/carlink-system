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
 *   - desktop is frozen: outside any @media, .app-shell, .sidebar, .top-bar
 *     and .main-column each have one rule, exactly as on master, and no
 *     other selector mentions them; the menu button, the drawer's close
 *     button and the backdrop are display: none;
 *   - in the @media for the breakpoint: the menu is fixed, solid, slid off
 *     (translateX(-100%)) and hidden until .sidebar.open; the button, close
 *     button and backdrop show; the drawer sits over the backdrop, both over
 *     the top bar; toasts are hidden while it's open;
 *   - no other @media touches those four classes (bar a reduced-motion
 *     transition).
 * components/AppShell.tsx and components/Sidebar.tsx
 *   - the wiring: the button (aria-controls, aria-expanded; it won't open
 *     over another dialog), the backdrop, Esc (only when meant for the
 *     menu), leaving the breakpoint, every link and the close button closing
 *     it, the page inert while it's open, focus in and back, and each
 *     listener added and removed in pairs on the right target.
 *
 *   node apps/dashboard/scripts/check-side-menu.mjs
 *
 * Prints one line per check and exits 1 if any fails. This is a static
 * check: what the drawer does in a browser (opening and closing it at
 * 390x844, 768x1024 and the desktop sizes) is tested live, see the PR; CI
 * has no browser test for it.
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
const rulesOf = (stmts) => stmts.filter((s) => !s.head.startsWith("@")).map((s) => ({ sel: s.head.replace(/\s+/g, " "), body: s.body }));
const has = (sel) => (r) => r.sel.split(",").map((s) => s.trim()).includes(sel);
const decl = (body, prop) => {
  const m = [...body.matchAll(new RegExp(`(?:^|;|\\s)${prop}\\s*:\\s*([^;]+)`, "g"))];
  return m.length ? m[m.length - 1][1].trim() : undefined;
};
const decls = (body) => body.split(";").map((d) => d.trim().replace(/\s+/g, " ")).filter(Boolean);
const merged = (rules, sel) => rules.filter(has(sel)).map((r) => r.body).join(";");

// ---- Desktop, frozen: the shell's rules outside any @media, as on master ----
const plain = rulesOf(top);
const MASTER = {
  ".app-shell": ["display: flex", "min-height: 100vh"],
  ".sidebar": [
    "width: 224px", "flex-shrink: 0", "display: flex", "flex-direction: column", "gap: 4px", "padding: 20px 14px",
    "background: var(--nav-bg)", "border-right: 1px solid var(--border-color)", "position: sticky", "top: 0",
    "height: 100vh", "overflow-y: auto",
  ],
  ".top-bar": [
    "position: sticky", "top: 0", "z-index: 100", "display: flex", "align-items: center", "gap: 20px",
    "padding: 12px 28px", "background: var(--nav-bg-solid)", "border-bottom: 1px solid var(--border-color)",
  ],
  ".main-column": ["flex: 1", "min-width: 0", "display: flex", "flex-direction: column"],
};
const SHELL = /\.(app-shell|sidebar|top-bar|main-column)(?![\w-])/;
for (const [sel, want] of Object.entries(MASTER)) {
  const found = plain.filter((r) => r.sel === sel);
  check(found.length === 1 && JSON.stringify(decls(found[0].body)) === JSON.stringify(want),
    `desktop: one "${sel}" rule outside @media, exactly as on master`);
}
const others = plain.filter((r) => SHELL.test(r.sel) && !(r.sel in MASTER));
check(others.length === 0, `desktop: no other selector outside @media touches the shell (${others.map((r) => r.sel).join(" | ") || "none"})`);
for (const sel of [".side-menu-button", ".sidebar-close", ".side-menu-backdrop"]) {
  check(decl(merged(plain, sel), "display") === "none", `desktop: ${sel} is display: none outside @media`);
}
check(!plain.some((r) => /\.top-bar-tagline(?![\w-])/.test(r.sel)), "desktop: the strapline has no rule outside @media");

// ---- The @media for the breakpoint ----------------------------------------------
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
check(decl(merged(drawerRules, "body.side-menu-open .intake-toast"), "visibility") === "hidden",
  "drawer: toasts (.intake-toast, over the drawer at 1100) hidden while it's open, so taps can't fall through them");

// Nothing else moves the shell at another width (a reduced-motion
// transition on the drawer is all another @media may set).
const otherMedia = top.filter((s) => s.head.startsWith("@") && !drawerMedia.includes(s));
const stray = otherMedia.filter((m) =>
  rulesOf(parse(m.body)).some((r) => SHELL.test(r.sel) && !(/^\.sidebar(, \.sidebar\.open)?$/.test(r.sel) && /^\s*transition\s*:\s*none\s*;?\s*$/.test(r.body))),
);
check(stray.length === 0, `globals.css: no other @media touches the shell (${stray.map((m) => m.head).join(" | ") || "none"})`);

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
// The {...} block that opens at the first "{" at or after `from`.
function block(src, from) {
  const open = src.indexOf("{", from);
  if (from < 0 || open < 0) return "";
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}" && --depth === 0) return src.slice(open, i + 1);
  }
  return "";
}
const count = (src, s) => src.split(s).length - 1;
const sh = flat(shell);
check(/import \{[^}]*SIDE_MENU_DRAWER_QUERY[^}]*\} from "@\/lib\/sideMenu"/.test(sh) && /import \{[^}]*SIDE_MENU_ID[^}]*\} from "@\/lib\/sideMenu"/.test(sh),
  "AppShell: uses the breakpoint and id from lib/sideMenu.ts");
const btnAt = sh.indexOf('className="side-menu-button"');
const btnTag = openingTag(sh, btnAt);
check(btnAt > sh.indexOf('<header className="top-bar">') && sh.indexOf('<header className="top-bar">') > 0, "AppShell: the menu button is in the top bar");
check(btnTag.includes('type="button"') && btnTag.includes("aria-controls={SIDE_MENU_ID}") && btnTag.includes("aria-expanded={menuOpen}") && /aria-label="[^"]+"/.test(btnTag),
  "AppShell: the menu button has type, aria-controls, aria-expanded and aria-label");
check(btnTag.includes("onClick={openMenu}") && btnTag.includes("ref={menuButtonRef}"), "AppShell: the menu button opens the drawer and is the focus target on close");
check(/const openMenu = \(\) => \{ if \(!anotherDialogIsOpen\(\)\) setMenuOpen\(true\); \};/.test(sh) &&
  /function anotherDialogIsOpen\(\): boolean \{ return document\.querySelector\(`\[aria-modal="true"\]:not\(#\$\{SIDE_MENU_ID\}\)`\) !== null; \}/.test(sh),
  "AppShell: the menu won't open while another dialog (aria-modal) is up");
check(/<Sidebar [^>]*open=\{menuOpen\}[^>]*onClose=\{closeMenu\}/.test(sh), "AppShell: Sidebar gets open={menuOpen} and onClose={closeMenu}");
check(/\{menuOpen && <div className="side-menu-backdrop" onClick=\{closeMenu\}/.test(sh), "AppShell: the backdrop shows only while open and closes it when tapped");
check(/<div className="main-column" inert=\{menuOpen( \|\| undefined)?\}>/.test(sh), "AppShell: the page is inert while the drawer is open");
check(/if \(menuPath !== pathname\) \{ setMenuPath\(pathname\); setMenuOpen\(false\);/.test(sh), "AppShell: a page change closes it");

// The open-drawer effect: Esc, leaving the breakpoint, the scroll lock.
const effAt = sh.indexOf("useEffect(() => { if (!menuOpen) return;");
const eff = block(sh, effAt);
const cleanup = block(eff, eff.indexOf("return () =>"));
const setup = eff.slice(0, eff.indexOf("return () =>"));
check(effAt > 0 && cleanup.length > 0, "AppShell: found the open-drawer effect and its cleanup");
check(setup.includes("const narrow = window.matchMedia(SIDE_MENU_DRAWER_QUERY);"), "AppShell: the effect watches the breakpoint query");
check(/const onKey = \(e: KeyboardEvent\) => \{ if \(e\.key === "Escape" && !e\.defaultPrevented && focusIsInMenu\(\)\) setMenuOpen\(false\); \};/.test(setup),
  "AppShell: Esc closes it, only when focus is in the menu and no dialog over it handled the key");
check(/function focusIsInMenu\(\): boolean \{ const active = document\.activeElement; return !active \|\| active === document\.body \|\| Boolean\(document\.getElementById\(SIDE_MENU_ID\)\?\.contains\(active\)\); \}/.test(sh),
  "AppShell: focusIsInMenu() is true only for focus in the menu or on the page body");
check(/const onResize = \(\) => \{ if \(!narrow\.matches\) setMenuOpen\(false\); \};/.test(setup), "AppShell: leaving the breakpoint closes it");
const early = setup.indexOf("if (!narrow.matches) { onResize(); return; }");
check(early > 0 && early < setup.indexOf("addEventListener"), "AppShell: already past the breakpoint when the effect runs: closed at once, before subscribing");
const PAIRS = [
  ['document.addEventListener("keydown", onKey);', 'document.removeEventListener("keydown", onKey);'],
  ['narrow.addEventListener("change", onResize);', 'narrow.removeEventListener("change", onResize);'],
  ['document.body.classList.add("side-menu-open");', 'document.body.classList.remove("side-menu-open");'],
];
for (const [add, remove] of PAIRS) {
  check(count(setup, add) === 1 && count(cleanup, remove) === 1 && count(sh, add) === 1 && count(sh, remove) === 1,
    `AppShell: ${add.replace(/;$/, "")} in the effect, ${remove.replace(/;$/, "")} in its cleanup`);
}
check(count(sh, "addEventListener(") === 2 && count(sh, "removeEventListener(") === 2, "AppShell: no other listeners added or removed");
check(/if \(menuWasOpen\.current && !menuOpen && focusIsInMenu\(\)\) menuButtonRef\.current\?\.focus\(\)/.test(sh),
  "AppShell: focus goes back to the menu button on close, unless it moved on to a dialog over the menu");
check(sh.includes('className="top-bar-tagline"'), "AppShell: the strapline has its class (hidden on phones)");

// ---- Sidebar.tsx ---------------------------------------------------------------
const sd = flat(sidebar);
check(/import \{ SIDE_MENU_ID \} from "@\/lib\/sideMenu"/.test(sd) && sd.includes("id={SIDE_MENU_ID}"), "Sidebar: the aside has the menu id");
const asideTag = openingTag(sd, sd.search(/<aside\s/));
check(asideTag.includes('className={open ? "sidebar open" : "sidebar"}'), 'Sidebar: class "sidebar" closed (desktop as before), "sidebar open" open');
check(!/\brole\b|aria-modal/.test(asideTag) && asideTag.includes('aria-label={open ? "Menu" : undefined}'),
  'Sidebar: still an <aside> with no role (dialog is not allowed on one), labelled "Menu" only while open');
const links = [...sd.matchAll(/<Link\b/g)].map((m) => openingTag(sd, m.index + 1));
check(links.length >= 2 && links.every((l) => l.includes("onClick={onClose}")), `Sidebar: every link closes the drawer (${links.length} <Link>s)`);
const closeAt = sd.indexOf('className="sidebar-close"');
const closeTag = openingTag(sd, closeAt);
check(closeTag.includes("onClick={onClose}") && closeTag.includes("ref={closeRef}") && /aria-label="[^"]+"/.test(closeTag) && closeTag.includes('type="button"'),
  "Sidebar: the close button closes it, is labelled, and is the focus target on open");
check(/if \(open\) closeRef\.current\?\.focus\(\)/.test(sd), "Sidebar: focus moves into the drawer when it opens");

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
