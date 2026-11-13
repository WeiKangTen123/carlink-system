/** The parts-catalogue look-up on Annex A (contract: plans/2026-11-12-
 * parts-catalogue/CONTRACT.md, "Dashboard endpoints" and "Phase 3"). A
 * person clicks [Look up in catalogue], or the API queues one by itself
 * once the AI reading of a Honda, BMW or MINI is saved; the catalogue
 * worker on the server (tools/parts_mcp) reads the makers' catalogues and
 * sends back part numbers, diagrams and reference prices, shown in
 * components/CatalogueSuggestionsPanel.tsx. Reference only: nothing here
 * fills W/shop Est. or prints, and a row reaches Annex A only when a person
 * ticks it and presses [Use N ticked] (then Save Annex A): see "Putting a
 * row on an Annex A line" at the end.
 *
 * Types for GET /reports/{id}/catalogue and the pure helpers the panel and
 * the button use: the words for prices, the worker, job states and
 * refusals, the rows' order and colour groups. Checked by scripts/
 * check-catalogue.mjs, which imports this file straight into Node -- so no
 * runtime imports here (type-only imports are fine). */

// ---------------------------------------------------------------------------
// Types (field names exactly as the contract)

export type CatalogueJobStatus = "pending" | "running" | "done" | "failed" | "cancelled";

/** Why a job, or one site within it, failed. */
export type CatalogueErrorCode =
  | "not_supported"
  | "no_vehicle"
  | "needs_human"
  | "challenge_blocked"
  | "site_blocked"
  | "daily_cap_reached"
  | "internal";

/** Why this person can't start a look-up on this case (can_request false). */
export type CatalogueBlockedReason = "make_not_covered" | "no_make_model" | "no_parts" | "draft" | "signed_off" | "readonly";

export type CatalogueSuggestionStatus = "suggested" | "accepted" | "rejected";

/** A car in a catalogue: the one used, or a candidate for "Change car". */
export type CatalogueVehicle = {
  key: string;
  source: string;
  brand: string | null;
  model: string | null;
  name: string | null;
  market: string | null;
  year_from: number | null;
  year_to: number | null;
  url: string | null;
  /** The home-market name this car is the twin of ("Vezel" for an HR-V). */
  twin_of: string | null;
};

/** One line of a catalogue diagram, as the catalogue worker read it. */
export type CatalogueRow = {
  callout: string | null;
  part_number: string | null;
  superseded_from: string | null;
  name: string | null;
  name_original: string | null;
  qty: string | null;
  side: string | null;
  notes: string | null;
  ref_amount: number | null;
  ref_currency: string | null;
  ref_tax_note: string | null;
  source: string;
  url: string | null;
  fetched_at: string | null;
  stale: boolean;
};

/** Which current Annex A line a row looks like: a head-word match the API
 * works out in code (no AI). kind null: no line like it. */
export type CatalogueMatchHint = {
  kind: "line" | null;
  line_index?: number | null;
  line_name?: string | null;
};

/** A row as stored for the case, with the surveyor's decision on it. */
export type CatalogueSuggestion = CatalogueRow & {
  id: number;
  status: CatalogueSuggestionStatus;
  decided_by: string | null;
  decided_at: string | null;
  match_hint?: CatalogueMatchHint | null;
  /** The name as a new Annex A line starts out ("FACE, L. RR. BUMPER CORNE"
   * -> "Rear bumper corner face LH"); `name` stays the catalogue's own. */
  tidy_name?: string | null;
  /** Rows of one diagram that are the same part in other colours share it:
   * folded into one row ("8 colour versions"), one of them to be picked. */
  variant_key?: string | null;
};

export type CatalogueDiagram = {
  diagram_key: string;
  diagram_name: string | null;
  diagram_url: string | null;
  image_url: string | null;
  match_score: number | null;
  /** The damaged part this diagram already shows under ("Same diagram as
   * Rear Bumper above"); its rows here are only the ones not listed there. */
  same_as_part?: string | null;
  rows: CatalogueSuggestion[];
};

export type CatalogueFailure = {
  source: string | null;
  code: CatalogueErrorCode | (string & {});
  message: string | null;
};

/** One damaged part of the case and what the catalogues showed for it. */
export type CataloguePart = {
  source_part: string;
  query: string;
  side: "left" | "right" | null;
  diagrams: CatalogueDiagram[];
  /** Not in the contract's GET shape (failures are top-level there); read
   * if the API sends them per part. */
  failures?: CatalogueFailure[];
};

export type CatalogueJob = {
  id: number;
  status: CatalogueJobStatus;
  created_at: string | null;
  claimed_at: string | null;
  finished_at: string | null;
  /** Who asked: a person's display name, or AUTO_REQUESTED_BY. */
  requested_by?: string | null;
  progress: { done_parts: number; total_parts: number; message: string | null } | null;
  error_code: CatalogueErrorCode | (string & {}) | null;
  error_text: string | null;
};

export type CatalogueWorker = { last_seen_at: string | null; online: boolean };

/** The make and model the look-up would use, and whether a person has
 * confirmed them (false: still the AI's reading). */
export type CatalogueVehicleHint = { brand: string | null; model: string | null; year: number | null; confirmed: boolean };

export type CatalogueInfo = {
  enabled: true;
  worker: CatalogueWorker;
  can_request: boolean;
  blocked_reason: CatalogueBlockedReason | (string & {}) | null;
  vehicle_hint: CatalogueVehicleHint | null;
  /** The case's latest job, whatever its state. */
  job: CatalogueJob | null;
  /** The catalogue car of the latest done job. */
  vehicle: CatalogueVehicle | null;
  candidates: CatalogueVehicle[];
  parts: CataloguePart[];
  /** Damaged parts no catalogue had. */
  not_found: string[];
  failures: CatalogueFailure[];
  /** The makes the catalogues cover (the API's CATALOGUE_MAKES). */
  covered_makes: string[];
};

