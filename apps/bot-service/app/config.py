from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BOT_SERVICE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = (BOT_SERVICE_DIR / "carlink.db").resolve()
DEFAULT_STORAGE_DIR = (BOT_SERVICE_DIR / "storage").resolve()
# /app/secrets in the container (BOT_SERVICE_DIR is /app there).
DEFAULT_SECRETS_DIR = (BOT_SERVICE_DIR / "secrets").resolve()

# The values Gemini's Interactions API takes (google-genai 2.22.0:
# GenerationConfig.thinking_level, ImageContent.resolution, the
# code_execution tool), for the model settings below.
THINKING_LEVELS = ("minimal", "low", "medium", "high")
IMAGE_RESOLUTIONS = ("low", "medium", "high", "ultra_high")
READ_TOOLS = ("code_execution",)
# The entry of a model setting that applies to every model (or every photo
# kind) not named in it.
ANY = "*"


def _entries(text: str | None) -> list[str]:
    return [e.strip() for e in (text or "").split(",") if e.strip()]


def thinking_levels(text: str | None) -> dict[str, str]:
    """A gemini_thinking_level_* setting as {model: level}. "medium" is
    {"*": "medium"}, every model; "gemini-3.5-flash-lite=medium" names one
    model, and a model not named gets no level at all (its own default);
    both together, "low,gemini-3-flash-preview=high", give the bare level to
    the rest. Per model because one chain mixes the strong model (default
    high) with the lite ones (default minimal): raising the lite reader's
    level must not lower the strong one's. {} when unset. Raises ValueError
    on a level Gemini doesn't have."""
    out: dict[str, str] = {}
    for entry in _entries(text):
        model, _, level = entry.rpartition("=")
        model, level = model.strip() or ANY, level.strip().lower()
        if level not in THINKING_LEVELS:
            raise ValueError(f"thinking level {level!r}: use one of {', '.join(THINKING_LEVELS)}")
        out[model] = level
    return out


def image_resolutions(text: str | None) -> dict[str, str]:
    """gemini_image_resolution as {photo kind: resolution}: "damage=ultra_high,
    tyre=ultra_high,overview=medium"; "*=medium" for every kind not named.
    {} when unset. Raises ValueError on a malformed entry or a resolution
    Gemini doesn't have. The kinds are the sorter's (ai/triage.PHOTO_KINDS)
    plus "unsorted"; not checked here (config can't import the sorter), the
    benchmark checks them."""
    out: dict[str, str] = {}
    for entry in _entries(text):
        kind, sep, resolution = entry.partition("=")
        kind, resolution = kind.strip().lower(), resolution.strip().lower()
        if not sep or not kind:
            raise ValueError(f"image resolution {entry!r}: write it as kind=resolution, e.g. damage=ultra_high")
        if resolution not in IMAGE_RESOLUTIONS:
            raise ValueError(f"image resolution {resolution!r}: use one of {', '.join(IMAGE_RESOLUTIONS)}")
        out[kind] = resolution
    return out


def read_tools_by_model(text: str | None) -> dict[str, list[str]]:
    """gemini_read_tools as {model: [tool names]}, written like the
    thinking levels: "code_execution" is {"*": ["code_execution"]}, every
    model; "gemini-3.1-flash-lite=code_execution" names one model, and a
    model not named gets no tools; a bare tool beside pairs goes to every
    model not named. Per model because the tool is a trial not every model
    of a chain takes well: on 12 Nov the drafting schema with code
    execution passed on gemini-3.1-flash-lite and gemini-3-flash-preview,
    while gemini-3.5-flash-lite was busy (503) and then ran out of time.
    {} when unset. Raises ValueError on a tool this code doesn't know how
    to send."""
    out: dict[str, list[str]] = {}
    for entry in _entries(text):
        model, _, name = entry.rpartition("=")
        model, name = model.strip() or ANY, name.strip().lower()
        if name not in READ_TOOLS:
            raise ValueError(f"read tool {name!r}: only {', '.join(READ_TOOLS)}")
        tools = out.setdefault(model, [])
        if name not in tools:
            tools.append(name)
    return out


