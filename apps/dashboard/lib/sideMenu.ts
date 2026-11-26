/**
 * The side menu on narrow screens (owner, 26 Nov 2026). On a phone the menu
 * stayed open and left the page only about 100 px wide. At this width and
 * below it folds away: the top bar's menu button opens it as a drawer over
 * the page, and a link, the backdrop or Esc closes it
 * (components/AppShell.tsx, components/Sidebar.tsx). Wider screens keep the
 * side menu exactly as before.
 *
 * The "Side menu: a drawer on narrow screens" block in app/globals.css uses
 * the same width in its @media; scripts/check-side-menu.mjs checks the two
 * agree.
 */
export const SIDE_MENU_DRAWER_MAX_WIDTH = 900;

/** Matches while the side menu is a drawer. */
export const SIDE_MENU_DRAWER_QUERY = `(max-width: ${SIDE_MENU_DRAWER_MAX_WIDTH}px)`;

/** The side menu's id, for the menu button's aria-controls. */
export const SIDE_MENU_ID = "side-menu";