/** Off (the API's default, or an API from before the look-up): the
 * dashboard shows nothing of it. */
export type CatalogueState = CatalogueInfo | { enabled: false };

export const CATALOGUE_OFF: CatalogueState = { enabled: false };

/** GET every 5 s, only while a job is pending or running. */
export const CATALOGUE_POLL_MS = 5000;

/** Rows shown per diagram before "+ N more rows". */
export const ROWS_SHOWN = 6;

/** The API calls the worker online when it was seen in the last 2 min;
 * the panel keeps saying so a little longer, as its copy ages. */
export const WORKER_FRESH_MS = 3 * 60_000;

/** requested_by of a look-up the API queued by itself once the AI reading
 * of the case was saved (app/parts/catalogue.AUTO_REQUESTED_BY). */
export const AUTO_REQUESTED_BY = "auto (after detection)";

/** The makes the catalogues cover when the API doesn't say. */
export const DEFAULT_COVERED_MAKES = ["Honda", "BMW", "MINI"];

// ---------------------------------------------------------------------------
// Reading the API's answer

const isObj = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** GET /reports/{id}/catalogue as the panel uses it: off unless the API
 * says enabled: true, and every list present (an empty list where the API
 * left one out), so the panel never trips over a missing field. */
export function normalizeCatalogue(body: unknown): CatalogueState {
  if (!isObj(body) || body.enabled !== true) return CATALOGUE_OFF;
  const worker = isObj(body.worker) ? body.worker : {};
  const hint = isObj(body.vehicle_hint) ? body.vehicle_hint : null;
  return {
    enabled: true,
    worker: { last_seen_at: str(worker.last_seen_at), online: worker.online === true },
    can_request: body.can_request === true,
    blocked_reason: str(body.blocked_reason),
    vehicle_hint: hint
      ? {
          brand: str(hint.brand),
          model: str(hint.model),
          year: typeof hint.year === "number" ? hint.year : null,
          confirmed: hint.confirmed === true,
        }
      : null,
    job: isObj(body.job) ? (body.job as unknown as CatalogueJob) : null,
    vehicle: isObj(body.vehicle) ? (body.vehicle as unknown as CatalogueVehicle) : null,
    candidates: arr<unknown>(body.candidates).filter(isObj) as unknown as CatalogueVehicle[],
    parts: arr<unknown>(body.parts)
      .filter(isObj)
      .map((p) => ({
        ...(p as unknown as CataloguePart),
        diagrams: arr<unknown>(p.diagrams)
          .filter(isObj)
          .map((d) => ({
            ...(d as unknown as CatalogueDiagram),
            same_as_part: str(d.same_as_part),
            rows: arr<unknown>(d.rows).filter(isObj) as unknown as CatalogueSuggestion[],
          })),
        failures: arr<unknown>(p.failures).filter(isObj) as unknown as CatalogueFailure[],
      })),
    not_found: arr<unknown>(body.not_found).filter((s): s is string => typeof s === "string" && Boolean(s)),
    failures: arr<unknown>(body.failures).filter(isObj) as unknown as CatalogueFailure[],
    covered_makes: Array.isArray(body.covered_makes)
      ? body.covered_makes.filter((s): s is string => typeof s === "string" && Boolean(s.trim()))
      : [...DEFAULT_COVERED_MAKES],
  };
}

/** The state with one suggestion replaced by the API's answer to Hide or
 * Undo (matched by id). */
export function withSuggestion(state: CatalogueInfo, s: CatalogueSuggestion): CatalogueInfo {
  return {
    ...state,
    parts: state.parts.map((p) => ({
      ...p,
      diagrams: p.diagrams.map((d) => (d.rows.some((r) => r.id === s.id) ? { ...d, rows: d.rows.map((r) => (r.id === s.id ? { ...r, ...s } : r)) } : d)),
    })),
  };
}

// ---------------------------------------------------------------------------
// Times, always in Singapore time whatever the browser's zone

const SGT_PARTS = new Intl.DateTimeFormat("en-SG", {
  timeZone: "Asia/Singapore",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  // h23, not hour12:false: Node and browsers disagree on midnight under
  // hour12:false ("24:05" vs "00:05"); see lib/caseFields.ts.
  hourCycle: "h23",
});

function sgt(ms: number) {
  const parts = SGT_PARTS.formatToParts(new Date(ms));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return { day: get("day"), month: get("month"), year: get("year"), time: `${get("hour")}:${get("minute")}` };
}

const DAY_MS = 24 * 60 * 60_000;