def read_tools(text: str | None) -> list[str]:
    """Every tool gemini_read_tools names, for any model, once each. [] when
    unset. Raises ValueError on a tool this code doesn't know how to send."""
    return list(dict.fromkeys(name for names in read_tools_by_model(text).values() for name in names))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BOT_SERVICE_DIR / ".env"), ".env"),
        extra="ignore",
    )

    telegram_bot_token: str = ""
    # Where people open Carlink in a browser, for links sent outside the
    # dashboard: the Telegram bot's "open your case" and "connect first"
    # messages. No trailing slash needed. Set in infra/docker-compose.prod.yml
    # (from each stack's infra/.env); the default is production's address.
    public_base_url: str = "https://carlink.34-16-92-98.sslip.io"
    gemini_api_key: str = ""
    # Ordered by real capability, not just cost -- confirmed live:
    # gemini-3-flash-preview is the only one of these that has actually
    # produced a real damage bounding box in testing (see extraction.py's
    # bbox_* prompt); 3.6/3.7-flash are untested-but-plausible mid-tier
    # candidates; the two -lite models never localize but reliably draft
    # everything else, so they're the last-resort fallback that keeps a
    # report drafting even once the stronger models' (much lower) daily
    # quota is exhausted. "antigravity" was in this list before but
    # doesn't accept image input at all (confirmed: HTTP 400 "Image input
    # modality is not enabled") -- every report has photos, so it could
    # never have actually served a request here.
    gemini_model_chain: str = "gemini-3-flash-preview,gemini-3.6-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # Dashboard intake sorts every photo of a case (often 40-80) before the
    # real drafting call. Sorting needs no bounding boxes -- the one thing
    # only the stronger models do -- so it runs on the lite models only,
    # keeping the stronger models' much smaller daily quota for the
    # drafting call. Contract 9 (conflict #15): the strong model is no
    # longer the last resort. With both lites busy, a sort batch spent one
    # of its ~20 calls a day; now that batch fails, the finished ones stay
    # sorted and the wizard offers Retry.
    gemini_triage_model_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # The second look after an intake reading (ai/second_look.py): ONE call
    # per read on its own chain, so it never draws on the reading model's
    # small free daily allowance. Provisional: chosen after measuring models
    # with scripts/benchmark_intake.py --second-look-models. NB on the free
    # tier gemini-3.5-flash and gemini-3.6-flash share ONE allowance of 20 a
    # day (Google's 429 for 3.5 names 3.6), the backup reader's.
    gemini_second_look_chain: str = "gemini-3.5-flash,gemini-3.6-flash"
    # Off until the benchmark shows it finds more parts without many wrong
    # ones (owner's gate, 2026-11-09): no second-look call at all and the
    # Review step shows no suggestions. The benchmark turns it on for a run
    # given --second-look-models.
    second_look_enabled: bool = False
    # Generation settings for each kind of reading call, all unset by
    # default: an unset one sends nothing, so the request is exactly what
    # it was before they existed (contract 6, 2026-11-10). The lite models
    # read at their default thinking level, which is "minimal" on
    # gemini-3.5-flash-lite. Set them to measure (scripts/benchmark_intake.py
    # --thinking-level-read ...), then here once a level has earned it.
    #
    # Thinking level per call kind: "minimal", "low", "medium" or "high" for
    # every model of the chain, or per model ("gemini-3.5-flash-lite=medium",
    # see thinking_levels). A model that refuses a level (gemini-3.8-flash
    # has no "minimal") fails that try like any other error and the chain
    # moves on to the next model.
    # The light models read with thinking "high": on the 4 benchmark cases
    # gemini-3.5-flash-lite found 10 of 67 parts at its default (minimal)
    # and 15 at high, wrong sides 4 -> 1 (2026-11-10). Thinking is free on
    # the free tier. The strong models keep their own default (high).
    gemini_thinking_level_read: str | None = "gemini-3.5-flash-lite=high,gemini-3.1-flash-lite=high"
    gemini_thinking_level_sort: str | None = None
    gemini_thinking_level_second_look: str | None = None
    # Per photo kind on the reading call, e.g. "damage=ultra_high,
    # tyre=ultra_high,overview=medium" (image_resolutions). Each image item
    # gets that "resolution" (low/medium/high/ultra_high = 280/560/1120/2240
    # tokens on Gemini 3; Gemini's default is high); a kind not named is sent
    # without one. Sorting and the second look are never given one.
    gemini_image_resolution: str | None = None
    # Tools for the reading call only: "code_execution" lets the model crop
    # and zoom into a photo itself (Agentic Vision). With a JSON schema this
    # is a preview feature on Gemini 3 (Google's structured-output docs),
    # untested on the lite models: a trial setting. For every model of the
    # chain, or per model like the thinking levels
    # ("gemini-3.1-flash-lite=code_execution", see read_tools_by_model).
    gemini_read_tools: str | None = None
    # Contract 8 D1: the light reader looking closer -- Gemini zooming in
    # itself (the code execution tool above) and our own close-ups of the
    # damage the sorter boxed. All off by default, and off the requests are
    # byte for byte today's. scripts/benchmark_intake.py measures each
    # (--read-tools, --read-tools-timeout, --zoom-hint, --sort-damage-box,
    # --read-crops) before it is turned on.
    #
    # Seconds per model try of a reading whose request carries tools: the
    # model runs its code on Google's side before it answers, and a 32-photo
    # reading can take longer than the caller's timeout (intake's 120 s).
    # None: the caller's timeout, as before. A try that runs out still
    # falls through to the next model.
    gemini_read_tools_timeout: float | None = None
    # extraction.ZOOM_NOTE added to the reading's system prompt -- crop and
    # zoom into each damaged area before naming the part and its side -- on
    # a try whose request carries tools, and only then. A setting of its
    # own so the tool can be measured both asked and not asked to zoom.
    gemini_read_zoom_hint: bool = False
    # The sorter also boxes the damage on each photo showing it
    # (IntakePhoto.damage_box, [ymin, xmin, ymax, xmax] on 0-1000). Changes
    # the sort's prompt and schema, so sorts made with it are cached apart
    # (triage.sort_prompt_version).
    intake_sort_damage_box: bool = False
    # F7 (plans/2026-11-22-enhance-staging): the photos a sorting batch
    # skipped -- a batch answered for at least half its photos, so the rest
    # stayed unsorted and got no zone read -- go into ONE more small batch
    # at the end (at most BATCH_SIZE photos; +0-1 call, only when it
    # happens). "off": they stay unsorted, as before.
    intake_sort_retry_skipped: str = "off"
    # B4 (plans/2026-11-22-enhance-staging R-batching): sorting batches in
    # flight at once (intake/pipeline._sort), 1 to 5. 3: as before. 5 sorts
    # a 60-photo case in one wave; the requests still go through the rate
    # limiter (ai/rate_limit.acquire: at least gemini_min_call_interval_
    # seconds apart, at most gemini_max_requests_per_minute a minute).
    intake_sort_workers: int = 3
    # B9: one more sort call at thinking "low" (ai/triage.rescue_photos) for
    # the photos about to drop out of every read -- called unusable,
    # interior, document, third_party or scene, "other" with no damage, or
    # skipped by a batch -- asking only whether each shows damage on the
    # insured car and where; a "yes" puts it back as a damage photo, so the
    # zone reads see it. +0-1 call, only when such photos exist. "off":
    # none, as before.
    intake_sort_rescue: str = "off"
    # B5: when a person regroups a photo, the sorter's own kind is kept
    # (IntakePhoto.ai_kind; data["photo_index"][i]["ai_kind"] once filed),
    # and the AI-accuracy record counts the regroups at sign-off. "off":
    # the sorter's kind is overwritten and nothing is counted, as before.
    intake_keep_sort_label: str = "off"
    # Right after a photo the sorter boxed, the reading gets a close-up of
    # that box cut from the largest copy of the photo we keep
    # (intake/crops.py), as "Close-up of photo P07:" and the image. At most
    # intake_read_crops_max per reading, damage close-ups first: each is one
    # more image (~1120 tokens at Gemini 3's default resolution, high).
    intake_read_crops: bool = False
    intake_read_crops_max: int = 8
    # How many photos one intake reading sends (intake/pipeline.read_limit);
    # unset: pipeline.MAX_READ_PHOTOS (32). E12 (plans/2026-11-22-enhance-
    # staging 3c, Wave 2): 40 at today's image size, or 48-64 with
    # gemini_image_resolution_main_read below at medium -- the same image
    # tokens as 32 at Gemini's default. 1 to MAX_READ_PHOTOS_LIMIT; the
    # photos are picked in the same priority order (pipeline.
    # select_for_reading), and above 32 a request never grows past
    # pipeline.READ_BYTES_BUDGET.
    intake_max_read_photos: int | None = None
    # E12b: the main read's own photo resolution, written like
    # gemini_image_resolution ("*=medium", or per photo kind). Set, it
    # replaces gemini_image_resolution on the main read's photos only (the
    # zone reads keep theirs); the Telegram bot's drafts, whose photos have
    # no kind, are sent as before. Unset (the default): as before.
    gemini_image_resolution_main_read: str | None = None
    # E7 (Wave 2): the side worked out in code. The sort also says per photo
    # which part of the car it looks at (view), which way the car's front
    # points (its existing angle) and where the steering wheel shows;
    # intake/geometry.py turns that into the car's left or right (right-hand
    # drive) for whole-side views, takes a majority over the photos of one
    # area, and uses it where code decides a side from the reads: the side
    # zone reads' side_seen (zones) and the side resolver (pipeline.
    # resolve_sides). "vote": one more vote; "tiebreak": only where today's
    # rule leaves the side undetermined; "shadow": the sort is asked and the
    # geometry stored as for those two (the same sort cache), nothing uses
    # it. "off": the sort is asked nothing new -- its request byte for byte
    # as before -- and no side changes.
    intake_side_geometry: str = "off"
    # E11 (Wave 2): an end zone read (front, rear) whose photos it says show
    # the OTHER end. "drop": its finds are dropped, as before. "shadow": the
    # run log also keeps what "keep" would add (zones' entry wrong_end);
    # nothing else changes. "keep": its finds become suggestions with the
    # note "The rear read thought these photos show the front." -- never on
    # the list.
    intake_zone_wrong_end: str = "drop"
    # A part the reader names on a photo the sorter tagged with the same
    # part on the other side takes the sorter's side (intake/pipeline.
    # side_from_sorter). Off: the reader's side stands, as before.
    # Off: measured on the light reader (2026-11-10) it added no parts and
    # made sides worse when the sorter's own side was wrong.
    intake_side_from_sorter: bool = False
    # F3 (plans/2026-11-22-enhance-staging): the side resolver
    # (intake/pipeline.resolve_sides) drops a "(side undetermined)" find it
    # places on a side whose name is already listed. "on": when that find
    # is on a different photo from the listed part, it is kept as a
    # suggestion instead (source "other_side", other_side_of the listed
    # name: "Other side? Tail Lamp (side undetermined)") -- two lamps on two
    # photos no longer become one. "off": dropped, as before.
    intake_keep_other_side: str = "off"
    # Sweeps idle intake drafts and stale tmp uploads (intake/cleanup.py).
    # Off in tests and scripts that import the app; set in production.
    run_background_cleanup: bool = False
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
    storage_dir: str = str(DEFAULT_STORAGE_DIR)
    # Holds ai-keys.secret, which seals people's own AI keys (ai/secretbox.py).
    # Its own volume in production (/app/secrets: the api writes, the bot
    # only reads), never under storage_dir, which /files serves and the
    # backups copy.
    secrets_dir: str = str(DEFAULT_SECRETS_DIR)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # Minimum spacing enforced between Gemini calls, per process (see
    # extraction.py's _wait_for_rate_limit) -- a conservative placeholder
    # until real RPM numbers from the account's actual quota tier are
    # available to tune this precisely. Only paces calls made *within* one
    # process (telegram-bot and api are separate processes sharing the same
    # GEMINI_API_KEY, so this doesn't coordinate across them) -- a real
    # cross-process limit would need a shared store (Redis, or the sqlite
    # db both containers already mount), not worth the added infra until
    # this is actually the bottleneck in practice.
    # Minimum gap between consecutive calls, so a burst under the per-minute
    # cap still arrives spaced rather than all at once.
    gemini_min_call_interval_seconds: float = 4.0
    # Hard ceiling per API key per rolling 60s, enforced across BOTH the API
    # and bot processes (see ai/rate_limit.py). Deliberately under the free
    # tier's 20/min for gemini-3-flash, because one draft can spend several
    # requests walking the fallback chain.
    gemini_max_requests_per_minute: int = 12
    # The shared quota guard (ai/quota_guard.py; plans/2026-11-22 "B1"):
    # production and staging on ONE Gemini key -- one daily allowance per
    # model and one per-minute window for both, since Google's limits are per
    # Google Cloud project. Blank (the default): off -- the window and the
    # "day used up" memory stay in this stack's STORAGE_DIR (.ratelimit,
    # .allowance) and every request is exactly as before. Set to the folder
    # infra/docker-compose.ai-shared.yml mounts (/app/ai-shared): both stacks
    # keep the window, a per-day request count per model and lane, and the
    # day memory there. A set folder that isn't there (not mounted yet) falls
    # back to the old files, so production keeps working.
    gemini_shared_quota_dir: str = ""
    # This stack's lane in the shared window. "prod": up to
    # gemini_max_requests_per_minute, never waiting for staging. "staging":
    # the limits below, and nothing sent at all without the shared folder.
    gemini_quota_lane: str = "prod"
    # Staging takes a slot only while the shared window, with its slot,
    # holds at most gemini_staging_window_share requests (both lanes), and at
    # most its per-minute cap of its own: gemini_staging_max_per_minute, and
    # never more than gemini_quota_minute_budget - gemini_quota_prod_per_minute
    # (review M6), so the two lanes together never send more than the budget
    # in any 60 s -- production at its full cap included. Defaults: 14 - 12 =
    # 2 a minute for staging.
    gemini_staging_max_per_minute: int = 3
    gemini_staging_window_share: int = 6
    # Most requests both stacks together may send in any 60 s on the one
    # key (under Google's ~15 a minute per model), and production's own cap
    # as both stacks reckon it: the production lane never takes more than
    # this either. Keep it equal to production's
    # gemini_max_requests_per_minute.
    gemini_quota_minute_budget: int = 14
    gemini_quota_prod_per_minute: int = 12
    # Staging sends nothing more until the next reset (midnight Pacific)
    # once it has sent gemini_staging_day_max requests today, production has
    # sent gemini_staging_prod_busy, or -- per model -- fewer than
    # gemini_staging_model_reserve of the model's day are left.
    gemini_staging_day_max: int = 300
    gemini_staging_prod_busy: int = 400
    gemini_staging_model_reserve: int = 300
    # Requests a day per model on the free tier, for the reserve above and
    # the extras tiers (quota_guard.extras_tier): "model=N,model=N". A limit
    # Google itself stated in a 429 (ai/allowance.py) wins. Staging never
    # uses a model with no known limit (the strong model's 20 a day stays
    # production's).
    gemini_quota_day_limits: str = "gemini-3.5-flash-lite=500,gemini-3.1-flash-lite=500"
    # The model whose day left sets production's extras tier (RICH > 50%
    # left, NORMAL 25-50%, LEAN < 25%; the base pipeline is never cut).
    gemini_quota_tier_model: str = "gemini-3.5-flash-lite"
    # Dashboard sign-in (app/auth). A session ends after this long without
    # use, and in any case this many days after signing in.
    auth_session_idle_hours: float = 12
    auth_session_max_days: float = 7
    # The session cookie is only sent over HTTPS. Production is HTTPS (host
    # nginx, infra/host-nginx/); browsers also accept it on http://localhost.
    # Set false only for plain-HTTP access from another machine.
    auth_cookie_secure: bool = True
    # New passwords: off = at least 10 characters (the owner's choice for
    # now); on = at least 15 plus the common/predictable/personal checks in
    # app/auth/passwords.py. The dashboard's hints say 10 -- update
    # MIN_PASSWORD_LENGTH in apps/dashboard/lib/signup.ts when switching.
    strict_password_rules: bool = False
    # Uploads and new cases are refused (507 disk_full, and a reply from the
    # Telegram bot) once the storage disk has less than this many GB free: a
    # full disk breaks SQLite writes for both processes, and the VM's ~10GB
    # disk is shared with another app. See storage/files.disk_space_low.
    min_free_disk_gb: float = 3.0
    # /docs, /redoc and /openapi.json. Off: they list every route to anyone
    # who signs up.
    enable_api_docs: bool = False
    # The parts-catalogue look-up (app/api/catalogue.py): a case's damaged
    # parts are looked up -- when a person asks, or by itself once the AI
    # reading is saved -- and the catalogue worker (tools/parts_mcp, the
    # "parts-worker" container) does the looking. Off unless BOTH are set:
    # the flag, and the token the worker sends as "Authorization: Bearer
    # <token>". While off, the case page doesn't show the feature and the
    # worker's endpoints answer 404. Set in infra/.env, never in git.
    catalogue_enabled: bool = False
    catalogue_worker_token: str = ""
    # The makes the worker can look up (comma-separated, any case: "Mini" is
    # MINI). It reads the microfiches sites only (parts-honda.uk,
    # bmw-spare-parts.com, mini-spare-parts.com). Another make is never
    # looked up: GET .../catalogue says "make_not_covered", and nothing is
    # queued after detection (catalogue.maybe_auto_lookup).
    catalogue_makes: str = "Honda,BMW,MINI"

    # Contract 8 L: every signed-off case teaches the past-case parts lists.
    # At sign-off its final Annex A joins the past-case history that Annex A
    # suggestions and prices come from, as the past case "carlink:<report
    # id>" (app/parts/learn.py) -- replaced on a re-sign-off, removed when
    # the case is reopened or deleted -- so the kits improve without anyone
    # importing spreadsheets. Off: nothing is learned, and the lines learned
    # so far are left out of every suggestion (history.past_lines): the
    # history is the imported spreadsheets alone, as before. No Gemini call
    # either way; the AI accuracy record (app/reports/accuracy.py) is kept
    # either way.
    learn_from_signed_off: bool = True

    # The shared quota guard's settings: a typo stops the app at start-up --
    # a staging stack that quietly ran in the production lane would take
    # production's share of the one key.
    @field_validator("gemini_quota_lane")
    @classmethod
    def _quota_lane(cls, value):
        return _one_of(value, QUOTA_LANES, "gemini_quota_lane")

    @field_validator("gemini_quota_day_limits")
    @classmethod
    def _quota_day_limits(cls, value):
        return ",".join(f"{m}={n}" for m, n in day_limits(value).items())

    @field_validator("gemini_staging_max_per_minute", "gemini_staging_window_share", "gemini_staging_day_max",
                     "gemini_quota_minute_budget", "gemini_quota_prod_per_minute",
                     "gemini_staging_prod_busy", "gemini_staging_model_reserve")
    @classmethod
    def _quota_counts(cls, value):
        if value < 0:
            raise ValueError("the gemini_staging_* limits: 0 or more")
        return value

    # A typo in one of the model settings stops the app at start-up rather
    # than being sent to Gemini (a 400 on every call) or silently ignored.
    @field_validator("gemini_thinking_level_read", "gemini_thinking_level_sort", "gemini_thinking_level_second_look")
    @classmethod
    def _thinking_level(cls, value):
        return normal_thinking_levels(value)

    @field_validator("gemini_image_resolution", "gemini_image_resolution_main_read")
    @classmethod
    def _image_resolution(cls, value):
        return normal_image_resolutions(value)

    # E12: blank is unset (32); 1 to MAX_READ_PHOTOS_LIMIT.
    @field_validator("intake_max_read_photos", mode="before")
    @classmethod
    def _max_read_photos(cls, value):
        return normal_max_read_photos(value)

    @field_validator("intake_side_geometry")
    @classmethod
    def _side_geometry(cls, value):
        return _one_of(value, SIDE_GEOMETRY, "intake_side_geometry")

    @field_validator("intake_zone_wrong_end")
    @classmethod
    def _zone_wrong_end(cls, value):
        return _one_of(value, ZONE_WRONG_END, "intake_zone_wrong_end")

    @field_validator("gemini_read_tools")
    @classmethod
    def _read_tools(cls, value):
        return normal_read_tools(value)

    # Contract 8 D1: blank is unset; 0 or less is refused (a try that can
    # never answer, or a negative number of close-ups).
    @field_validator("gemini_read_tools_timeout", mode="before")
    @classmethod
    def _read_tools_timeout(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if float(value) <= 0:
            raise ValueError("gemini_read_tools_timeout: seconds, more than 0")
        return float(value)

    @field_validator("intake_read_crops_max")
    @classmethod
    def _read_crops_max(cls, value):
        if value < 0:
            raise ValueError("intake_read_crops_max: 0 or more")
        return value

    @field_validator("intake_sort_retry_skipped", "intake_keep_other_side", "intake_sort_rescue",
                     "intake_keep_sort_label")
    @classmethod
    def _off_on(cls, value, info):
        return _one_of(value, OFF_ON, info.field_name)

    @field_validator("intake_sort_workers", mode="before")
    @classmethod
    def _sort_workers(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return 3
        try:
            workers = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"intake_sort_workers {value!r}: a whole number, 1 to {MAX_SORT_WORKERS}") from None
        if not 1 <= workers <= MAX_SORT_WORKERS:
            raise ValueError(f"intake_sort_workers {value!r}: 1 to {MAX_SORT_WORKERS}")
        return workers

    # Contract 8 D2: reading the car one area at a time (intake/zones.py).
    # "single": the one overall reading, as before. "zones": after it, one
    # short call per area the photos show -- front, rear, left, right, and
    # damage close-ups whose area isn't clear -- each with that area's
    # checklist (ai/zone_read.py), at most 5 more calls a case; their finds
    # are merged into the overall reading's, and a part only one read saw,
    # on one photo, is marked Low for a person to check. Measured before it
    # is turned on (scripts/benchmark_intake.py --read-mode zones).
    intake_read_mode: str = "single"
    # Zone calls only: each photo goes as a copy with a light 3x3 grid
    # (A1..C3) drawn on it (ai/grid.py), and the model gives the cells the
    # damage covers before it names the panel. Off: the photos as they are.
    intake_grid_marks: bool = False

    @field_validator("intake_read_mode")
    @classmethod
    def _read_mode(cls, value):
        return normal_read_mode(value)

    # Contract 9: a reason, a photo box and a 3D link for every finding,
    # on the free tier. Every default sends today's requests byte for byte,
    # but two the owner agreed to: the sort chain above no longer ends in
    # the strong model, and store=False goes on every call
    # (gemini_store_off).
    #
    # The zone calls' models (intake/zones.py), never the reading chain:
    # the reading's first model has ~20 calls a day on the free tier, and
    # ~5 zone calls a case would spend them in ~3 cases (conflict #14).
    gemini_zone_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # store=False on every Gemini call (structured.call_structured): on the
    # free tier Google otherwise keeps each request -- the case's photos --
    # for a day so it can be fetched again, which nothing here does. Checked
    # on 15 Nov on the pinned google-genai 2.22.0 (3.1-flash-lite and
    # gemini-4-hydrogen): the answers are the same. False: today's request,
    # with no store field (the benchmark's --store-on, for an A/B).
    gemini_store_off: bool = True

    # Option B (zone mode only, settings.intake_read_mode "zones"):
    # "suggest" keeps the damage list to the parts the overall reading found
    # and makes the parts only zone reads found suggestions a person adds
    # (intake/zones.split_suggestions); "list" puts every find in the list
    # (contract 8's m5/m6 runs). Measured offline on m6: the list's wrong
    # sides and extras fall, the zone-only finds wait for a person.
    intake_zone_only: str = "suggest"
    # The zone reads' tail fields (ai/zone_read.py, section 4.7): "off" asks
    # for nothing new; "reason" adds each find's reason; "reason_box" also
    # its box_2d. Turned on after the R2 run (reason_box only with R3's box
    # probe at 14/20 or better).
    intake_zone_evidence: str = "off"
    # The re-check of the suggestions (ai/check.py, section 4.7): "off";
    # "decision", the Decisions API; "lite", one batched call on
    # gemini_check_chain; "decision_then_lite", the lite call when the
    # Decisions API fails. It labels and sorts the suggestions, never adds
    # or removes one. Turned on after the R4 / R5 runs.
    intake_check_mode: str = "off"
    # The Decisions API's model (a plain httpx call: it is not in the SDK).
    gemini_decision_model: str = "gemini-decision-1"
    # The lite re-check's models: never the strong one.
    gemini_check_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # The lite re-check's thinking level, written like the others above
    # (thinking_levels); unset sends none.
    gemini_thinking_level_check: str | None = None
    # The resolution of the photos sent to the Decisions API: low, medium,
    # high or ultra_high (266 tokens a photo at low).
    intake_check_resolution: str = "medium"
    # The Decisions API's yes-probability p: "yes" at intake_check_yes or
    # more, "no" at intake_check_no or less, "unsure" between. Set from
    # R4's offline sweep.
    intake_check_yes: float = 0.7
    intake_check_no: float = 0.3
    # F1 (plans/2026-11-22-enhance-staging, owner decision 9): when the
    # main read fails (every model busy, out of allowance, the person's AI
    # unavailable) -- "zones": the zone reads still run (zone mode), then
    # the main read is tried once more; if it fails again, the zone reads'
    # finds become the list as "Check" (Low) rows, with a notice ("The
    # overall read failed. This list is from the area reads. Press
    # Re-read."; IntakeSession.ai_draft["main_read"], GET /intake/{id}'s
    # read_notice). "off": the run fails ("Retry"), as before.
    intake_main_read_fallback: str = "off"
    # B2 (R-batching): zone mode's zone reads start beside the main read
    # instead of after it -- they need only the sort (intake/pipeline.
    # ParallelZones). The same calls, through the same rate limiter. It acts
    # ONLY together with intake_main_read_fallback "zones" (the review's
    # M4): then a failed main read uses the zone answers already paid for;
    # without F1 they would be thrown away and paid for again on Retry, so
    # the reads stay one after the other. "off": one after the other, as
    # before.
    intake_parallel_reads: str = "off"

    @field_validator("intake_main_read_fallback")
    @classmethod
    def _main_read_fallback(cls, value):
        return _one_of(value, MAIN_READ_FALLBACKS, "intake_main_read_fallback")

    @field_validator("intake_parallel_reads")
    @classmethod
    def _parallel_reads(cls, value):
        return _one_of(value, OFF_ON, "intake_parallel_reads")

    @field_validator("gemini_zone_chain", "gemini_check_chain")
    @classmethod
    def _chains(cls, value, info):
        return normal_chain(value, info.field_name)

    @field_validator("intake_zone_only")
    @classmethod
    def _zone_only(cls, value):
        return normal_zone_only(value)

    @field_validator("intake_zone_evidence")
    @classmethod
    def _zone_evidence(cls, value):
        return normal_zone_evidence(value)

    @field_validator("intake_check_mode")
    @classmethod
    def _check_mode(cls, value):
        return normal_check_mode(value)

    @field_validator("gemini_decision_model")
    @classmethod
    def _decision_model(cls, value):
        model = str(value or "").strip()
        if not model:
            raise ValueError("gemini_decision_model: a model name")
        return model

    @field_validator("gemini_thinking_level_check")
    @classmethod
    def _thinking_level_check(cls, value):
        return normal_thinking_levels(value)

    @field_validator("intake_check_resolution")
    @classmethod
    def _check_resolution(cls, value):
        return normal_check_resolution(value)

    @field_validator("intake_check_yes", "intake_check_no")
    @classmethod
    def _check_cut(cls, value, info):
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{info.field_name}: a probability, 0 to 1")
        return float(value)

    @model_validator(mode="after")
    def _check_cuts_in_order(self):
        # "no" at or under intake_check_no, "yes" at or over intake_check_yes:
        # the other way round, one p would be both.
        if self.intake_check_no >= self.intake_check_yes:
            raise ValueError("intake_check_no must be under intake_check_yes")
        return self

    # Contract 10: linked parts in detection (plans/2026-11-17-linked-parts/
    # contract10.md section 4.6). A part on the damage list brings the parts
    # tied to it -- the beam behind a bumper cover, the hinges a boot lid
    # opens with -- as tick-to-add suggestions (intake/links.py and its
    # table, intake/link_table.json), and a light "linked check" call can
    # look at them (ai/link_check.py). Everything here is off by default:
    # no linked part, no call, and every request and saved draft is byte
    # for byte today's.
    #
    # "off": no linked parts at all. "shadow": worked out and kept in the
    # AI draft only (ai_draft["link_suggestions"], counted at sign-off),
    # never shown. "on": shown with the suggestions on the Review step.
    intake_links: str = "off"
    # The linked parts' own cap, on top of the second look's 16
    # (second_look.MAX_SUGGESTIONS); at most 4 per listed part.
    intake_links_max: int = 12
    # The kinds of link that may show (link_table.json "type"): any of
    # attached, opens_with, behind, crush.
    intake_links_types: str = "attached,opens_with,behind,crush"
    # When a linked part takes the side of the listed part it hangs off.
    # "firm": that part's name has a side the reading gave it itself (not
    # side_inferred, not Low, not disputed); "strict": also seen in 2
    # photos or found by 2 reads. Otherwise the "(side undetermined)" name.
    intake_links_side: str = "firm"
    # How hard a listed part was hit when its words don't say (no Dented,
    # Bent, Crumpled ...): light, medium, heavy or crush. A link shows at
    # its own min_hit or harder.
    intake_links_unknown_hit: str = "medium"
    # A linked part a zone read listed as intact: "demote" (shown lower,
    # with a note -- a zone read's "intact" is sometimes wrong), "drop", or
    # "ignore" (as if no read had said so).
    intake_links_intact: str = "demote"
    # Also suggest the parts the parts catalogue draws on the same OEM
    # diagram as a listed part (catalogue.diagram_neighbours: its cache
    # only, never a web request during a reading).
    intake_links_catalogue: bool = False
    # The linked check (ai/link_check.py): "off", or "lite" -- per damaged
    # end of the car, one call on gemini_link_chain with that end's photos,
    # its skill file (ai/skills/) and the closed list of its linked parts.
    # The answer re-buckets them; it never adds a part or sets a side.
    intake_link_check: str = "off"
    # The linked check's models: light ones only, never the strong model
    # (its ~20 calls a day are the reading's).
    gemini_link_chain: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    # At most this many linked-check calls a case (one per damaged end).
    intake_link_check_max_calls: int = 2
    # The linked check's thinking level, written like the others above
    # (thinking_levels); unset sends none.
    gemini_thinking_level_link: str | None = None
    # Staff photos sent to the Telegram bot go through the website's intake
    # pipeline -- the same sort, reading, zone reads and suggestions. Off:
    # the bot drafts from them as it does today.
    telegram_full_detection: bool = False
    # The car map (plans/2026-11-18-car-knowledge/K1-design.md;
    # intake/car_map.py): which link table the linked parts are built from.
    # "off": L1 (intake/link_table.json) as it is, byte for byte. "shadow":
    # L1 shown, the car map's table worked out beside it for the AI draft.
    # "on": the car map's table shown (L1 when the map can't be used).
    # car_map.link_tables reads it: second_look._store, the re-link after
    # a hand-add (reports/suggestions.relinked) and the linked check's
    # areas (ai/link_check._plan) build from the table it names (K4).
    intake_car_map: str = "off"
    # The car map's own part names (taxonomy.CAR_MAP_PARTS: a bumper's
    # absorber and brackets, a door's lock, hinges and handle ...). "off":
    # the car map's table leaves out every relation that names one (today's
    # linked parts) and the part pickers offer today's list. "on": its
    # linked parts may name them (car_map.table_for_links) and GET
    # /taxonomy tells the dashboard to offer them. Never in a Gemini enum of
    # the reads either way; a case that already holds one keeps it valid
    # and printed when this is turned off.
    intake_car_map_names: str = "off"
    # The assembly walk (K1-design.md 5a; intake/assembly_walk.py). "on":
    # each zone read also answers short yes/no signs about the damaged
    # anchor panels of the car map (car_map.MAP; none with signs: the walk
    # is skipped) inside the same call, kept as ai_draft["assembly_walk"],
    # and the linked parts use them. "off": every zone request and linked
    # part as before.
    intake_assembly_walk: str = "off"
    # How far a "yes" sign lifts a listed part's hit level: "one" step above
    # its own words at most, or "full" (up to the sign's level). Never lower.
    intake_walk_raise: str = "one"
    # 2: a car map chain's second hop (anchor -> part -> part) when a sign
    # that unlocks it says yes; at most 4 such parts a case. 1: one hop.
    intake_link_hops: int = 1
    # The car map's review screen (K1-design.md 5b, 6; intake/links.Options):
    # how the linked parts are ordered and capped. Every default is today's
    # order and caps, byte for byte.
    # "table": inside a bucket by kind of link, hit and table order (L1);
    # "prior": a relation's prior rank first (1 listed most often .. 5; a
    # relation with none counts as 3) -- only the car map has priors.
    intake_links_order: str = "table"
    # Linked parts per group and bucket: a group is an assembly with the
    # car map "on" (one per side for a door), else the listed part (L1's 4).
    intake_links_assembly_max: int = 4
    # "in": the strip-down bucket counts in intake_links_max (L1); "out":
    # it is its own folded block on the Review step ("Check at strip-down
    # (N)"), at most intake_links_stripdown_max, never counted in the 12.
    # A second hop's part (hop 2) goes there unless a photo shows it.
    intake_links_stripdown_cap: str = "in"
    intake_links_stripdown_max: int = 12
    # F2 + F5 (plans/2026-11-22-enhance-staging): what the caps cut -- the
    # suggestions past second_look.MAX_SUGGESTIONS, the linked parts past
    # intake_links_assembly_max a group or intake_links_max (and the
    # strip-down block's max), a linked part dropped because a zone read
    # called it intact -- is kept beside the suggestions, never shown or
    # printed until the Review step folds it in "More (N)":
    # data["second_look"]["cut"] (each with cut_reason "limit" or
    # "intact") and ["cut_counts"]. "off": cut and gone, as before.
    intake_keep_cut: str = "off"
    # F6: the linked check's "no" on a part the table marks hidden (a beam,
    # a regulator: no photo can show it) becomes "cannot_tell", as the cue
    # rule does for a seen with nothing to point at. "keep": as before.
    intake_link_hidden_no: str = "keep"
    # B3 (R-batching): the linked check's calls (one per area, at most
    # intake_link_check_max_calls) at the same time, each area standing
    # alone -- one failed call no longer ends the step for the others.
    # "off": one after the other, a failure ending the step, as before.
    intake_link_check_parallel: str = "off"

    @field_validator("intake_keep_cut", "intake_link_check_parallel")
    @classmethod
    def _keep_cut(cls, value, info):
        return _one_of(value, OFF_ON, info.field_name)

    @field_validator("intake_link_hidden_no")
    @classmethod
    def _link_hidden_no(cls, value):
        return _one_of(value, LINK_HIDDEN_NO, "intake_link_hidden_no")

    @field_validator("intake_links")
    @classmethod
    def _links_mode(cls, value):
        return normal_links_mode(value)

    @field_validator("intake_links_max", "intake_link_check_max_calls", "intake_links_assembly_max",
                     "intake_links_stripdown_max")
    @classmethod
    def _links_counts(cls, value, info):
        return normal_count(value, info.field_name)

    @field_validator("intake_links_types")
    @classmethod
    def _links_types(cls, value):
        return normal_link_types(value)

    @field_validator("intake_links_side")
    @classmethod
    def _links_side(cls, value):
        return normal_links_side(value)

    @field_validator("intake_links_unknown_hit")
    @classmethod
    def _links_unknown_hit(cls, value):
        return normal_hit_level(value)

    @field_validator("intake_links_intact")
    @classmethod
    def _links_intact(cls, value):
        return normal_links_intact(value)

    @field_validator("intake_link_check")
    @classmethod
    def _link_check(cls, value):
        return normal_link_check(value)

    @field_validator("gemini_link_chain")
    @classmethod
    def _link_chain(cls, value):
        return normal_chain(value, "gemini_link_chain")

    @field_validator("gemini_thinking_level_link")
    @classmethod
    def _thinking_level_link(cls, value):
        return normal_thinking_levels(value)

    @field_validator("intake_car_map")
    @classmethod
    def _car_map(cls, value):
        return normal_car_map_mode(value)

    @field_validator("intake_car_map_names")
    @classmethod
    def _car_map_names(cls, value):
        return normal_car_map_names(value)

    @field_validator("intake_assembly_walk")
    @classmethod
    def _assembly_walk(cls, value):
        return normal_walk_mode(value)

    @field_validator("intake_walk_raise")
    @classmethod
    def _walk_raise(cls, value):
        return normal_walk_raise(value)

    @field_validator("intake_link_hops", mode="before")
    @classmethod
    def _link_hops(cls, value):
        return normal_link_hops(value)

    # Light-model detection (plans/2026-11-18-light-detection/P-prompts.md):
    # instruction packs (ai/prompt_packs.py, ai/packs/*.txt) and prompt
    # versions, one switch per benchmark experiment (P's ids in brackets).
    # Every default is today's: the requests are byte for byte what they
    # were (scripts/test_light_packs.py). scripts/benchmark_intake.py has a
    # flag for each; set one here only once its experiment has won.
    #
    # The main read (ai/extraction.draft_report -- the Telegram bot's
    # drafts too, which share it): packs, any of orient (R1: today's sides
    # rule replaced by orient-v1), cues and small (R2: added after it).
    intake_read_packs: str = ""
    # "v1": today's prompt. "v2" (R3): P's layout -- evidence rules for
    # identifiers only, the damage list last with a walk A-E, the packs in
    # it, and a last text part after the photos.
    intake_read_prompt: str = "v1"
    # v2 only (R4): the description's first sentence names where the car was
    # hit, and point_of_impact and the final check agree with it.
    intake_read_anchor: bool = False
    # The zone reads (ai/zone_read.py): packs, any of small (Z1: this zone's
    # rows), intact (Z2: when a part may be called intact, the same words on
    # the schema's checked_intact, and a part in both lists kept out of
    # intact), orient and cues (Z3).
    intake_zone_packs: str = ""
    # Z5: the end zones (front, rear) also say which half of the photo each
    # find is in, and code works out its side from it; a side the model
    # named that disagrees becomes the "(side undetermined)" name.
    intake_zone_photo_half: bool = False
    # The sort (ai/triage.py): "v2" today's; "v3" (S2) with orient-v1 and
    # small parts named. A sort made by another version is never served from
    # the benchmark's sort cache (triage.sort_prompt_version). S1 is
    # gemini_thinking_level_sort: at "high" a batch's try gets 90 s, not 60.
    intake_sort_prompt: str = "v2"
    # The linked check (ai/link_check.py). L1 is gemini_thinking_level_link:
    # at "high" a call's time box is 60 s, not 30. "L1": the frozen skill
    # files (ai/skills/); "L2": ai/skills-L2/ (worked examples, and an unseen
    # part never "no" for that alone).
    intake_link_skill: str = "L1"
    # "v1": today's base prompt; "v2": P's (work in order: the parents' hit,
    # then each candidate). P's L2 arm is skill L2 with prompt v2.
    intake_link_prompt: str = "v1"
    # L3 (needs intake_link_skill L2): the skill's "Signs of hidden damage"
    # answered in a `signs` field before the candidates (the .signs.md
    # skills); kept on the linked check's CheckRun only, never used yet.
    intake_link_signs: bool = False
    # L4: "candidates_first" (today) or "photos_first": the closed list
    # moves after the photos, as the last text part.
    intake_link_order: str = "candidates_first"

    @field_validator("intake_read_packs")
    @classmethod
    def _read_packs(cls, value):
        return normal_packs(value, READ_PACKS, "intake_read_packs")

    @field_validator("intake_zone_packs")
    @classmethod
    def _zone_packs(cls, value):
        return normal_packs(value, ZONE_PACKS, "intake_zone_packs")

    @field_validator("intake_read_prompt")
    @classmethod
    def _read_prompt(cls, value):
        return _one_of(value, READ_PROMPTS, "intake_read_prompt")

    @field_validator("intake_sort_prompt")
    @classmethod
    def _sort_prompt(cls, value):
        return _one_of(value, SORT_PROMPTS, "intake_sort_prompt")

    @field_validator("intake_link_skill")
    @classmethod
    def _link_skill(cls, value):
        return normal_link_skill(value)

    @field_validator("intake_link_prompt")
    @classmethod
    def _link_prompt(cls, value):
        return _one_of(value, LINK_PROMPTS, "intake_link_prompt")

    @field_validator("intake_link_order")
    @classmethod
    def _link_order(cls, value):
        return _one_of(value, LINK_ORDERS, "intake_link_order")

    @model_validator(mode="after")
    def _light_switches_that_act(self):
        # A switch that would change nothing is a typo by another name.
        problem = light_conflict(self.intake_read_prompt, self.intake_read_anchor, self.intake_link_skill,
                                 self.intake_link_signs)
        if problem:
            raise ValueError(problem)
        return self

    @field_validator("intake_links_order")
    @classmethod
    def _links_order(cls, value):
        return normal_links_order(value)

    @field_validator("intake_links_stripdown_cap")
    @classmethod
    def _links_stripdown_cap(cls, value):
        return normal_stripdown_cap(value)


READ_MODES = ("single", "zones")
# plans/2026-11-22-enhance-staging's fixes: the first of each is the
# default, today's behaviour.
OFF_ON = ("off", "on")
MAIN_READ_FALLBACKS = ("off", "zones")
LINK_HIDDEN_NO = ("keep", "cannot_tell")
# B4: at most this many sorting batches at once (5 batch starts still sit
# under the 12-a-minute limit; the limiter spaces them).
MAX_SORT_WORKERS = 5
# Wave 2 (plans/2026-11-22-enhance-staging 3c): the first of each is the
# default, today's behaviour. E7, E11.
SIDE_GEOMETRY = ("off", "shadow", "vote", "tiebreak")
ZONE_WRONG_END = ("drop", "shadow", "keep")
# E12: the most photos one reading may send (64 at medium is 32 at high's
# image tokens).
MAX_READ_PHOTOS_LIMIT = 64


def normal_max_read_photos(value) -> int | None:
    """intake_max_read_photos: None (unset: 32) when blank, else a whole
    number 1 to MAX_READ_PHOTOS_LIMIT. Raises ValueError otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        count = int(str(value).strip())
    except ValueError:
        raise ValueError(f"intake_max_read_photos {value!r}: a whole number, 1 to {MAX_READ_PHOTOS_LIMIT}") from None
    if isinstance(value, bool) or not 1 <= count <= MAX_READ_PHOTOS_LIMIT:
        raise ValueError(f"intake_max_read_photos {value!r}: 1 to {MAX_READ_PHOTOS_LIMIT}")
    return count


def normal_read_mode(value) -> str:
    """intake_read_mode written the one way; None or blank is "single".
    Raises ValueError on anything else, so a typo stops the app at start-up
    rather than quietly reading the old way."""
    mode = str(value or "").strip().lower() or "single"
    if mode not in READ_MODES:
        raise ValueError(f"read mode {value!r}: use one of {', '.join(READ_MODES)}")
    return mode


def normal_grid_marks(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
# (end of Contract 8 D2)


# Contract 9's parsers. Each raises ValueError, so a typo stops the app at
# start-up; the benchmark checks its flags with them.
def chain(text: str | None) -> list[str]:
    """A model chain setting as its model names, in order, once each."""
    return list(dict.fromkeys(_entries(text)))


def normal_chain(value, name: str = "the chain") -> str:
    """A model chain written the one way, "a,b". Blank is refused: every
    call on it would fail."""
    models = chain(str(value or ""))
    if not models:
        raise ValueError(f"{name}: at least one model name")
    return ",".join(models)


# The shared quota guard's lanes (ai/quota_guard.py); the first is the default.
QUOTA_LANES = ("prod", "staging")


def day_limits(text: str | None) -> dict[str, int]:
    """gemini_quota_day_limits as {model: requests a day}: "gemini-3.5-flash-
    lite=500,gemini-3.1-flash-lite=500". {} when blank. Raises ValueError on
    an entry that isn't model=N with N a whole number, 0 or more."""
    out: dict[str, int] = {}
    for entry in _entries(text):
        model, sep, number = entry.rpartition("=")
        model, number = model.strip(), number.strip()
        if not sep or not model or not number.isdigit():
            raise ValueError(f"gemini_quota_day_limits {entry!r}: write it as model=N, e.g. gemini-3.5-flash-lite=500")
        out[model] = int(number)
    return out


def _one_of(value, choices: tuple, name: str) -> str:
    """A choice setting written the one way; None or blank is the first."""
    choice = str(value or "").strip().lower() or choices[0]
    if choice not in choices:
        raise ValueError(f"{name} {value!r}: use one of {', '.join(choices)}")
    return choice


ZONE_ONLY = ("suggest", "list")
ZONE_EVIDENCE = ("off", "reason", "reason_box")
CHECK_MODES = ("off", "decision", "lite", "decision_then_lite")


def normal_zone_only(value) -> str:
    """intake_zone_only written the one way; None or blank is "suggest"."""
    return _one_of(value, ZONE_ONLY, "intake_zone_only")


def normal_zone_evidence(value) -> str:
    """intake_zone_evidence written the one way; None or blank is "off"."""
    return _one_of(value, ZONE_EVIDENCE, "intake_zone_evidence")


def normal_check_mode(value) -> str:
    """intake_check_mode written the one way; None or blank is "off"."""
    return _one_of(value, CHECK_MODES, "intake_check_mode")


def normal_check_resolution(value) -> str:
    """intake_check_resolution: one of IMAGE_RESOLUTIONS; blank is medium."""
    resolution = str(value or "").strip().lower() or "medium"
    if resolution not in IMAGE_RESOLUTIONS:
        raise ValueError(f"intake_check_resolution {value!r}: use one of {', '.join(IMAGE_RESOLUTIONS)}")
    return resolution
# (end of Contract 9)


# Contract 10's parsers, as Contract 9's: each raises ValueError, so a typo
# stops the app at start-up; the benchmark checks its flags with them.
LINKS_MODES = ("off", "shadow", "on")
# The kinds of link (link_table.json "type"), in the order they rank among
# a listed part's linked parts.
LINK_TYPES = ("attached", "opens_with", "behind", "crush")
LINKS_SIDES = ("firm", "strict")
# How hard a listed part was hit, lightest first (intake/links.hit_of).
HIT_LEVELS = ("light", "medium", "heavy", "crush")
LINKS_INTACT = ("demote", "drop", "ignore")
LINK_CHECK_MODES = ("off", "lite")


def normal_links_mode(value) -> str:
    """intake_links written the one way; None or blank is "off"."""
    return _one_of(value, LINKS_MODES, "intake_links")


def link_types(text: str | None) -> list[str]:
    """intake_links_types as its types, once each, in LINK_TYPES order (so
    "behind, attached" and "attached,behind" are one setting). Raises
    ValueError on a type the table doesn't have."""
    named = {entry.lower() for entry in _entries(text)}
    unknown = sorted(named - set(LINK_TYPES))
    if unknown:
        raise ValueError(f"intake_links_types {', '.join(unknown)}: use any of {', '.join(LINK_TYPES)}")
    return [kind for kind in LINK_TYPES if kind in named]


def normal_link_types(value) -> str:
    """intake_links_types written the one way, "attached,behind". Blank is
    refused: no link could ever show (intake_links "off" is the switch)."""
    kinds = link_types(str(value or ""))
    if not kinds:
        raise ValueError(f"intake_links_types: at least one of {', '.join(LINK_TYPES)}")
    return ",".join(kinds)


def normal_links_side(value) -> str:
    """intake_links_side written the one way; None or blank is "firm"."""
    return _one_of(value, LINKS_SIDES, "intake_links_side")


def normal_hit_level(value, name: str = "intake_links_unknown_hit") -> str:
    """A hit level, one of HIT_LEVELS; None or blank is "medium" (the level
    of a part whose words say nothing)."""
    level = str(value or "").strip().lower() or "medium"
    if level not in HIT_LEVELS:
        raise ValueError(f"{name} {value!r}: use one of {', '.join(HIT_LEVELS)}")
    return level


def normal_links_intact(value) -> str:
    """intake_links_intact written the one way; None or blank is "demote"."""
    return _one_of(value, LINKS_INTACT, "intake_links_intact")


def normal_link_check(value) -> str:
    """intake_link_check written the one way; None or blank is "off"."""
    return _one_of(value, LINK_CHECK_MODES, "intake_link_check")


def normal_count(value, name: str = "the count") -> int:
    """A count setting (intake_links_max, intake_link_check_max_calls): a
    whole number, 0 or more."""
    try:
        count = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} {value!r}: a whole number, 0 or more") from None
    if count < 0:
        raise ValueError(f"{name} {value!r}: a whole number, 0 or more")
    return count
