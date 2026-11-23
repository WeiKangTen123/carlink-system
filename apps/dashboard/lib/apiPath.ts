/**
 * One caller-supplied value (a case, user or key id) as one path segment
 * of an API URL.
 *
 * Server actions take their arguments from the browser, and the server
 * calls the API over the Docker network (API_INTERNAL_URL) with the
 * caller's sign-in. An id like "../catalogue-worker/claim" would otherwise
 * make that call land on another API route, one nginx keeps from the
 * outside. Encoded, "/", "?" and "#" stay inside the segment; "." and ".."
 * (which fetch resolves as "this folder" and "one up") and "" are refused.
 * Real ids (12 hex characters, small numbers) come out unchanged.
 */
export function seg(value: string | number): string {
  const text = String(value);
  if (text === "" || text === "." || text === "..") throw new Error("Invalid id in an API path");
  return encodeURIComponent(text);
}