function msOf(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

const sameSgtDay = (a: ReturnType<typeof sgt>, b: ReturnType<typeof sgt>) => a.day === b.day && a.month === b.month && a.year === b.year;

/** "08:12 today", "08:12 yesterday", "11 Nov 08:12", "11 Nov 2025 08:12". */
export function dayTime(iso: string | null | undefined, now: number): string {
  const t = msOf(iso);
  if (t == null) return iso || "";
  const at = sgt(t);
  if (sameSgtDay(at, sgt(now))) return `${at.time} today`;
  if (sameSgtDay(at, sgt(now - DAY_MS))) return `${at.time} yesterday`;
  return at.year === sgt(now).year ? `${at.day} ${at.month} ${at.time}` : `${at.day} ${at.month} ${at.year} ${at.time}`;
}

/** "12 Nov 10:42" ("12 Nov 2025 10:42" in another year). */
export function stamp(iso: string | null | undefined, now: number): string {
  const t = msOf(iso);
  if (t == null) return iso || "";
  const at = sgt(t);
  return at.year === sgt(now).year ? `${at.day} ${at.month} ${at.time}` : `${at.day} ${at.month} ${at.year} ${at.time}`;
}

/** "just now", "1 min ago", "40 min ago", "3 h ago", then dayTime(). */
export function ago(iso: string | null | undefined, now: number): string {
  const t = msOf(iso);
  if (t == null) return iso || "";
  const ms = Math.max(0, now - t);
  if (ms < 60_000) return "just now";
  if (ms < 60 * 60_000) return `${Math.floor(ms / 60_000)} min ago`;
  if (ms < DAY_MS) return `${Math.floor(ms / (60 * 60_000))} h ago`;
  return dayTime(iso, now);
}

// ---------------------------------------------------------------------------
// The catalogue worker (a container on the server since 12 Nov 2026)

/** Seen recently enough to call it running. */
export function workerOnline(worker: CatalogueWorker | null | undefined, now: number): boolean {
  const seen = msOf(worker?.last_seen_at);
  return Boolean(worker?.online) && seen != null && now - seen < WORKER_FRESH_MS;
}

/** "not running (last seen 08:12 today)", "not running (not seen yet)". */
function notRunning(worker: CatalogueWorker | null | undefined, now: number): string {
  const seen = worker?.last_seen_at && msOf(worker.last_seen_at) != null ? `last seen ${dayTime(worker.last_seen_at, now)}` : "not seen yet";
  return `not running (${seen})`;
}

/** "Catalogue look-up: ready", "Catalogue look-up: not running (last seen
 * 08:12 today)", "Catalogue look-up: not running (not seen yet)". */
export function workerLabel(worker: CatalogueWorker | null | undefined, now: number): string {
  return `Catalogue look-up: ${workerOnline(worker, now) ? "ready" : notRunning(worker, now)}`;
}

/** "seen 1 min ago": the label's tooltip while it is ready. */
export function workerSeen(worker: CatalogueWorker | null | undefined, now: number): string | null {
  return worker?.last_seen_at && msOf(worker.last_seen_at) != null ? `seen ${ago(worker.last_seen_at, now)}` : null;
}

// ---------------------------------------------------------------------------
// Sites, cars, prices

/** What each source key is called when no URL says which site it was.
 * "microfiches" is the one engine behind bmw-spare-parts.com,
 * mini-spare-parts.com and parts-honda.uk, so its URL names the site. */
const SOURCE_NAMES: Record<string, string> = {
  microfiches: "BMW/MINI/Honda catalogues",
};

/** The link if it is https, else null (never a javascript: or http: href). */
export function safeUrl(url: string | null | undefined): string | null {
  return typeof url === "string" && /^https:\/\/[^\s]+$/i.test(url) ? url : null;
}

/** "parts-honda.uk", "bmw-spare-parts.com": the site a row or car came from, by
 * its URL (else `fallbackUrl`, e.g. its diagram's); else the source's name. */
export function siteLabel(source: string | null | undefined, url?: string | null, fallbackUrl?: string | null): string {
  for (const candidate of [url, fallbackUrl]) {
    const href = safeUrl(candidate);
    if (!href) continue;
    try {
      return new URL(href).hostname.replace(/^www\./, "");
    } catch {
      // try the next, then the source's name
    }
  }
  return (source && SOURCE_NAMES[source]) || source || "";
}

/** "ex VAT", "inc VAT", or the note as written. */
function taxShort(note: string | null | undefined): string {
  const n = (note || "").trim();
  if (!n) return "";
  if (/\bex(cl|cluding|\.)?\b|without/i.test(n)) return "ex VAT";
  if (/\binc(l|luding|luded|\.)?\b|with vat/i.test(n)) return "inc VAT";
  return n;
}

/** "ref EUR 309.41 ex VAT", "ref SGD 1,250.00", "no price shown". The
 * site's own price, a reference only: never the workshop's estimate. */
export function priceLabel(row: Pick<CatalogueRow, "ref_amount" | "ref_currency" | "ref_tax_note">): string {
  const amount = row.ref_amount;
  if (amount == null || typeof amount !== "number" || !Number.isFinite(amount)) return "no price shown";
  const figure = amount.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return ["ref", (row.ref_currency || "").trim().toUpperCase(), figure, taxShort(row.ref_tax_note)].filter(Boolean).join(" ");
}

/** The price's tooltip: where and when it was read. `site` as the row
 * shows it (siteLabel). */
export function priceTitle(row: CatalogueRow, now: number, site = siteLabel(row.source, row.url)): string {
  const parts = [`Reference price on ${site || "the parts site"}${row.ref_tax_note ? `, ${row.ref_tax_note}` : ""}.`];
  if (row.fetched_at) parts.push(`Read ${stamp(row.fetched_at, now)}${row.stale ? ", may be out of date" : ""}.`);
  parts.push("Not the workshop's price: it never fills W/shop Est.");
  return parts.join(" ");
}

/** "2015–2019", "2015 on", "up to 2019", "2016". */
export function yearsLabel(v: Pick<CatalogueVehicle, "year_from" | "year_to">): string {
  const from = v.year_from ?? null;
  const to = v.year_to ?? null;
  if (from && to) return from === to ? String(from) : `${from}–${to}`;
  if (from) return `${from} on`;
  if (to) return `up to ${to}`;
  return "";
}

/** "Honda HR-V 1.5 i-VTEC": make, then the catalogue's name for the car
 * (its model in front when the name leaves it out). */
export function carLabel(v: Pick<CatalogueVehicle, "brand" | "model" | "name">): string {
  const brand = (v.brand || "").trim();
  const model = (v.model || "").trim();
  const name = (v.name || "").trim();
  let car = name && model && !name.toLowerCase().startsWith(model.toLowerCase()) ? `${model} ${name}` : name || model;
  if (brand && !car.toLowerCase().startsWith(brand.toLowerCase())) car = `${brand} ${car}`;
  return car.trim() || "Unnamed car";
}

/** What follows the car's name: ["2015–2019", "Europe", "parts-honda.uk",
 * "looked up 12 Nov 10:42"], each only when known. */
export function carDetails(v: CatalogueVehicle, lookedUpAt: string | null, now: number): string[] {
  return [yearsLabel(v), (v.market || "").trim(), siteLabel(v.source, v.url), lookedUpAt ? `looked up ${stamp(lookedUpAt, now)}` : ""].filter(Boolean);
}

/** "Honda HR-V 1.5 i-VTEC · 2015–2019 · Europe · parts-honda.uk", plus
 * "looked up 12 Nov 10:42" when given the time. */
export function carLine(v: CatalogueVehicle, lookedUpAt: string | null, now: number): string {
  return [carLabel(v), ...carDetails(v, lookedUpAt, now)].join(" · ");
}

/** "Market twin of Vezel: part numbers may differ.", or null. */
export function twinWarning(v: Pick<CatalogueVehicle, "twin_of"> | null | undefined): string | null {
  const twin = (v?.twin_of || "").trim();
  return twin ? `Market twin of ${twin}: part numbers may differ.` : null;
}

/** "Honda Vezel Hybrid 2017": the make and model the look-up goes by. */
export function hintReading(hint: CatalogueVehicleHint | null | undefined): string {
  if (!hint) return "";
  return [hint.brand, hint.model, hint.year ? String(hint.year) : null].filter(Boolean).join(" ");
}

/** "100% match", or null. */
export function scoreLabel(score: number | null | undefined): string | null {
  return typeof score === "number" && Number.isFinite(score) ? `${Math.round(score * 100)}% match` : null;
}

// Same as sameLineKey in lib/pastReports.ts (not imported: see the top).
const lineKey = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** '~ like line 2 "Rear bumper fascia"' or "not in Annex A"; null when the
 * API sent no hint. `tableNames` are the Annex A lines as the table shows
 * them (List Items, then S/Nett Items), so the number is the one on screen;
 * the line's own name is matched, not the API's index. */
export function matchHintText(hint: CatalogueMatchHint | null | undefined, tableNames: string[]): string | null {
  if (!hint) return null;
  if (hint.kind !== "line") return "not in Annex A";
  const name = (hint.line_name || "").trim();
  if (!name) return "~ like a line in Annex A";
  const at = tableNames.findIndex((n) => lineKey(n) === lineKey(name));
  return at >= 0 ? `~ like line ${at + 1} "${name}"` : `~ like "${name}"`;
}

// ---------------------------------------------------------------------------
// Jobs, refusals and failures in plain words

export function isJobOpen(job: CatalogueJob | null | undefined): boolean {
  return job?.status === "pending" || job?.status === "running";
}

/** "Started automatically after detection" for a look-up the API queued by
 * itself (requested_by AUTO_REQUESTED_BY), else null. */
export function autoStartedText(job: CatalogueJob | null | undefined): string | null {
  return job?.requested_by === AUTO_REQUESTED_BY ? "Started automatically after detection" : null;
}

/** Poll only while the case's job is waiting or running. */
export function shouldPoll(state: CatalogueState | null | undefined): boolean {
  return Boolean(state?.enabled) && isJobOpen((state as CatalogueInfo).job);
}

/** The three ways a parts site stops answering a robot: one sentence. */
const SITE_CHECK_CODES = ["needs_human", "site_blocked", "challenge_blocked"];
const SITE_CHECK_TEXT = "The parts site asked for a human check; try again later.";

/** Why a job (or one site in it) failed, in plain words. */
export function errorCodeText(code: string | null | undefined): string {
  if (code && SITE_CHECK_CODES.includes(code)) return SITE_CHECK_TEXT;
  switch (code) {
    case "daily_cap_reached":
      return "Today's catalogue look-ups are used up; it carries on tomorrow.";
    case "not_supported":
      return "The parts catalogues don't cover this make.";
    case "no_vehicle":
      return "No car in the catalogues matches this make, model and year. Check them on the Cover page, or pick a car with Change car.";
    case "internal":
      return "Something went wrong in the catalogue look-up. Try again; if it keeps failing, tell an admin.";
    default:
      return "The look-up failed. Try again.";
  }
}

/** One site's failure: errorCodeText, with the site in front (a site check
 * reads the same whichever site asked). */
export function failureText(f: CatalogueFailure): string {
  const text = errorCodeText(f.code);
  if (SITE_CHECK_CODES.includes(f.code) || !f.source) return text;
  return `${siteLabel(f.source)}: ${text}`;
}

/** Every failure of the latest result (top-level and per part), each said
 * once, and not the one the job's own status line already says. */
export function failureLines(state: CatalogueInfo): string[] {
  const all = [...state.failures, ...state.parts.flatMap((p) => p.failures ?? [])];
  const said = new Set<string>();
  if (state.job?.status === "failed") said.add(errorCodeText(state.job.error_code));
  const out: string[] = [];
  for (const f of all) {
    const text = failureText(f);
    if (said.has(text)) continue;
    said.add(text);
    out.push(text);
  }
  return out;
}

/** "Honda, BMW and MINI". */
export function makesList(makes: string[]): string {
  const list = makes.map((m) => m.trim()).filter(Boolean);
  return list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}` : list.join("");
}

/** "No parts catalogue for Toyota yet (Honda, BMW and MINI are covered)."
 * -- the API's words for a 400 make_not_covered too. */
export function notCoveredText(make: string | null | undefined, covered: string[] = DEFAULT_COVERED_MAKES): string {
  const listed = makesList(covered);
  const which = listed ? ` (${listed} ${covered.filter((m) => m.trim()).length === 1 ? "is" : "are"} covered)` : "";
  return `No parts catalogue for ${(make || "").trim().replace(/\s+/g, " ") || "this make"} yet${which}.`;
}

/** What the reasons need beyond their code: the case's make and the makes
 * covered (make_not_covered). */
export type BlockedContext = { make?: string | null; covered?: string[] | null };

/** The context for blockedReasonText from the panel's state. */
export function blockedContext(state: CatalogueInfo): BlockedContext {
  return { make: state.vehicle_hint?.brand ?? null, covered: state.covered_makes };
}

/** What blocks [Look up in catalogue] for this person on this case. */
export function blockedReasonText(reason: string | null | undefined, ctx: BlockedContext = {}): string {
  switch (reason) {
    case "make_not_covered":
      return notCoveredText(ctx.make, ctx.covered?.length ? ctx.covered : DEFAULT_COVERED_MAKES);
    case "no_make_model":
      return "Add the make and model on the Cover page first.";
    case "no_parts":
      return "No damaged parts are recorded on tab 1, so there is nothing to look up.";
    case "draft":
      return "Finish filing the case first.";
    case "signed_off":
      return "The case is signed off.";
    case "readonly":
      return "You can look at this case but not change it.";
    default:
      return "The look-up can't be started on this case.";
  }
}

/** 429 lookup_limit when the answer came without the API's own words
 * (those say how many and for how long: 3 in 24 hours for an account
 * without staff access, 20 in an hour for everyone else). */
export const LOOKUP_LIMIT_TEXT = "You've started the most catalogue look-ups allowed for now. Try again later, or ask an admin.";

/** A refused POST lookup / cancel / Hide, by its code or status; null to
 * use the API's own words (make_not_covered with no make known: the API's
 * sentence is the same one). `said`: the API's own words, if any. */
export function lookupRefusalText(
  code: string | null | undefined,
  status?: number | null,
  ctx: BlockedContext = {},
  said?: string | null,
): string | null {
  if (code === "lookup_limit") return (said || "").trim() || LOOKUP_LIMIT_TEXT;
  if (code === "job_open") return "A look-up is already waiting or running for this case.";
  if (code === "make_not_covered") return ctx.make ? blockedReasonText(code, ctx) : null;
  if (code === "no_make_model") return blockedReasonText("no_make_model");
  if (code === "no_parts") return blockedReasonText("no_parts");
  if (status === 404) return "The catalogue look-up is turned off.";
  return null;
}

export type JobStatusView = { tone: "wait" | "busy" | "fail" | "info"; text: string; detail: string | null };

/** The job's line at the top of the panel; null for none or a done job
 * (the results say when they were looked up). */
export function jobStatusView(job: CatalogueJob | null | undefined, worker: CatalogueWorker | null | undefined, now: number): JobStatusView | null {
  if (!job) return null;
  switch (job.status) {
    case "pending": {
      // "Waiting to start: queued 10:40. The catalogue look-up is not
      // running (last seen 08:12 today)."
      const online = workerOnline(worker, now);
      const queued = job.created_at ? `: queued ${dayTime(job.created_at, now).replace(/ today$/, "")}` : "";
      return {
        tone: "wait",
        text: `Waiting to start${queued}.${online ? "" : ` The catalogue look-up is ${notRunning(worker, now)}.`}`,
        detail: online ? null : "It starts by itself when the look-up runs again; tell an admin if it stays like this.",
      };
    }
    case "running": {
      const total = job.progress?.total_parts ?? 0;
      const done = job.progress?.done_parts ?? 0;
      const text = total > 0 ? `Looking up ${Math.min(done + 1, total)} of ${total} part${total === 1 ? "" : "s"}…` : "Starting the look-up…";
      return { tone: "busy", text, detail: job.progress?.message || null };
    }
    case "failed":
      return { tone: "fail", text: errorCodeText(job.error_code), detail: null };
    case "cancelled":
      return { tone: "info", text: `Look-up cancelled${job.finished_at ? ` ${dayTime(job.finished_at, now)}` : ""}.`, detail: null };
    default:
      return null;
  }
}

/** When the results shown were looked up: the done job's finish, else the
 * newest row's read time. */
export function resultsTime(state: CatalogueInfo): string | null {
  if (state.job?.status === "done" && state.job.finished_at) return state.job.finished_at;
  let newest: string | null = null;
  let newestMs = -Infinity;
  for (const p of state.parts)
    for (const d of p.diagrams)
      for (const r of d.rows) {
        const t = msOf(r.fetched_at);
        if (t != null && t > newestMs) {
          newestMs = t;
          newest = r.fetched_at;
        }
      }
  return newest;
}

// ---------------------------------------------------------------------------
// Rows: order, hidden ones, how many to show

/** Rows in diagram order: by callout number (1, 2, 10, then "10a"), rows
 * with no callout last, otherwise as the catalogue listed them. */
export function sortRows<T extends Pick<CatalogueRow, "callout">>(rows: T[]): T[] {
  const order = (c: string | null) => {
    const text = (c ?? "").trim();
    const m = text.match(/^(\d+)/);
    return { n: m ? parseInt(m[1], 10) : null, text };
  };
  return rows
    .map((row, i) => ({ row, i, o: order(row.callout) }))
    .sort((a, b) => {
      if (a.o.n != null && b.o.n != null && a.o.n !== b.o.n) return a.o.n - b.o.n;
      if (a.o.n == null && b.o.n != null) return 1;
      if (a.o.n != null && b.o.n == null) return -1;
      if (a.o.text !== b.o.text) return a.o.text < b.o.text ? -1 : 1;
      return a.i - b.i;
    })
    .map((x) => x.row);
}

/** A diagram's rows: shown (suggested or accepted) and hidden (rejected),
 * each in diagram order. */
export function splitRows<T extends Pick<CatalogueSuggestion, "callout" | "status">>(rows: T[]): { shown: T[]; hidden: T[] } {
  const sorted = sortRows(rows);
  return { shown: sorted.filter((r) => r.status !== "rejected"), hidden: sorted.filter((r) => r.status === "rejected") };
}

/** The first ROWS_SHOWN rows, or all once expanded; `more` is how many the
 * "+ N more rows" button would add. */
export function pageRows<T>(shown: T[], expanded: boolean, limit = ROWS_SHOWN): { rows: T[]; more: number } {
  if (expanded || shown.length <= limit) return { rows: shown, more: 0 };
  return { rows: shown.slice(0, limit), more: shown.length - limit };
}

/** A diagram's shown rows as the panel lists them: a row, or a colour
 * group -- two or more rows sharing a variant_key, folded into one ("8
 * colour versions") at its first row's place. */
export type RowUnit<T> = { kind: "row"; row: T } | { kind: "variants"; key: string; rows: T[] };

/** `rows` (already in order) with each colour group folded (RowUnit). A
 * group down to one row (the others hidden) is a plain row again. */
export function foldVariants<T extends Pick<CatalogueSuggestion, "variant_key">>(rows: T[]): RowUnit<T>[] {
  const count = new Map<string, number>();
  for (const r of rows) if (r.variant_key) count.set(r.variant_key, (count.get(r.variant_key) ?? 0) + 1);
  const groups = new Map<string, { kind: "variants"; key: string; rows: T[] }>();
  const out: RowUnit<T>[] = [];
  for (const row of rows) {
    const key = row.variant_key;
    if (!key || (count.get(key) ?? 0) < 2) {
      out.push({ kind: "row", row });
      continue;
    }
    const group = groups.get(key);
    if (group) group.rows.push(row);
    else {
      const unit = { kind: "variants" as const, key, rows: [row] };
      groups.set(key, unit);
      out.push(unit);
    }
  }
  return out;
}

/** "8 colour versions". */
export function variantCountLabel(n: number): string {
  return `${n} colour version${n === 1 ? "" : "s"}`;
}

/** What a colour group's part numbers share, then "…": "04718T7JH00Z…". */
export function variantNumberLabel(rows: Pick<CatalogueRow, "part_number">[]): string {
  const numbers = rows.map((r) => (r.part_number || "").trim()).filter(Boolean);
  if (!numbers.length) return "no part no.";
  let prefix = numbers[0];
  for (const n of numbers) while (prefix && !n.startsWith(prefix)) prefix = prefix.slice(0, -1);
  return prefix.length < Math.max(...numbers.map((n) => n.length)) ? `${prefix}…` : prefix;
}

/** The other rows of `row`'s colour group on its diagram: one colour per
 * group goes on a line, so ticking one unticks these. */
export function variantSiblings<T extends Pick<CatalogueSuggestion, "id" | "variant_key">>(rows: T[], row: T): T[] {
  if (!row.variant_key) return [];
  return rows.filter((r) => r.id !== row.id && r.variant_key === row.variant_key);
}

/** The ticks after ticking `id` (onto `target`): its colour group's other
 * ticks taken off. */
export function tickOnly(picks: Record<number, string>, id: number, target: string, siblingIds: number[]): Record<number, string> {
  const next = { ...picks };
  for (const s of siblingIds) delete next[s];
  next[id] = target;
  return next;
}

/** "Same diagram as Rear Bumper above", for a diagram the API marked
 * same_as_part; with rows of its own left, that only those are listed. */
export function sameDiagramText(part: string, rowsLeft: number): string {
  return rowsLeft > 0 ? `Same diagram as ${part} above; only the rows not listed there are shown.` : `Same diagram as ${part} above`;
}

/** How many rows the surveyor hid, on the whole case. */
export function hiddenCount(state: CatalogueInfo): number {
  return state.parts.reduce((n, p) => n + p.diagrams.reduce((m, d) => m + d.rows.filter((r) => r.status === "rejected").length, 0), 0);
}

/** The parts with something to show (a diagram, or a failure of their
 * own); the rest are in not_found. */
export function partsShown(state: CatalogueInfo): CataloguePart[] {
  return state.parts.filter((p) => p.diagrams.length > 0 || (p.failures?.length ?? 0) > 0);
}

/** Anything looked up, or being looked up, on this case. */
export function hasCatalogueContent(state: CatalogueInfo): boolean {
  return Boolean(state.job || state.vehicle || state.parts.length || state.not_found.length || state.candidates.length || state.failures.length);
}

// ---------------------------------------------------------------------------
// The buttons

/** Hide / Undo / Cancel / Change car need the right to change the case;
 * no make and model, or one not covered, only stops a new look-up. */
export function canDecide(state: CatalogueState | null | undefined): boolean {
  if (!state?.enabled) return false;
  return !["readonly", "signed_off", "draft"].includes(state.blocked_reason ?? "");
}

export type CatalogueBusy = "lookup" | "cancel" | null;

/** [Look up in catalogue] beside Annex A's other buttons; null hides it
 * (the feature is off, or not known yet). */
export function lookupButton(
  state: CatalogueState | null | undefined,
  opts: { locked: boolean; busy: CatalogueBusy }
): { label: string; disabled: boolean; title: string; showCancel: boolean } | null {
  if (!state?.enabled) return null;
  const open = isJobOpen(state.job);
  return {
    label: open ? "Looking up…" : opts.busy === "lookup" ? "Starting…" : "Look up in catalogue",
    disabled: opts.locked || !state.can_request || open || opts.busy !== null,
    title: !state.can_request
      ? blockedReasonText(state.blocked_reason, blockedContext(state))
      : open
        ? "A look-up is waiting or running; the catalogue panel below shows how far it is."
        : `Looks up the damaged parts in the makers' parts catalogues (${makesList(state.covered_makes)}). Reference only: nothing goes into Annex A or the report.`,
    showCancel: open && canDecide(state),
  };
}