# (end of Contract 10)


# The car map's switch (intake/car_map.py), parsed as Contract 10's are.
CAR_MAP_MODES = ("off", "shadow", "on")


def normal_car_map_mode(value) -> str:
    """intake_car_map written the one way; None or blank is "off"."""
    return _one_of(value, CAR_MAP_MODES, "intake_car_map")


CAR_MAP_NAMES = ("off", "on")


def normal_car_map_names(value) -> str:
    """intake_car_map_names written the one way; None or blank is "off"."""
    return _one_of(value, CAR_MAP_NAMES, "intake_car_map_names")


# The assembly walk's switches (intake/assembly_walk.py), parsed the same way.
WALK_MODES = ("off", "on")
WALK_RAISES = ("one", "full")
LINK_HOPS = (1, 2)


def normal_walk_mode(value) -> str:
    """intake_assembly_walk written the one way; None or blank is "off"."""
    return _one_of(value, WALK_MODES, "intake_assembly_walk")


def normal_walk_raise(value) -> str:
    """intake_walk_raise written the one way; None or blank is "one"."""
    return _one_of(value, WALK_RAISES, "intake_walk_raise")


def normal_link_hops(value) -> int:
    """intake_link_hops as 1 or 2; None or blank is 1."""
    text = "" if value is None else str(value).strip()
    if isinstance(value, bool) or text not in ("",) + tuple(str(h) for h in LINK_HOPS):
        raise ValueError(f"intake_link_hops {value!r}: use 1 or 2")
    return int(text or LINK_HOPS[0])

