/** The upload step's tip (components/intake/UploadStep.tsx): which
 * document photos to add. With INTAKE_USER_FIELDS on (GET /intake/{id}
 * user_fields; app/reports/user_fields.py) the odometer reading is the
 * user's to type in, never the AI's, so the tip still asks for the
 * odometer photo, for the record, without saying the AI reads it. Off: the
 * tip as before. */
export function uploadTip(userFields: boolean): string {
  return userFields
    ? "Tip: also photograph the log card and the VIN/chassis plate, so the AI can read them, and the odometer for the record (you type its reading in)."
    : "Tip: also photograph the log card, the VIN/chassis plate and the odometer, so the AI can read them.";
}