// ---------------------------------------------------------------------------
// Putting a row on an Annex A line (Phase 2: accept / attach)
//
// The surveyor ticks catalogue rows and picks, for each, an Annex A line
// ("line 2 Rear bumper") or "New line"; [Use N ticked] puts them into the
// Annex A DRAFT, like the Suggested lines panel. Nothing is saved until
// Save Annex A, which sends each line's catalogue_ref: the API checks the
// row is the case's own, writes its facts from the stored row and marks it
// accepted (and back to suggested once no line carries it). A reference
// only: attaching never touches the line's name, qty or W/shop Est., a new
// line starts unpriced, and nothing of it prints.

/** What an Annex A line keeps of the catalogue row put on it (the API's
 * schema.CatalogueRef). Not printed. */
export type CatalogueRef = {
  suggestion_id: number;
  source: string | null;
  part_number: string | null;
  name: string | null;
  diagram_name: string | null;
  diagram_url: string | null;
  ref_amount: number | null;
  ref_currency: string | null;
  ref_tax_note: string | null;
  fetched_at: string | null;
};

/** The fields of an Annex A line that attaching reads or sets (structural:
 * lib/api.ts's AnnexAPartItem has them; no runtime import here). */
export type AttachableLine = {
  part_name: string;
  oem_part_number?: string | null;
  catalogue_ref?: CatalogueRef | null;
};

