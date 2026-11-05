import type { ReportData } from "@/lib/api";

/** What the edit form (ReportForm.tsx) may change: the Case Record facts
 * the report never prints. Everything else on a case is edited where it is
 * printed -- the Cover page tab (vehicle, dates, Our Ref, claim type,
 * workshop) and Damage detection / Annex A (damaged parts) -- so this form
 * must not send those keys: PUT /reports/{id} merges with exclude_unset,
 * and an emitted damage_summary: [] or vehicle_info: {} would wipe them. */
export type ReportEditPayload = Pick<
  ReportData,
  | "description"
  | "location"
  | "people_involved"
  | "witnesses"
  | "reported_to_authorities"
  | "authority_reference"
  | "police_report"
  | "insurance_details"
  | "third_party_info"
>;

const text = (formData: FormData, name: string): string | null => (formData.get(name) as string | null)?.trim() || null;

/** Builds the PUT /reports/{id} body from the edit form's FormData. Only
 * the keys above: lib/api.ts updateReport takes a Partial<ReportData> and the
 * API merges with exclude_unset. */
export function buildReportPayload(formData: FormData): { payload: ReportEditPayload; tempPhotoPaths: string[] } {
  const personNames = formData.getAll("person_name") as string[];
  const personRoles = formData.getAll("person_role") as string[];
  const personContacts = formData.getAll("person_contact") as string[];
  const people_involved = personNames
    .map((name, i) => ({
      name: name.trim(),
      // A blank role stays blank. "" rather than null: the API's
      // PersonInvolved.role is a required str.
      role: (personRoles[i] || "").trim(),
      contact: personContacts[i]?.trim() || null,
    }))
    .filter((p) => p.name);

  const witnessNames = formData.getAll("witness_name") as string[];
  const witnessContacts = formData.getAll("witness_contact") as string[];
  const witnessStatements = formData.getAll("witness_statement") as string[];
  const witnesses = witnessNames
    .map((name, i) => ({
      name: name.trim(),
      contact: witnessContacts[i]?.trim() || null,
      statement: witnessStatements[i]?.trim() || null,
    }))
    .filter((w) => w.name);

  const reportedToAuthorities = formData.get("reported_to_authorities") === "on";
  const reportNumber = text(formData, "report_number");

  const edit: ReportEditPayload = {
    description: (formData.get("description") as string | null)?.trim() || "",
    location: text(formData, "location"),
    people_involved,
    witnesses,
    reported_to_authorities: reportedToAuthorities,
    // Mirrored into both police_report.report_number (what the PDF template
    // and case page prefer) and the flat authority_reference (older readers
    // such as the Telegram template), so the two can't drift apart.
    authority_reference: reportNumber,
    police_report: {
      reported_to_police: reportedToAuthorities,
      police_station: text(formData, "police_station"),
      officer_name: text(formData, "officer_name"),
      report_number: reportNumber,
    },
    // Nested sections merge key by key on the server, so claim_type and
    // workshop_assigned (Cover page) are untouched by this form.
    insurance_details: {
      insurer_name: text(formData, "insurer_name"),
      claim_number: text(formData, "claim_number"),
    },
    third_party_info: {
      plate_number: text(formData, "tp_plate_number"),
      make_model: text(formData, "tp_make_model"),
      insurer_name: text(formData, "tp_insurer_name"),
    },
  };

  let tempPhotoPaths: string[] = [];
  const tempPhotoPathsRaw = formData.get("temp_photo_paths") as string | null;
  if (tempPhotoPathsRaw) {
    try {
      tempPhotoPaths = JSON.parse(tempPhotoPathsRaw);
    } catch {
      tempPhotoPaths = [];
    }
  }

  return { payload: edit, tempPhotoPaths };
}
