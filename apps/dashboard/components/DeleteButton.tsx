"use client";

import { Trash2 } from "lucide-react";
import { deleteReportAction } from "@/app/reports/actions";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";

/** Only admins delete filed cases; an unfinished intake (status "intake")
 * may also be thrown away by its owner. The API enforces the same rule
 * (the list rows carry the owner but not the case's permissions). */
export function DeleteButton({ id, status, ownerId }: { id: string; status: string; ownerId: string | null }) {
  const user = useCurrentUser();
  const ownDraft = status === "intake" && ownerId !== null && ownerId === user?.id;
  if (!isAdmin(user) && !ownDraft) return null;

  return (
    <form
      action={deleteReportAction}
      onSubmit={(e) => {
        if (!confirm("Delete this report? This deletes its photos and PDF too, and can't be undone.")) {
          e.preventDefault();
        }
      }}
      style={{ display: "inline" }}
    >
      <input type="hidden" name="id" value={id} />
      <button
        type="submit"
        className="row-delete"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          cursor: "pointer",
        }}
      >
        <Trash2 style={{ width: 12, height: 12 }} /> Delete
      </button>
    </form>
  );
}