/** The ref a line keeps for catalogue row `row` on `diagram`. */
export function catalogueRefOf(row: CatalogueSuggestion, diagram: Pick<CatalogueDiagram, "diagram_name" | "diagram_url">): CatalogueRef {
  return {
    suggestion_id: row.id,
    source: row.source ?? null,
    part_number: row.part_number ?? null,
    name: row.name ?? null,
    diagram_name: diagram.diagram_name ?? null,
    diagram_url: diagram.diagram_url ?? null,
    ref_amount: row.ref_amount ?? null,
    ref_currency: row.ref_currency ?? null,
    ref_tax_note: row.ref_tax_note ?? null,
    fetched_at: row.fetched_at ?? null,
  };
}

const trimmed = (s: string | null | undefined) => (s || "").trim();

/** The line's part number is none, or the one its catalogue row brought:
 * attaching may fill it, unlinking may take it off (the API's
 * parts._catalogue_number reads it the same way). */
function numberIsTheCatalogues(line: AttachableLine): boolean {
  const own = trimmed(line.oem_part_number);
  return !own || own === trimmed(line.catalogue_ref?.part_number);
}

/** `line` with `ref` on it: catalogue_ref set, and the part number when the
 * line had none (or only the previous catalogue row's). Name, qty and
 * W/shop Est. are left as they are. */
