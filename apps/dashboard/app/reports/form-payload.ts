import { CATEGORY_OPTIONS, type ReportData, type DamageSummaryItem } from "@/lib/api";

/** Builds a ReportData payload from the shared New/Edit report form's
 * FormData -- pulled out so both create and edit go through exactly the
 * same parsing logic instead of two copies that can silently drift apart. */
export function buildReportPayload(formData: FormData): { payload: ReportData; tempPhotoPaths: string[] } {
  const category = CATEGORY_OPTIONS.filter((c) => formData.get(`category_${c}`) === "on");

  const personNames = formData.getAll("person_name") as string[];
  const personRoles = formData.getAll("person_role") as string[];
  const personDepartments = formData.getAll("person_department") as string[];
  const personContacts = formData.getAll("person_contact") as string[];
  const people_involved = personNames
    .map((name, i) => ({
      name: name.trim(),
      role: (personRoles[i] || "Driver").trim(),
      department: personDepartments[i]?.trim() || null,
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

  const plateNumber = (formData.get("plate_number") as string)?.trim() || null;
  const make = (formData.get("make") as string)?.trim() || null;
  const model = (formData.get("model") as string)?.trim() || null;
  const vin = (formData.get("vin") as string)?.trim() || null;
  const driverName = (formData.get("driver_name") as string)?.trim() || null;
  const vehicle_details = [plateNumber, [make, model].filter(Boolean).join(" ") || null].filter(Boolean).join(" - ") || null;

  const policeStation = (formData.get("police_station") as string)?.trim() || null;
  const officerName = (formData.get("officer_name") as string)?.trim() || null;
  const reportNumber = (formData.get("report_number") as string)?.trim() || null;
  const reportedToAuthorities = formData.get("reported_to_authorities") === "on";

  const insurerName = (formData.get("insurer_name") as string)?.trim() || null;
  const claimNumber = (formData.get("claim_number") as string)?.trim() || null;
  const claimType = (formData.get("claim_type") as string)?.trim() || null;

  const repairRecommendation = (formData.get("repair_recommendation") as string)?.trim() || null;
  const inspectionRecommendation = (formData.get("inspection_recommendation") as string)?.trim() || null;

  const damagedPartsStr = (formData.get("damaged_parts") as string)?.trim() || "";
  const damaged_parts = damagedPartsStr
    ? damagedPartsStr.split(",").map((p) => p.trim()).filter(Boolean)
    : [];

  const severityInput = (formData.get("severity_level") as string)?.trim() || null;

  // ReportForm sends the reconciled per-part data (AI-derived damage_type/
  // photo_reference/ai_confidence where a part still matches what the AI
  // identified, a plain entry for anything hand-typed) via this hidden
  // field -- see buildFinalDamageSummary there. Falls back to a plain
  // rebuild only if that field is missing entirely, which shouldn't happen
  // from ReportForm itself but keeps this function safe to call from
  // anywhere else that only has damaged_parts + severity.
  const damageSummaryRaw = formData.get("damage_summary") as string | null;
  let damage_summary: DamageSummaryItem[] | null = null;
  if (damageSummaryRaw) {
    try {
      damage_summary = JSON.parse(damageSummaryRaw);
    } catch {
      damage_summary = null;
    }
  }
  if (!damage_summary) {
    damage_summary = damaged_parts.map((part) => ({
      part,
      severity: severityInput,
      human_verified: true,
    }));
  }

  const payload: ReportData = {
    reporter_name: (formData.get("reporter_name") as string)?.trim() || null,
    reporter_role: (formData.get("reporter_role") as string)?.trim() || null,
    reporter_contact: (formData.get("reporter_contact") as string)?.trim() || null,
    reporter_department: (formData.get("reporter_department") as string)?.trim() || null,
    incident_datetime: (formData.get("incident_datetime") as string)?.trim() || null,
    location: (formData.get("location") as string)?.trim() || null,
    weather_condition: (formData.get("weather_condition") as string)?.trim() || null,
    road_condition: (formData.get("road_condition") as string)?.trim() || null,
    traffic_condition: (formData.get("traffic_condition") as string)?.trim() || null,
    category: category.length ? category : [],
    accident_type: (formData.get("accident_type") as string)?.trim() || null,
    severity_level: severityInput,
    vehicle_details,
    vehicle_info: {
      plate_number: plateNumber,
      make,
      model,
      vin,
      driver_name: driverName,
    },
    damaged_parts,
    damage_summary,
    description: (formData.get("description") as string)?.trim() || "",
    people_involved,
    witnesses,
    immediate_actions: (formData.get("immediate_actions") as string)?.trim() || null,
    reported_to_authorities: reportedToAuthorities,
    // Mirrored into both police_report.report_number (what the PDF template
    // and detail page prefer) and the flat authority_reference (kept as a
    // fallback -- other surfaces like the Telegram/WhatsApp template read
    // authority_reference directly), so the two can't drift apart.
    authority_reference: reportNumber,
    police_report: {
      reported_to_police: reportedToAuthorities,
      police_station: policeStation,
      officer_name: officerName,
      report_number: reportNumber,
    },
    insurance_details: {
      insurer_name: insurerName,
      claim_number: claimNumber,
      claim_type: claimType,
    },
    recommendations: {
      repair_recommendation: repairRecommendation,
      inspection_recommendation: inspectionRecommendation,
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

  return { payload, tempPhotoPaths };
}