# Light-model detection's parsers (P-prompts.md), as contract 10's: each
# raises ValueError, so a typo stops the app at start-up; the benchmark
# checks its flags with them.
READ_PACKS = ("orient", "cues", "small")
ZONE_PACKS = ("orient", "cues", "small", "intact")
READ_PROMPTS = ("v1", "v2")
SORT_PROMPTS = ("v2", "v3")
LINK_SKILLS = ("L1", "L2")
LINK_PROMPTS = ("v1", "v2")
LINK_ORDERS = ("candidates_first", "photos_first")


def pack_list(text: str | None, choices: tuple = ZONE_PACKS, name: str = "the packs") -> list[str]:
    """A packs setting as its packs, once each, in `choices` order (so
    "small, orient" and "orient,small" are one setting). Raises ValueError
    on a pack it can't take."""
    named = {entry.lower() for entry in _entries(text)}
    unknown = sorted(named - set(choices))
    if unknown:
        raise ValueError(f"{name} {', '.join(unknown)}: use any of {', '.join(choices)}")
    return [pack for pack in choices if pack in named]


def normal_packs(value, choices: tuple, name: str) -> str:
    """A packs setting written the one way, "orient,small"; blank (None
    too) is none -- today's prompt."""
    return ",".join(pack_list(str(value or ""), choices, name))