export function attachToLine<T extends AttachableLine>(line: T, ref: CatalogueRef): T {
  const number = numberIsTheCatalogues(line) ? trimmed(ref.part_number) || null : (line.oem_part_number ?? null);
  return { ...line, catalogue_ref: ref, oem_part_number: number };
}

/** Unlink: catalogue_ref null (sent as null, so the save takes it off) and
 * the part number it brought taken off too; a number typed some other way
 * stays. */
export function detachFromLine<T extends AttachableLine>(line: T): T {
  const refNumber = trimmed(line.catalogue_ref?.part_number);
  const fromRef = Boolean(refNumber) && trimmed(line.oem_part_number) === refNumber;
  return { ...line, catalogue_ref: null, oem_part_number: fromRef ? null : (line.oem_part_number ?? null) };
}

/** The catalogue's qty when it is a plain number ("2", "2.0"), else 1
 * ("AR", "as required", none). */
export function catalogueQty(qty: string | number | null | undefined): number {
  const text = String(qty ?? "").trim();
  if (!/^\d+(\.\d+)?$/.test(text)) return 1;
  const n = parseFloat(text);
  return n > 0 ? n : 1;
}

/** A new Annex A line from a catalogue row, shaped like lib/api.ts's
 * AnnexAPartItem: named by the API's tidy_name ("Rear bumper face" for
 * "FACE, RR. BUMPER"; the table still tells the surveyor to check the
 * wording), else the catalogue's own name; qty from the catalogue when it
 * is a number, no estimate (W/shop Est. is the workshop's figure, never the
 * site's price), origin "lookup", for the damaged part it was found for. */
