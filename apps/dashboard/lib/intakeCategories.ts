/** Incident types offered on the New Intake review step.
 *
 * report.category still uses CATEGORY_OPTIONS from lib/api.ts, which the old
 * report form shares and which still carries non-motor categories from an
 * older product (Unauthorized Access, Harassment...). The wizard only offers
 * the motor ones, under plainer labels -- the stored values are unchanged, so
 * a case reads the same everywhere else. */
import type { CATEGORY_OPTIONS } from "./api";

type Category = (typeof CATEGORY_OPTIONS)[number];

export const MOTOR_INCIDENT_TYPES: readonly { value: Category; label: string }[] = [
  { value: "Vehicle Collision or Damage", label: "Collision or damage" },
  { value: "Theft or Burglary", label: "Vehicle theft or break-in" },
  { value: "Vandalism or Property Damage", label: "Vandalism / malicious damage" },
  // Motor claims that fit none of the above: flood, fire, falling objects.
  { value: "Other", label: "Other (flood, fire, …)" },
];

export type IncidentTypeOption = {
  value: string;
  label: string;
  /** False for a value the draft holds that isn't a motor type -- the AI
   * can still answer with one. Shown under its raw name, never hidden. */
  motor: boolean;
};

/** The motor types, then any other value in `held` (the draft's categories,
 * plus any seen earlier on the page) so nothing is silently dropped. */
export function incidentTypeOptions(held: readonly string[]): IncidentTypeOption[] {
  const motor: IncidentTypeOption[] = MOTOR_INCIDENT_TYPES.map((t) => ({ value: t.value, label: t.label, motor: true }));
  const known = new Set<string>(MOTOR_INCIDENT_TYPES.map((t) => t.value));
  const extra: IncidentTypeOption[] = [];
  for (const value of held) {
    if (!value || known.has(value)) continue;
    known.add(value);
    extra.push({ value, label: value, motor: false });
  }
  return [...motor, ...extra];
}
