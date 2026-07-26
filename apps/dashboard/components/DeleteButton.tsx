"use client";

import { deleteReportAction } from "@/app/reports/actions";

export function DeleteButton({ id }: { id: string }) {
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
      <button type="submit" className="row-delete">
        Delete
      </button>
    </form>
  );
}