export function catalogueLine(
  row: CatalogueSuggestion,
  part: Pick<CataloguePart, "source_part">,
  diagram: Pick<CatalogueDiagram, "diagram_name" | "diagram_url">
) {
  const ref = catalogueRefOf(row, diagram);
  const site = siteLabel(row.source, row.url, diagram.diagram_url);
  return {
    item_no: "",
    part_name: trimmed(row.tidy_name) || trimmed(row.name) || trimmed(row.part_number) || trimmed(diagram.diagram_name) || "Catalogue part",
    condition: "",
    action: "",
    qty: catalogueQty(row.qty),
    qty_text: null,
    workshop_est: 0,
    discount_pct: 0.2,
    adjusted_cost: 0,
    is_net_item: false,
    oem_part_number: trimmed(row.part_number) || null,
    suggested_from: site ? `catalogue (${site})` : "catalogue",
    usual_unit_price: null,
    prefilled: false,
    origin: "lookup" as const,
    source_part: part.source_part || null,
    stale: false,
    catalogue_ref: ref,
  };
}

/** The line's name is still the catalogue's wording -- its own ("FACE, RR.
 * BUMPER") or the API's tidy_name of it ("Rear bumper face", which the ref
 * doesn't keep: the caller finds it by the ref's suggestion_id) -- so the
 * table says "check wording" beside it until someone writes it in the
 * firm's words. */