def normal_link_skill(value) -> str:
    """intake_link_skill written the one way ("L1", "L2"); blank is L1."""
    skill = str(value or "").strip().upper() or LINK_SKILLS[0]
    if skill not in LINK_SKILLS:
        raise ValueError(f"intake_link_skill {value!r}: use one of {', '.join(LINK_SKILLS)}")
    return skill


def light_conflict(read_prompt: str, read_anchor: bool, link_skill: str, link_signs: bool) -> str | None:
    """Why two light-model settings can't go together, or None."""
    if read_anchor and read_prompt != "v2":
        return "intake_read_anchor is part of read prompt v2: set intake_read_prompt v2 too"
    if link_signs and link_skill != "L2":
        return "intake_link_signs uses the L2 skills' signs files: set intake_link_skill L2 too"
    return None
# (end of light-model detection)


# The car map's review screen (K4): the order inside a bucket, and where
# the strip-down bucket is counted.
LINKS_ORDERS = ("table", "prior")
STRIPDOWN_CAPS = ("in", "out")


def normal_links_order(value) -> str:
    """intake_links_order written the one way; None or blank is "table"."""
    return _one_of(value, LINKS_ORDERS, "intake_links_order")


def normal_stripdown_cap(value) -> str:
    """intake_links_stripdown_cap written the one way; None or blank is "in"."""
    return _one_of(value, STRIPDOWN_CAPS, "intake_links_stripdown_cap")


# Each model setting written the one way, None when blank (unset). Raise
# ValueError as the parsers above do. The benchmark checks its flags with
# them before it sets them on `settings` (an assignment isn't validated).
def normal_thinking_levels(value) -> str | None:
    if value is None:
        return None
    levels = thinking_levels(str(value))
    return ",".join(level if model == ANY else f"{model}={level}" for model, level in levels.items()) or None


def normal_image_resolutions(value) -> str | None:
    if value is None:
        return None
    return ",".join(f"{kind}={r}" for kind, r in image_resolutions(str(value)).items()) or None


def normal_read_tools(value) -> str | None:
    if value is None:
        return None
    table = read_tools_by_model(str(value))
    return ",".join(name if model == ANY else f"{model}={name}" for model, names in table.items() for name in names) or None


settings = Settings()
