"use server";

import { redirect } from "next/navigation";
import { createReport, CATEGORY_OPTIONS, type ReportData } from "@/lib/api";

export async function createReportAction(formData: FormData) {
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
  const makeModel = (formData.get("make_model") as string)?.trim() || null;
  const vehicle_details = [plateNumber, makeModel].filter(Boolean).join(" - ") || null;

  const damagedPartsStr = (formData.get("damaged_parts") as string)?.trim() || "";
  const damaged_parts = damagedPartsStr
    ? damagedPartsStr.split(",").map((p) => p.trim()).filter(Boolean)
    : [];

  const severityInput = (formData.get("severity_level") as string)?.trim() || null;

  // Only the part name and severity are actually asked on this form -- damage
  // type, photo reference, and repair necessity are left null rather than
  // guessed, since nobody has confirmed them here.
  const damage_summary = damaged_parts.map((part) => ({
    part,
    severity: severityInput,
    human_verified: true,
  }));

  const payload: ReportData = {
    reporter_name: (formData.get("reporter_name") as string)?.trim() || null,
    reporter_role: (formData.get("reporter_role") as string)?.trim() || null,
    incident_datetime: (formData.get("incident_datetime") as string)?.trim() || null,
    location: (formData.get("location") as string)?.trim() || null,
    category: category.length ? category : [],
    accident_type: (formData.get("accident_type") as string)?.trim() || null,
    severity_level: severityInput,
    vehicle_details,
    vehicle_info: {
      plate_number: plateNumber,
      make: makeModel,
    },
    damaged_parts,
    damage_summary,
    description: (formData.get("description") as string)?.trim() || "",
    people_involved,
    witnesses,
    immediate_actions: (formData.get("immediate_actions") as string)?.trim() || null,
    reported_to_authorities: formData.get("reported_to_authorities") === "on",
    authority_reference: (formData.get("authority_reference") as string)?.trim() || null,
  };

  const result = await createReport(payload);
  redirect(`/reports/${result.id}`);
}
