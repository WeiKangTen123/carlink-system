"use client";

import { createContext, useContext } from "react";
import type { CurrentUser } from "@/lib/api";

/** The signed-in user, loaded once by the root layout (app/layout.tsx).
 * Only for what the screen shows -- every permission is checked again by
 * the API. */
const CurrentUserContext = createContext<CurrentUser | null>(null);

export const CurrentUserProvider = CurrentUserContext.Provider;

export function useCurrentUser(): CurrentUser | null {
  return useContext(CurrentUserContext);
}

export function isAdmin(user: CurrentUser | null): boolean {
  return user?.role === "admin";
}

/** Admins, and staff ticked "Can see and work on all cases". Everyone else
 * sees only the cases they own. */
export function canViewAllCases(user: CurrentUser | null): boolean {
  return isAdmin(user) || Boolean(user?.can_view_all_cases);
}
