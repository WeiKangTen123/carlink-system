"""SQLAlchemy models.

Phase 0 keeps this deliberately thin: one Report row with the validated
draft stored as JSON, rather than the fully normalized Reporter /
PersonInvolved / Witness / AuditLogEntry tables from the target data model
in docs/proposal.md section F. Normalize into those tables in Phase 1, once
there's a dashboard and multi-report querying that actually needs it.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    """ISO 8601 with an explicit UTC offset.

    SQLite hands DateTime columns back naive (no tzinfo) even though they're
    written in UTC, and a naive ISO string is read as LOCAL time by
    JavaScript: Node on the server (UTC) and a browser in Singapore (UTC+8)
    then disagreed by 8 hours -- every case time and SLA countdown shown in
    the browser was 8 hours off, and React reported hydration error #418.
    """
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


class AppSetting(Base):
    """Key/value store for the handful of settings a user can actually
    change at runtime.

    Deliberately separate from Settings in config.py: those are env-var,
    deploy-time configuration (API keys, model chain, storage paths) that
    a dashboard form has no business rewriting -- the Settings page shows
    them read-only and says so. This table is only for things that are
    genuinely user-owned, like the company name printed on PDFs.
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class ApiKey(Base):
    """Operator-supplied provider API keys (currently Gemini).

    Multiple keys are supported on purpose: every model in the fallback
    chain shares one key's quota, so once that key is exhausted across all
    models, drafting stops entirely. A second key is a whole fresh set of
    quota buckets -- this project hit free-tier limits repeatedly during
    development, so this is a real operational need, not a nicety.

    The key itself is never returned by the API -- only `last4` and
    whether one exists (see the /setup/llm-keys endpoints). It's stored
    as-is rather than encrypted because any decryption key would have to
    live on the same box, so encryption here would be theatre rather than
    protection; the real control is not exposing it over HTTP.
    """
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    provider: Mapped[str] = mapped_column(String, default="gemini")
    label: Mapped[str] = mapped_column(String, default="")
    key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    type: Mapped[str] = mapped_column(String, default="security_incident")
    status: Mapped[str] = mapped_column(String, default="confirmed")
    channel: Mapped[str] = mapped_column(String, default="telegram")
    reporter_chat_id: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
    photo_paths: Mapped[list] = mapped_column(JSON, default=list)
    pdf_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    # The account the case belongs to (users.id); None = Unassigned, which
    # only admins and "see all cases" staff see (app/auth/permissions.py).
    # Set to whoever files it on the dashboard; Telegram cases stay
    # Unassigned until an admin assigns them. (Added after the table
    # shipped: see db._ADDED_COLUMNS / _ADDED_INDEXES.)
    owner_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # What the AI last said about the case: the draft before any person's
    # edits (the wizard: IntakeSession.ai_draft at filing; the bots: the
    # draft confirmed in the chat). The case page tags the cover fields
    # whose value is still the AI's (GET /reports/{id} ai_read). Never
    # printed. (Added after the table shipped: see db._ADDED_COLUMNS.)
    ai_draft: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class IntakeSession(Base):
    """Progress of a dashboard intake that hasn't been turned into a case yet.

    The draft itself is an ordinary Report row with status "intake" -- so it
    shows in the Cases Repository's New Intake / Draft lane like any other
    case -- and this holds only what the intake wizard needs on top: where
    background AI processing has got to, and the reporter's own words, which
    the AI rewrites into data["description"] and would otherwise be lost.
    Deleted when the intake is finalized.
    """
    __tablename__ = "intake_sessions"

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    # uploading -> sorting -> reading -> ready, or failed (retryable);
    # "finalizing" while POST /finalize turns it into a case.
    stage: Mapped[str] = mapped_column(String, default="uploading")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Set when a run stopped because the person's AI isn't available (an
    # ai/keys.AiUnavailable code, e.g. "ai_key_invalid"), so the wizard can
    # offer the fix; cleared when the next run starts. (Added later: see
    # db._ADDED_COLUMNS.)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Who started the run that failed (a user id), so the wizard can word
    # the error for whoever is looking: "your AI key" or someone else's.
    # Cleared with error_code. (Added later: see db._ADDED_COLUMNS.)
    error_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reporter_notes: Mapped[str] = mapped_column(String, default="")
    # Which photos the reading step actually sent to the model, so the
    # review screen can say what the facts were read from.
    read_photo_ids: Mapped[list] = mapped_column(JSON, default=list)
    # The AI's last draft exactly as it came back, before merging into
    # Report.data. A re-read compares the form against it to tell which
    # fields the surveyor changed -- those are kept, not overwritten.
    # (Added after the table shipped: see db._ADDED_COLUMNS.)
    ai_draft: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # When the current stage began, so the wizard can show elapsed time and
    # an estimate of what's left. (Added later: see db._ADDED_COLUMNS.)
    stage_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Contract 10: the last beat of an AI run in another process (the
    # Telegram bot's, intake/pipeline.keep_beating), so the api tells a live
    # run there from one a restart cut off (pipeline.is_live). NULL while
    # no other process runs it -- always, with telegram_full_detection off.
    # (Added later: see db._ADDED_COLUMNS.)
    run_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class IntakePhoto(Base):
    """One uploaded photo of an intake draft.

    Its own row rather than an entry in Report.photo_paths because uploads
    arrive several at a time in parallel: appending to one shared JSON list
    from concurrent requests loses entries, while inserting rows doesn't.
    Moved into Report.photo_paths, in `position` order, on finalize.
    """
    __tablename__ = "intake_photos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    report_id: Mapped[str] = mapped_column(String, index=True)
    # Order chosen by the browser (files sorted by name, i.e. camera order).
    position: Mapped[int] = mapped_column(Integer, default=0)
    original_name: Mapped[str] = mapped_column(String, default="")
    path: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Set by AI sorting (ai/triage.py) or overridden by the surveyor.
    kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quality: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    shows_damage: Mapped[bool] = mapped_column(Boolean, default=False)
    # The one damaged part the photo shows best (a taxonomy.CANONICAL_PARTS
    # name), so the reading step can spread its photo budget across parts
    # instead of across the coarse areas -- on a rear-end case every damage
    # photo is "rear". (Added after the table shipped: see db._ADDED_COLUMNS.)
    part: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Where the sorter saw the damage, [ymin, xmin, ymax, xmax] on 0-1000
    # of the image it was sent (triage.clean_box), when it was asked to box
    # it (settings.intake_sort_damage_box, contract 8 D1); the reading's
    # close-ups are cut from it (intake/crops.py). JSON, so the four numbers
    # take one column and no migration beyond db._ADDED_COLUMNS. None: not
    # asked, no damage seen, or a box that made no sense.
    damage_box: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    kind_source: Mapped[str] = mapped_column(String, default="ai")
    # B5 (settings.intake_keep_sort_label "on"; plans/2026-11-22-enhance-
    # staging R-batching): the sorter's own kind, kept when a person first
    # regroups the photo (api/intake.update_intake_photo), for the
    # AI-accuracy record's regroup counts. None: never regrouped, regrouped
    # with the setting off, or the sorter never sorted it. (Added after the
    # table shipped: see db._ADDED_COLUMNS.)
    ai_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # E7 (settings.intake_side_geometry not "off"; plans/2026-11-22-enhance-
    # staging 3c): what the sort said of the photo's geometry -- {"view",
    # "angle", "wheel", "side" (intake/geometry.photo_side), "rhd_check"} --
    # for the side worked out in code. None: the sort wasn't asked. (Added
    # after the table shipped: see db._ADDED_COLUMNS.)
    geometry: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # When the photo was taken, by its EXIF, read from the original as sent
    # before settings.photo_store_compress drops the EXIF (owner decision
    # D7, 23 Nov 2026: keep the date taken, no location) --
    # storage/files.photo_taken_at. Website uploads and staff Telegram
    # drafts. For a later "Date of survey" suggestion a person checks,
    # never filled in silently. Two columns, so neither means two things:
    # taken_at_local: the camera's clock, naive, in the zone the phone was
    # set to (UTC+8 in Malaysia) -- NOT UTC, so never through iso_utc. Its
    # date is the day the photo was taken there.
    # taken_at: the same moment in UTC (naive, like every DateTime column
    # here), only when the camera wrote its offset (OffsetTimeOriginal...);
    # None otherwise, rather than a guessed zone.
    # Both None: no usable EXIF date. (Added after the table shipped: see
    # db._ADDED_COLUMNS.)
    taken_at_local: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Kept in the case file but never sent to the AI.
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class PublicSubmission(Base):
    """Who sent a public report through the Telegram bot, and what they gave
    (P6, app/intake/public.py, which makes every change to it).

    The report itself is an ordinary intake draft (Report channel
    "telegram_public", status "intake", IntakeSession, IntakePhoto rows).
    Its state is never stored here: new = no owner, taken = an owner, filed
    = any status but "intake" -- one source of truth, settled by the
    conditional UPDATE on reports. taken_*, filed_at and
    withdraw_requested_at are facts kept for the inbox.

    The submitter_* fields are the unchanging record of what the person
    sent; Report.data holds the copy staff can edit. telegram_* is cleared
    when the case is filed. Deleted with the draft (withdraw, discard,
    expiry); kept with a filed case. The unique rule and the indexes are
    part of the table itself (create_all makes it in one statement; the
    indexes are also in db._ADDED_INDEXES).
    """
    __tablename__ = "public_submissions"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_public_submissions_reference"),
        # The 30-day sweep (public.sweep_untaken).
        Index("ix_public_submissions_expires_at", "expires_at"),
        # The caps per sender and per phone (public.check_can_accept); the
        # first also serves lookups by telegram_user_id alone (/withdraw).
        Index("ix_public_submissions_telegram_user_id_submitted_at", "telegram_user_id", "submitted_at"),
        Index("ix_public_submissions_submitter_phone_submitted_at", "submitter_phone", "submitted_at"),
    )

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    # TG-YYMMDD-XXXX (public.new_reference): what the person is told.
    reference: Mapped[str] = mapped_column(String, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # submitted_at + 30 days, as promised at consent: untaken reports are
    # deleted then. Stored, not worked out, so the promise doesn't move.
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consent_version: Mapped[str] = mapped_column(String, nullable=False)
    submitter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # driver | owner | workshop | other (public.ROLES).
    submitter_role: Mapped[str] = mapped_column(String, nullable=False)
    # E.164, from a contact the person shared from their own Telegram
    # (typed numbers are refused, so every phone here is Telegram-verified).
    submitter_phone: Mapped[str] = mapped_column(String, nullable=False)
    # sg_mobile | sg_other | foreign (public.phone_kind).
    phone_kind: Mapped[str] = mapped_column(String, nullable=False)
    # None: "Not sure".
    plate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    plate_looks_sg: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Their text, or "Location pin: lat, lon". At most 300 characters.
    when_where: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Their own words, verbatim; at most 2000 characters.
    statement: Mapped[str] = mapped_column(String(2000), nullable=False)
    photo_count_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forwarded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The sender's Telegram user id (as text), @username and name; all
    # three cleared when the case is filed (public.on_finalize).
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    telegram_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    taken_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    filed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # /withdraw on a report staff had already taken: noted for them.
    withdraw_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PublicBlock(Base):
    """A Telegram account the public bot takes no reports from, set when a
    report is discarded as spam (public.block_sender), for BLOCK_FOR (90
    days); swept after. The plain Telegram user id: a hash of a numeric id
    can be reversed, so it is stored honestly and the privacy notice says
    so."""
    __tablename__ = "public_blocks"
    __table_args__ = (
        Index("ix_public_blocks_until", "until"),
    )

    telegram_user_id: Mapped[str] = mapped_column(String, primary_key=True)
    blocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # The account that discarded the report (users.id).
    blocked_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The report's TG- reference, for the history.
    reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class User(Base):
    """A person who can sign in to the dashboard (app/auth).

    The first admin is created on the server with
    `python -m app.auth.manage create-user`. Everyone else signs up on the
    public /signup page (username, email, password) and is in at once as a
    "user", or joins through a one-time invite link an admin creates
    (app/api/signup.py). Only an admin makes someone an admin. Users are
    disabled rather than deleted, so the case history keeps pointing at real
    accounts.

    The surveyor profile (qualifications, licence, firm) is what sign-off
    prints -- it used to be typed in, or defaulted to one fixed surveyor, so
    anyone could sign a case as anyone.
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    # Always stored lower-cased (app/auth/core.normalize_email).
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    # Lower-cased, 3-30 characters (app/auth/core.username_problem); signs
    # in like the email. Unique through ux_users_username (reports/db.py),
    # which also covers databases from before this column existed.
    # (Added after the table shipped: see db._ADDED_COLUMNS.)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The full name printed on sign-offs; starts out as the username.
    name: Mapped[str] = mapped_column(String)
    # "admin": users, API keys, settings, deleting cases. "user": cases.
    # (Called "surveyor" until 2026-10-25; db._DATA_FIXES renames it.)
    role: Mapped[str] = mapped_column(String, default="user")
    password_hash: Mapped[str] = mapped_column(String)
    # Set when an admin hands out a temporary password; the API refuses
    # everything but changing it until it is changed.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    qualifications: Mapped[str] = mapped_column(String, default="")
    license_number: Mapped[str] = mapped_column(String, default="")
    firm_name: Mapped[str] = mapped_column(String, default="")
    # Ticks an admin sets on a "user" (admins have all of it anyway); None
    # means no. "Can see and work on all cases", including Unassigned ones;
    # "May use the company AI key"; "May sign off reports" under the
    # company's name. Accounts from before these existed that an admin made
    # or invited got all three (reports/owners.grandfather_staff_ticks);
    # self-signups start without. (Added later: see db._ADDED_COLUMNS.)
    sees_all_cases: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    ai_company_key_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    may_sign_off: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    # Wrong passwords in a row; reaching the limit sets locked_until.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class UserAiKey(Base):
    """Someone's own AI (Gemini) key, added on Settings > My AI key
    (app/ai/keys.py). One per person, and one person per key.

    Its own table, not rows in api_keys: the AI takes every gemini row
    there as a company key. The key itself is only ever stored sealed
    (key_enc, app/ai/secretbox.py) and never leaves the server again;
    people, admins included, see last4 and status. fingerprint is an HMAC
    of the key under the same secret -- not a plain hash, so a stolen
    backup can't be used to test guessed keys against it.

    The unique rules are part of the table itself (create_all makes it in
    one statement), so no process stopping half-way can leave one out.
    """
    __tablename__ = "user_ai_keys"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_ai_keys_user_id"),
        UniqueConstraint("fingerprint", name="uq_user_ai_keys_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False, default="gemini")
    key_enc: Mapped[str] = mapped_column(String, nullable=False)
    last4: Mapped[str] = mapped_column(String, nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # untested | ok | invalid | quota | unreadable (keys.STATUSES).
    status: Mapped[str] = mapped_column(String, nullable=False, default="untested")
    # Google's last complaint, at most 200 characters, with anything that
    # looks like a key taken out. Shown only to the key's owner.
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Billed (True) or Free (False), as the person said when saving it or
    # since (PATCH /auth/me/ai-key). On free keys Google may use what's
    # sent, case photos too, so only a Billed key reads public reports.
    billing_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # When this key was added ("added_at"); a replacement key starts again.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChatLink(Base):
    """A chat account connected to a Carlink account (app/auth/chat_links.py):
    today a Telegram user, so the bot knows who is filing.

    external_id is the Telegram USER id (as text), never a chat id: it is
    the same for that person in every chat, for life. The username and name
    are only for showing; people change them. One Telegram per account and
    one account per Telegram: both unique rules are part of the table itself
    (create_all makes it in one statement), so no process stopping half-way
    can leave one out. Deleted on disconnect, on disabling the account and
    on any password change or reset; the history keeps the record.
    """
    __tablename__ = "chat_links"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_chat_links_channel_external_id"),
        UniqueConstraint("channel", "user_id", name="uq_chat_links_channel_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="telegram")
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    external_username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    # When the bot last saw this person; written at most every 10 minutes.
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ChatLinkCode(Base):
    """A one-time code someone made on Settings > My Telegram to connect
    their Telegram (app/auth/chat_links.py). Only its SHA-256 is stored,
    like sessions and account links. Works once, for 10 minutes; a new one
    cancels the last.

    Both sides confirm: Telegram taps Yes (used_by_* and waiting_since are
    set: "waiting for Carlink"), then the account confirms in Settings
    (linked_at), or says "Not me" (revoked_at). Swept a day after it expires
    (app/intake/cleanup.py).
    """
    __tablename__ = "chat_link_codes"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_chat_link_codes_code_hash"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="telegram")
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # The Telegram user who tapped [Yes, that's me] on it.
    used_by_external_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    used_by_username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    used_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    waiting_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    linked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    """One signed-in browser. The cookie holds a random token; only its
    SHA-256 is stored, so a copy of the database (or a backup) can't be used
    to sign in as anyone."""
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    # Hard limit regardless of activity; the idle limit is checked against
    # last_seen_at (see app/auth/core.py).
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str] = mapped_column(String, default="")
    user_agent: Mapped[str] = mapped_column(String, default="")


class AuditEntry(Base):
    """Who did what to a case (or to a user account), and when.

    user_name is copied at the time, so the history still reads right after
    a user is renamed or disabled. report_id is kept after a case is deleted
    -- the record that it was deleted, and by whom, is the point.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_name: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class AccountLink(Base):
    """A one-time link an admin sends someone (by WhatsApp -- there is no
    email service): "invite" creates an account, "reset" sets a new
    password on an existing one.

    Only the SHA-256 of the token is stored, like sessions. A link works
    once, expires, can be revoked, and is tied to one email; the role comes
    from this row, never from the person using the link.
    """
    __tablename__ = "account_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=gen_id)
    purpose: Mapped[str] = mapped_column(String, default="invite")  # invite | reset
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, default="user")
    # The name the admin typed, for the greeting on the invite page.
    name: Mapped[str] = mapped_column(String, default="")
    # The account a reset link is for.
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String)
    created_by_name: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    used_by_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AiRun(Base):
    """One piece of AI work on a case or its draft (round 3, B3): a sorting
    or reading stage of the intake wizard, a Telegram (re)draft, Annex A
    suggestions, and later re-detection and price lookups. Written by
    app/ai/runs.py, which every Gemini-calling path goes through; read by
    GET /reports/{id} (ai_runs, ai_last_read) and GET /ai-usage.

    Before this the only trace of an AI call was a log line without a case
    id: nobody could see which photos were read, how long it took, which
    key paid or why it failed. A draft and the case it becomes share one
    id, so the rows need no copying at filing and outlive the
    IntakeSession. Never holds a key (only its last 4 characters) or
    anything a member of the public sent: error_text is scrubbed of digits,
    plates and quoted model output (runs.scrub_error). New table, made by
    create_all: nothing in db._ADDED_COLUMNS.
    """
    __tablename__ = "ai_runs"
    __table_args__ = (
        # GET /ai-usage reads a window of days. Also in db._ADDED_INDEXES.
        Index("ix_ai_runs_started_at", "started_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    # The draft's or the case's id (the same id). A bot draft gets the id
    # its case will be saved under (channels/telegram._case_id_for).
    report_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # sort | read | redraft | annex_suggest | redetect | lookup | second_look (runs.KINDS).
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # Where it was started: dashboard | telegram | whatsapp | system.
    channel: Mapped[str] = mapped_column(String, nullable=False, default="dashboard")
    # Who pressed the button (users.id); None for the system's own runs.
    actor_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # own | company | none: the key that answered, else the last one tried.
    key_source: Mapped[str] = mapped_column(String, nullable=False, default="none")
    key_last4: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # running | ok | failed | refused | aborted (runs.STATUSES).
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # One entry per model try: {model, key_source, key_last4, outcome
    # (ok | 429 | timeout | error | schema), waited_s, ms[, prompt_tokens,
    # output_tokens]}.
    attempts: Mapped[list] = mapped_column(JSON, default=list)
    model_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The P-labels sent ("P03"), or {"count": n} for a sorting stage; None
    # when no photo went (Annex A suggestions).
    photos_sent: Mapped[Optional[object]] = mapped_column(JSON, nullable=True)
    input_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Only when the SDK reports them; None otherwise, never 0.
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Damaged parts the run came back with (reading, drafting).
    parts_found: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The knowledge stamp (L-library D.3, step 2B): the versions of what the
    # run worked with -- {library, links ("L1:06bbebfa", the link table's
    # version and the first 8 of its sha256), car_map, prompt, models} --
    # written when the run starts, its models when it ends
    # (app/reports/knowledge.py). None on rows from before. (Added after the
    # table shipped: see db._ADDED_COLUMNS.)
    knowledge: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class PastAnnexLine(Base):
    """One Annex A line from a past report spreadsheet (app/parts/history.py)
    -- the firm's own record of what was replaced with what, and what it
    was adjusted to. Re-importing a report replaces its lines."""
    __tablename__ = "past_annex_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_ref: Mapped[str] = mapped_column(String, index=True)  # "CL 11900"
    source_file: Mapped[str] = mapped_column(String, default="")
    # Always "": the price book keeps no plate (owner K0, 24 Nov 2026; the
    # CL reference is enough). Nothing writes or reads it; the start-up
    # step price_book_plates_v1 blanked the ones imported before
    # (app/parts/prices.drop_price_book_plates). Kept as a column only
    # because SQLite tables here are never rebuilt.
    plate: Mapped[str] = mapped_column(String, default="")
    make: Mapped[str] = mapped_column(String, default="", index=True)
    model: Mapped[str] = mapped_column(String, default="", index=True)
    make_model_raw: Mapped[str] = mapped_column(String, default="")
    registered: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO date
    surveyed: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO date
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String, default="list")  # list | nett
    item: Mapped[str] = mapped_column(String)
    # Wording-level key ("tail lamp" for "Tail lamp LH") and the canonical
    # part family the line belongs to ("Tail Lamp"), or None.
    item_key: Mapped[str] = mapped_column(String, index=True)
    family: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    condition: Mapped[str] = mapped_column(String, default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    # The quantity as the sheet had it when it wasn't a plain number
    # ("1set"), so a prefilled row can print it the same way; None for
    # "2". (Added after the table shipped: see db._ADDED_COLUMNS.)
    qty_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    workshop_est: Mapped[float] = mapped_column(Float, default=0.0)
    discount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adjusted_cost: Mapped[float] = mapped_column(Float, default=0.0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    # Stage 1 (owner, 24 Nov 2026): where the line came from -- "import" (a
    # firm's spreadsheet) or "carlink" (a case signed off here, app/parts/
    # learn.py) -- and whether a person confirmed its price. Only a
    # confirmed price prices a new case (history.price_usable, DB4); an
    # imported report's prices are the firm's own, so confirmed. None on
    # rows from before: the start-up step price_book_origin_v1 sets both
    # (app/parts/prices.mark_past_line_origins). (Added after the table
    # shipped: see db._ADDED_COLUMNS.)
    origin: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confirmed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)


class PastLabourLine(Base):
    """One Annex B (labour and spray painting) line from a past report
    spreadsheet (app/parts/history.py): the wording the surveyor used and
    what the workshop's figure was adjusted to, so a new case can pick the
    firm's usual labour lines with their usual prices. Imported and replaced
    together with the report's PastAnnexLine rows."""
    __tablename__ = "past_labour_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_ref: Mapped[str] = mapped_column(String, index=True)  # "CL 11900"
    source_file: Mapped[str] = mapped_column(String, default="")
    # Always "", as PastAnnexLine.plate.
    plate: Mapped[str] = mapped_column(String, default="")
    make: Mapped[str] = mapped_column(String, default="", index=True)
    model: Mapped[str] = mapped_column(String, default="", index=True)
    surveyed: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # ISO date
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String)
    # Wording-level key ("remove, reinstall rear windscreen" for "To remove,
    # reinstall rear windscreen (to FR)"), history.normalise_labour.
    desc_key: Mapped[str] = mapped_column(String, index=True)
    workshop_est: Mapped[float] = mapped_column(Float, default=0.0)
    adjusted_cost: Mapped[float] = mapped_column(Float, default=0.0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


# ------------------------------------------- "My prices" and the book (Stage 1)
# Owner decisions P1-P5, DB1-DB4 (24 Nov 2026; plans/2026-11-23-report-and-
# parts/S1-contract.md). A price a person types on a case's Annex A is
# remembered for that person only (UserPrice) and fills their next case of
# that make and model first. The shared book changes only when an admin
# decides on the "Price differences" list (BookPrice). Logic in
# app/parts/prices.py. New tables, made by create_all; the unique rule of
# user_prices is an index of its own (db._ADDED_INDEXES: side may be NULL).


class UserPrice(Base):
    """One person's own unit price for one Annex A line of one make and
    model: what they last typed as W/shop Est. / qty (DB2). Keyed as the
    price book keys its lines: make as history.split_make_model writes it
    ("Honda"), model_key the model's normalised first word with twins
    merged (history.model_key: "HR-V" -> "vezel"), item_key / family /
    side as past_annex_lines'. Only ever shown to its own person."""
    __tablename__ = "user_prices"
    __table_args__ = (Index("ix_user_prices_user_id_make_model_key", "user_id", "make", "model_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    make: Mapped[str] = mapped_column(String, nullable=False)
    model_key: Mapped[str] = mapped_column(String, nullable=False)
    item_key: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The printed description as last used ("Rear bumper fascia").
    wording: Mapped[str] = mapped_column(String, default="")
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    # The unit price before the last change, for Undo; None when the last
    # save made the row (Undo then removes it).
    previous_unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_case_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # How many Annex A saves have set it.
    uses: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class BookPrice(Base):
    """An admin's decision on one line of the book (POST /admin/price-
    differences/decide): "update" sets the book's unit price for make +
    model_key + item_key to unit_price (step 2 of the price choice,
    prices.choose_prices); "keep" leaves the book as it is and takes the
    line off the list until someone's price changes again. A newer
    "update" makes every older decision on the key inactive; a newer
    "keep" only the older "keep"s (keeping the book's price keeps an
    approved one too). users_count / cases_count: how many people and
    cases were behind the users' prices when decided -- never who."""
    __tablename__ = "book_prices"
    __table_args__ = (Index("ix_book_prices_key", "make", "model_key", "item_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    make: Mapped[str] = mapped_column(String, nullable=False)
    model_key: Mapped[str] = mapped_column(String, nullable=False)
    item_key: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wording: Mapped[str] = mapped_column(String, default="")
    decision: Mapped[str] = mapped_column(String, nullable=False)  # update | keep
    unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # The book's reference unit price when decided (approved, else the
    # median of 2+ confirmed cases); None when the book had none.
    book_price_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    users_count: Mapped[int] = mapped_column(Integer, default=0)
    cases_count: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    reason: Mapped[str] = mapped_column(String, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


# ------------------------------------------------- parts-catalogue look-up
# A person asks for a case's damaged parts to be looked up in the online
# parts catalogues (app/parts/catalogue.py, app/api/catalogue.py). The
# server never contacts a parts site itself: the owner's PC runs a worker
# (tools/parts_mcp) that claims the job, looks the parts up and sends the
# rows back. Everything here is a reference for the surveyor: nothing is
# printed, nothing fills an estimate, nothing becomes an Annex A row by
# itself. New tables, made by create_all: nothing in db._ADDED_COLUMNS.


class CatalogueJob(Base):
    """One look-up of one case's damaged parts, from the click on [Look up
    in catalogue] to the worker's result.

    pending -> running (a worker claimed it) -> done | failed, or cancelled
    by a person at any point before it finishes. A running job nobody has
    heard from for 45 minutes goes back to pending (the PC went to sleep).
    At most one pending or running job per case: the partial unique index
    below refuses a second, so a double click can't queue two.

    `payload` is the job exactly as the worker gets it, built when the
    person asked (catalogue.job_payload): make, model, year and the damaged
    parts' names. Never the plate, VIN, chassis or engine number, the case
    id or anyone's name -- the worker knows the job by its own id only.
    """
    __tablename__ = "catalogue_jobs"
    __table_args__ = (
        # One open job per case. Also in db._ADDED_INDEXES.
        Index(
            "ux_catalogue_jobs_open", "report_id", unique=True,
            sqlite_where=text("status IN ('pending', 'running')"),
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        # The worker's claim: the oldest pending job. Also in db._ADDED_INDEXES.
        Index("ix_catalogue_jobs_status_created_at", "status", "created_at"),
        # Ids never come back: a deleted case takes its jobs, and a worker
        # still holding one's id must not report into a newer job.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="lookup")
    # pending | running | done | failed | cancelled (catalogue.JOB_STATUSES).
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # The catalogue car the surveyor picked with "Change car"; None lets
    # the worker find one from make, model and year.
    vehicle_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Who pressed the button: their id, and their name as the history shows
    # it, copied at the time.
    requested_by_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_by: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # The worker's name ("weika-pc").
    claimed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The last claim or progress report: 45 minutes without one and a
    # running job goes back to pending.
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # A failed job's reason (catalogue.ERROR_CODES) and the worker's words.
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # From the worker's result: the catalogue car it used, the other cars it
    # could have been, what went wrong where, and per part the query and
    # side with whether any diagram was found (the rows themselves are
    # CatalogueSuggestion rows).
    vehicle: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    candidates: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    failures: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    parts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class CatalogueSuggestion(Base):
    """One catalogue row (a part number on a diagram) suggested for one
    damaged part of a case.

    "suggested" until a person rejects it ("rejected"; undo puts it back)
    or accepts it into Annex A ("accepted", Phase 2: PUT /annex-a carrying
    catalogue_ref.suggestion_id). A new finished look-up replaces the
    case's "suggested" rows; accepted and rejected ones stay, and the new
    result doesn't add a row with the same part and part number again
    (catalogue.store_result).
    """
    __tablename__ = "catalogue_suggestions"
    # Ids never come back (SQLite would reuse the highest deleted one): a
    # re-run deletes the "suggested" rows, and a page still showing one
    # must not reject a newer row by its old id.
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # The damaged part it was found for, as the job named it (the
    # damage_summary part), and the job's query and side for that part.
    source_part: Mapped[str] = mapped_column(String, nullable=False)
    part_query: Mapped[str] = mapped_column(String, nullable=False, default="")
    part_side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The diagram it is on, and where: diagram_rank is the diagram's place
    # among the part's (best first), row_rank the row's place on it.
    diagram_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    diagram_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    diagram_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diagram_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The row as the catalogue gave it (contract: Result.parts[].diagrams[].rows[]).
    callout: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    part_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    superseded_from: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name_original: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    qty: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The row's own side ("left" / "right"), when the catalogue says.
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The site's own price: a reference only, in its currency, never the
    # workshop's estimate.
    ref_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ref_currency: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ref_tax_note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # When the worker read it (ISO 8601, as sent), and whether that was
    # from its cache past its freshness.
    fetched_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # suggested | accepted | rejected (catalogue.SUGGESTION_STATUSES).
    status: Mapped[str] = mapped_column(String, nullable=False, default="suggested")
    decided_by_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)


class CatalogueWorker(Base):
    """A PC worker the server has heard from: every claim and progress
    report marks it seen, so the case page can say whether a look-up will
    start soon ("online" = seen in the last 2 minutes)."""
    __tablename__ = "catalogue_workers"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utc_now)
    version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
class AiAccuracy(Base):
    """How much of the AI's draft a signed-off case kept (contract 8 L,
    2026-11-12): what the surveyor kept, removed, added or changed before
    signing, worked out by app/reports/accuracy.py at sign-off. On a real
    case there is no answer sheet; the signed-off report is the answer.
    Read by GET /admin/ai-accuracy (Settings > AI accuracy).

    One row per case: a re-sign-off replaces it, and it goes when the case
    is reopened or deleted. models: the models that read the case, from
    the run log (app/ai/runs.py). metrics: accuracy.compare()'s answer --
    counts and canonical part names and Annex A wordings only, never a
    value of the case (no plate, name or figure). version: which metric
    definitions made it (accuracy.VERSION). New table, made by create_all:
    nothing in db._ADDED_COLUMNS.
    """
    __tablename__ = "ai_accuracy"

    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    signed_off_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    models: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class PartObservation(Base):
    """One part decision on a signed-off case, de-identified (step 2B,
    K-pricing B.4 (1) CAPTURE; owner-approved 24 Nov 2026): every part on
    the signed-off damage list, every part the AI listed that people took
    off, and every suggestion people saw -- with the car, where it was hit,
    how the part got there and what became of it. The raw material the
    knowledge loop learns from later (K3); nothing reads it back into a case
    yet. Written by app/reports/observations.py in the sign-off's background
    step; a re-sign-off replaces the case's rows, a reopen or a delete
    removes them, and none is kept past 7 years (observations.sweep_old).

    NEVER holds a plate, VIN, chassis or engine number, anyone's name, a
    note or remark, a photo or a price: a typed "Other" name is scrubbed of
    the case's identifiers (catalogue._secrets) and of plate- and number-
    shaped words before it is stored. The case id (report_id) leads back to
    the case, as the AI accuracy row's does; signed_by is an account id.
    New table, made by create_all: nothing in db._ADDED_COLUMNS."""
    __tablename__ = "part_observations"
    __table_args__ = (
        # Also in db._ADDED_INDEXES (create_all adds each index on its own).
        Index("ix_part_observations_make_model_key", "make", "model_key"),
        Index("ix_part_observations_part", "part"),
        Index("ix_part_observations_report_id", "report_id"),
        Index("ix_part_observations_signed_off_at", "signed_off_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String, nullable=False)
    # When it was signed off (sign_off.signature_date, naive UTC) and by
    # which account (users.id, never a name).
    signed_off_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    signed_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The vehicle: make as the price book splits it ("Honda"), model_key
    # its normalised first word with twins merged ("vezel"; history.
    # model_key), body type (taxonomy.BODY_TYPES), year band from the
    # registration date ("2015-19"). vehicle_confirmed: make and model
    # both confirmed or entered by a person (the Review step's "you").
    make: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    body_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    year_band: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    vehicle_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Where the car was hit, from where the signed-off damaged parts are:
    # impact_end front | rear | both | side | other; impact_side left |
    # right | both | centre. impact_confirmed: a person confirmed the point
    # of impact (the field's text itself is not kept).
    impact_end: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    impact_side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    impact_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The case had an AI draft to compare with (Report.ai_draft): without
    # one every part is a person's, and "added" says nothing about the AI.
    ai_drafted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The part: a canonical or car map name, or "Other / Not Listed" with
    # other_name (normalised, scrubbed); family and side as history.
    # family_and_side gives them; area front | rear | side | roof |
    # underbody | cabin | wheel. kind: "part" (fittings are inside a part's
    # row, below). component_key: the parts library's key, filled by 3B.
    part: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    side: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    other_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="part")
    component_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The firm's condition words, the hit level (links.hit_of) and Repair /
    # Replace -- known words only, never free text.
    condition_words: Mapped[list] = mapped_column(JSON, default=list)
    hit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The Annex A lines renewed with it (source_part this part, not its own
    # main row): [{wording (normalised key), qty, section list | nett,
    # necessary}] -- never a price.
    fittings: Mapped[list] = mapped_column(JSON, default=list)
    # How it got there: ai_read | zone | suggestion | link | catalogue |
    # hand_added; the suggestion's source (zone | kit | nearby | link |
    # other_side), the link rule ("L1:rear-bumper>beam") and the reads that
    # found it ("overall", "zone:rear").
    how: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    link_rule: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    found_by: Mapped[list] = mapped_column(JSON, default=list)
    # kept | removed | added | dismissed | left; prompted: added because it
    # was suggested (the echo guard counts these apart).
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    prompted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The case's knowledge stamp at sign-off (data.knowledge).
    knowledge: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Which definitions made the row (observations.VERSION).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