export function needsWording(line: AttachableLine, tidyName?: string | null): boolean {
  const own = trimmed(line.catalogue_ref?.name);
  const name = trimmed(line.part_name);
  return Boolean(own) && (name === own || (Boolean(trimmed(tidyName)) && name === trimmed(tidyName)));
}

/** "# 04715T7WA90 · parts-honda.uk": under the line's name. */
export function refLine(ref: CatalogueRef): string {
  const site = siteLabel(ref.source, ref.diagram_url);
  return [`# ${trimmed(ref.part_number) || "no part no."}`, site].filter(Boolean).join(" · ");
}

/** "ref EUR 309.41 ex VAT" on the price side; null with no price. */
export function refPrice(ref: CatalogueRef): string | null {
  return typeof ref.ref_amount === "number" && Number.isFinite(ref.ref_amount) ? priceLabel(ref) : null;
}

/** Where a ticked row goes: an Annex A line's key, or NEW_LINE. */
export const NEW_LINE = "new";

export type AttachTarget = { key: string; label: string };

/** The choices for a ticked row: "line 2 Rear bumper" for each Annex A
 * line, numbered as the table shows them (List Items, then S/Nett), then
 * "New line". */
export function attachTargets(lines: { key: string; part_name: string }[]): AttachTarget[] {
  return [
    ...lines.map((l, i) => ({ key: l.key, label: `line ${i + 1} ${trimmed(l.part_name) || "(no description)"}` })),
    { key: NEW_LINE, label: "New line" },
  ];
}

/** The line a ticked row starts on: the one its match hint names (found
 * by its name, as on screen), else a new line. */
export function defaultTarget(hint: CatalogueMatchHint | null | undefined, lines: { key: string; part_name: string }[]): string {
  const name = trimmed(hint?.line_name);
  if (hint?.kind !== "line" || !name) return NEW_LINE;
  const want = lineKey(name);
  return lines.find((l) => lineKey(l.part_name) === want)?.key ?? NEW_LINE;
}

/** A pick whose line has gone from the table (removed meanwhile) goes to a
 * new line rather than nowhere. */
export function resolvedTarget(target: string | undefined, lines: { key: string }[]): string {
  return target && (target === NEW_LINE || lines.some((l) => l.key === target)) ? target : NEW_LINE;
}

/** Lines two or more ticked rows are set to: a line holds one catalogue
 * row, so [Use] waits until each goes elsewhere (or to a new line). */
export function clashingTargets(targets: string[]): string[] {
  const seen = new Set<string>();
  const twice = new Set<string>();
  for (const t of targets) {
    if (t === NEW_LINE) continue;
    if (seen.has(t)) twice.add(t);
    seen.add(t);
  }
  return [...twice];
}

/** A row by id in the panel's state, with its part and diagram. */
export function findSuggestion(state: CatalogueInfo, id: number): { row: CatalogueSuggestion; part: CataloguePart; diagram: CatalogueDiagram } | null {
  for (const part of state.parts)
    for (const diagram of part.diagrams)
      for (const row of diagram.rows) if (row.id === id) return { row, part, diagram };
  return null;
}

/** The ticked rows put into the Annex A draft: each on its line
 * (attachToLine) or appended as a new line (catalogueLine, made into a
 * draft row by `toRow`). A row already on a line of the draft stays where
 * it is. Returns the new rows; nothing is saved. */
export function applyCataloguePicks<T extends AttachableLine & { _key: string }>(
  rows: T[],
  picks: { target: string; row: CatalogueSuggestion; part: CataloguePart; diagram: CatalogueDiagram }[],
  toRow: (line: ReturnType<typeof catalogueLine>) => T
): T[] {
  const onLines = new Set(rows.map((r) => r.catalogue_ref?.suggestion_id).filter((id): id is number => typeof id === "number"));
  let out = [...rows];
  for (const pick of picks) {
    if (onLines.has(pick.row.id)) continue;
    onLines.add(pick.row.id);
    if (pick.target !== NEW_LINE && out.some((r) => r._key === pick.target)) {
      const ref = catalogueRefOf(pick.row, pick.diagram);
      out = out.map((r) => (r._key === pick.target ? attachToLine(r, ref) : r));
    } else {
      out = [...out, toRow(catalogueLine(pick.row, pick.part, pick.diagram))];
    }
  }
  return out;
}
