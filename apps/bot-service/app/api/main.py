import asyncio
import shutil
import uuid
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ai.extraction import draft_report
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings
from app.reports.db import SessionLocal, init_db
from app.ai.client import available_api_keys, get_client, reset_client_cache
from app.reports.models import ApiKey, AppSetting, Report
from app.reports.schema import SecurityIncidentDraft
from app.rendering.renderer import render_pdf
from app.storage.files import report_pdf_path, save_photo, tmp_dir, to_public_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Carlink Bot Service API", lifespan=lifespan)
app.include_router(whatsapp_router)
app.mount("/files", StaticFiles(directory=settings.storage_dir), name="files")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/reports")
def list_reports() -> list[dict]:
    db = SessionLocal()
    try:
        reports = db.query(Report).order_by(Report.created_at.desc()).all()
        result = []
        for r in reports:
            data = r.data or {}
            # Same make+model-else-vehicle_details fallback StudioApp.tsx
            # already uses for its own header -- added because the
            # dashboard's list views only had `category` to show per case,
            # and every real report here has the same category ("Vehicle
            # Collision or Damage"), making every row look identical.
            vehicle_info = data.get("vehicle_info") or {}
            vehicle_name = " ".join(filter(None, [vehicle_info.get("make"), vehicle_info.get("model")])) or data.get("vehicle_details")
            # Same damage_summary-else-damaged_parts fallback used in the
            # dashboard and get_analytics_summary -- lets the Overview
            # page's priority queue rank and label cases without having to
            # fetch every full report just to read two fields.
            damage_items = data.get("damage_summary") or data.get("damaged_parts") or []
            result.append({
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "channel": r.channel,
                "created_at": r.created_at.isoformat(),
                "location": data.get("location"),
                "category": data.get("category"),
                "thumbnail_url": to_public_url(r.photo_paths[0]) if r.photo_paths else None,
                "plate_number": vehicle_info.get("plate_number"),
                "vehicle_name": vehicle_name,
                "severity_level": data.get("severity_level"),
                "damage_count": len(damage_items),
            })
        return result
    finally:
        db.close()


@app.get("/reports/{report_id}")
def get_report(report_id: str) -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            return {"error": "not found"}

        # If PDF is missing or doesn't exist on disk, render it on-demand
        pdf_path = r.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            pdf_path = report_pdf_path(r.id)
            render_pdf(r.data or {}, r.photo_paths or [], pdf_path, report_id=r.id)
            r.pdf_path = pdf_path
            db.commit()

        photo_urls = [to_public_url(p) for p in (r.photo_paths or []) if Path(p).exists()]

        return {
            "id": r.id,
            "type": r.type,
            "status": r.status,
            "channel": r.channel,
            "data": r.data,
            "photo_urls": photo_urls,
            "pdf_url": to_public_url(r.pdf_path) if r.pdf_path and Path(r.pdf_path).exists() else None,
            "created_at": r.created_at.isoformat(),
        }
    finally:
        db.close()


@app.get("/reports/{report_id}/download")
def download_report_pdf(report_id: str):
    from fastapi.responses import FileResponse
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")

        pdf_path = r.pdf_path
        if not pdf_path or not Path(pdf_path).exists():
            pdf_path = report_pdf_path(r.id)
            render_pdf(r.data or {}, r.photo_paths or [], pdf_path, report_id=r.id)
            r.pdf_path = pdf_path
            db.commit()

        filename = f"Car_Incident_Report_CIR-2026-{r.id[:4].upper()}.pdf"
        return FileResponse(
            path=r.pdf_path,
            filename=filename,
            media_type="application/pdf",
        )
    finally:
        db.close()



@app.post("/reports/analyze-photos")
async def analyze_report_photos(
    description: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
) -> dict:
    """Dashboard equivalent of the Telegram bot's photo+text drafting step:
    saves the uploaded photos to a temp dir, runs the same Gemini drafting
    call used by the bot, and returns the draft for the reporter to review
    and edit -- nothing is saved to the database yet. The returned
    temp_photo_paths get passed back in on POST /reports if the reporter
    goes on to save the report, so the photos are only ever attached to a
    report the reporter actually confirmed.
    """
    saved_paths: list[str] = []
    td = tmp_dir()
    for photo in photos:
        ext = Path(photo.filename or "").suffix or ".jpg"
        dest = td / f"{uuid.uuid4().hex}{ext}"
        with open(dest, "wb") as f:
            shutil.copyfileobj(photo.file, f)
        saved_paths.append(str(dest))

    try:
        # draft_report() makes a blocking Gemini network call (up to 45s per
        # model, x5 fallback models worst case). Called directly, it runs ON
        # this FastAPI process's event loop -- freezing the entire dashboard
        # (every /reports request, every user) for the full duration of one
        # person's photo analysis. render_pdf() elsewhere in this service
        # already gets this right via asyncio.to_thread; this call was
        # missed, along with the same call in both channel adapters.
        draft = await asyncio.to_thread(draft_report, description, saved_paths)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI drafting failed: {exc}") from exc

    return {"draft": draft.model_dump(), "temp_photo_paths": saved_paths}


class CreateReportRequest(BaseModel):
    draft: SecurityIncidentDraft
    temp_photo_paths: list[str] = []


@app.post("/reports")
def create_report(body: CreateReportRequest) -> dict:
    """Manual entry from the dashboard -- optionally with photos that were
    already analyzed via POST /reports/analyze-photos and are sitting in the
    temp dir, which get moved into the report's permanent storage here once
    the reporter actually confirms and saves.
    """
    db = SessionLocal()
    try:
        report = Report(
            channel="manual",
            reporter_chat_id="dashboard",
            data=body.draft.model_dump(),
            status="confirmed",
        )
        db.add(report)
        db.flush()

        photo_paths = [
            save_photo(report.id, temp_path, i)
            for i, temp_path in enumerate(body.temp_photo_paths)
            if Path(temp_path).exists()
        ]
        report.photo_paths = photo_paths
        for temp_path in body.temp_photo_paths:
            Path(temp_path).unlink(missing_ok=True)

        pdf_path = report_pdf_path(report.id)
        render_pdf(report.data, photo_paths, pdf_path, report_id=report.id)
        report.pdf_path = pdf_path

        db.commit()
        return {"id": report.id}
    finally:
        db.close()


@app.put("/reports/{report_id}")
def update_report(report_id: str, body: CreateReportRequest) -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        if r.status == "Signed Off":
            raise HTTPException(
                status_code=409,
                detail="Report is signed off and locked. Reopen it before editing.",
            )

        r.data = body.draft.model_dump()

        # New photos from this edit get appended after whatever's already
        # attached -- indexed starting from the current count, never from 0,
        # so a report that already has photo_00.jpg/photo_01.jpg doesn't get
        # a freshly-added photo overwriting one of them.
        existing_photos = r.photo_paths or []
        new_photos = [
            save_photo(r.id, temp_path, len(existing_photos) + i)
            for i, temp_path in enumerate(body.temp_photo_paths)
            if Path(temp_path).exists()
        ]
        if new_photos:
            r.photo_paths = existing_photos + new_photos
        for temp_path in body.temp_photo_paths:
            Path(temp_path).unlink(missing_ok=True)

        pdf_path = r.pdf_path or report_pdf_path(r.id)
        render_pdf(r.data, r.photo_paths or [], pdf_path, report_id=r.id)
        r.pdf_path = pdf_path

        db.commit()
        return {"id": r.id, "status": r.status, "pdf_url": to_public_url(r.pdf_path)}
    finally:
        db.close()


class DamageItemReviewRequest(BaseModel):
    """Only the two fields a reviewer actually fills in on the checklist.
    Both optional so a caller can tick the verified box without touching
    the part number, or vice versa."""
    human_verified: Optional[bool] = None
    oem_part_number: Optional[str] = None


@app.patch("/reports/{report_id}/damage-items/{item_index}")
def review_damage_item(report_id: str, item_index: int, body: DamageItemReviewRequest) -> dict:
    """Marks one damage line item verified and/or records its OEM part
    number -- the two things a surveyor is meant to do while reviewing the
    checklist, which the schema always had fields for (oem_part_number's
    own description says "a human enters this during review") but nothing
    could actually set.

    Deliberately NOT routed through PUT /reports/{id}: that replaces the
    entire data blob and re-renders the PDF through Chromium on every
    call, which is far too heavy for ticking a checkbox (and this project
    has already hit Chromium crashes rendering photo-heavy reports). The
    PDF does show verification state, but sign_off_report/reopen_report
    both re-render it, so the authoritative document still picks these up
    at the moment it actually matters.
    """
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        # Same lock rule update_report enforces -- a signed-off report is
        # a finalized record; reopen it first.
        if r.status == "Signed Off":
            raise HTTPException(
                status_code=409,
                detail="Report is signed off and locked. Reopen it before editing.",
            )

        data = dict(r.data or {})
        damage_summary = list(data.get("damage_summary") or [])
        if not 0 <= item_index < len(damage_summary):
            raise HTTPException(
                status_code=404,
                detail=f"Damage item {item_index} not found (report has {len(damage_summary)})",
            )

        item = dict(damage_summary[item_index])
        if body.human_verified is not None:
            item["human_verified"] = body.human_verified
        if body.oem_part_number is not None:
            # Empty string clears the field rather than storing "" -- keeps
            # "not entered" as a single honest null everywhere downstream.
            item["oem_part_number"] = body.oem_part_number.strip() or None
        damage_summary[item_index] = item

        # Reassigned rather than mutated in place: `data` is a plain JSON
        # column, so SQLAlchemy only detects the change on assignment (same
        # reason sign_off_report/reopen_report rebuild the dict).
        data["damage_summary"] = damage_summary
        r.data = data

        db.commit()
        return {"id": r.id, "item_index": item_index, "item": item}
    finally:
        db.close()


def _with_sign_off(report_data: dict, **updates) -> dict:
    """Returns a NEW report-data dict with sign_off fields applied.

    Copies at every level on purpose. `dict(r.data)` is a shallow copy, so
    `data["sign_off"]` is the *same object* SQLAlchemy loaded -- mutating
    it in place also mutates the "old" value it compares against, the JSON
    column looks unchanged, and the UPDATE is silently skipped. That is
    exactly what happened here: sign-off appeared to work because
    Report.status (a plain string) did persist, while reviewed_by and
    signature_date never saved at all -- which is why avg_resolution_time
    in /analytics/summary was permanently null.
    """
    data = dict(report_data or {})
    sign_off = dict(data.get("sign_off") or {})
    sign_off.update(updates)
    data["sign_off"] = sign_off
    return data


@app.post("/reports/{report_id}/reopen")
def reopen_report(report_id: str) -> dict:
    """Explicitly unlocks a Signed Off report for editing -- a deliberate
    action rather than update_report silently allowing edits underneath an
    already-signed-off record."""
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")

        r.status = "confirmed"
        r.data = _with_sign_off(r.data, status="Draft")

        pdf_path = r.pdf_path or report_pdf_path(r.id)
        render_pdf(r.data, r.photo_paths or [], pdf_path, report_id=r.id)
        r.pdf_path = pdf_path

        db.commit()
        return {"id": r.id, "status": r.status}
    finally:
        db.close()


@app.post("/reports/{report_id}/sign-off")
def sign_off_report(report_id: str, reviewer_name: str = "Surveyor / Loss Adjuster") -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        
        r.status = "Signed Off"
        # Enables a real avg_resolution_time in /analytics/summary -- these
        # fields existed in the schema already but never actually persisted
        # before (see _with_sign_off for why).
        r.data = _with_sign_off(
            r.data,
            status="Signed Off",
            reviewed_by=reviewer_name,
            signature_date=datetime.now(timezone.utc).isoformat(),
        )
        
        pdf_path = r.pdf_path or report_pdf_path(r.id)
        render_pdf(r.data, r.photo_paths or [], pdf_path, report_id=r.id)
        r.pdf_path = pdf_path

        db.commit()
        return {"id": r.id, "status": r.status, "pdf_url": to_public_url(r.pdf_path)}
    finally:
        db.close()


@app.get("/analytics/summary")
def get_analytics_summary() -> dict:
    db = SessionLocal()
    try:
        reports = db.query(Report).order_by(Report.created_at.desc()).all()
        total_incidents = len(reports)
        pending_review = sum(1 for r in reports if r.status in ("confirmed", "draft", "pending", "Under Review"))
        signed_off = sum(1 for r in reports if r.status in ("Signed Off", "signed_off"))
        high_severity = sum(1 for r in reports if (r.data or {}).get("severity_level") == "Severe")
        
        category_counts: dict[str, int] = {}
        severity_counts: dict[str, int] = {"Minor": 0, "Moderate": 0, "Severe": 0}
        damaged_parts_frequency: dict[str, int] = {}
        confidence_counts: dict[str, int] = {}
        resolution_hours: list[float] = []

        for r in reports:
            data = r.data or {}
            cats = data.get("category") or ["Vehicle Collision or Damage"]
            for c in cats:
                category_counts[c] = category_counts.get(c, 0) + 1

            sev = data.get("severity_level") or "Moderate"
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

            # Same damage_summary-else-damaged_parts fallback used everywhere
            # else in this codebase (see the dashboard's report detail page).
            damage_items = data.get("damage_summary") or [{"part": p} for p in (data.get("damaged_parts") or [])]
            for item in damage_items:
                part = item.get("part")
                if part:
                    damaged_parts_frequency[part] = damaged_parts_frequency.get(part, 0) + 1
                conf = item.get("ai_confidence")
                if conf:
                    confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

            sign_off = data.get("sign_off") or {}
            sig_date = sign_off.get("signature_date")
            if sign_off.get("status") == "Signed Off" and sig_date:
                try:
                    signed_at = datetime.fromisoformat(sig_date)
                    created_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
                    resolution_hours.append((signed_at - created_at).total_seconds() / 3600)
                except (ValueError, TypeError):
                    pass

        total_rated = sum(confidence_counts.values())
        # Real, computed ratio rather than an invented percentage -- the
        # model only ever gives a qualitative High/Medium/Low self-rating
        # (see extraction.py's SYSTEM_PROMPT), so this is "what share of
        # rated detections were High confidence," not a fabricated score.
        ai_confidence_avg = (
            f"{round(100 * confidence_counts.get('High', 0) / total_rated)}% High confidence"
            if total_rated > 0
            else None
        )
        avg_resolution_time = (
            f"{round(sum(resolution_hours) / len(resolution_hours), 1)}h"
            if resolution_hours
            else None
        )

        recent_activity = [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "channel": r.channel,
                "status": r.status,
                "location": (r.data or {}).get("location") or "Site Location",
                "reporter": (r.data or {}).get("reporter_name") or "Reporter",
                "category": (r.data or {}).get("category") or ["Vehicle Collision or Damage"],
            }
            for r in reports[:5]
        ]
        
        return {
            "total_incidents": total_incidents,
            "pending_review": pending_review,
            "signed_off": signed_off,
            "high_severity": high_severity,
            "category_counts": category_counts,
            "severity_counts": severity_counts,
            "damaged_parts_frequency": damaged_parts_frequency,
            "recent_activity": recent_activity,
            "avg_resolution_time": avg_resolution_time,
            "ai_confidence_avg": ai_confidence_avg,
        }
    finally:
        db.close()


@app.delete("/reports/{report_id}")
def delete_report(report_id: str) -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="not found")

        folder = None
        if r.pdf_path:
            folder = Path(r.pdf_path).resolve().parent
        elif r.photo_paths:
            folder = Path(r.photo_paths[0]).resolve().parent.parent
        if folder and folder.exists() and folder.is_relative_to(Path(settings.storage_dir).resolve()):
            shutil.rmtree(folder, ignore_errors=True)

        db.delete(r)
        db.commit()
        return {"deleted": report_id}
    finally:
        db.close()



# =============================================================================
# Settings & system info
#
# Split deliberately into two halves, because conflating them is exactly
# what made the old Settings page dishonest -- it showed a hardcoded model
# chain (which didn't even match the real one) next to a Save button that
# persisted nothing, implying all of it was editable.
#
#   /settings     -> the few genuinely user-owned values, really persisted
#   /system/info  -> live, read-only facts about how this deployment is
#                    actually configured and what's in it
# =============================================================================

# Only keys listed here can be written, so a typo'd or hostile key can't
# quietly fill the table with junk.
ALLOWED_SETTING_KEYS = {"company_name"}


class SettingsUpdateRequest(BaseModel):
    company_name: Optional[str] = None


@app.get("/settings")
def get_settings() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).all()
        stored = {r.key: r.value for r in rows}
        return {key: stored.get(key, "") for key in sorted(ALLOWED_SETTING_KEYS)}
    finally:
        db.close()


@app.put("/settings")
def update_settings(body: SettingsUpdateRequest) -> dict:
    db = SessionLocal()
    try:
        for key, value in body.model_dump(exclude_none=True).items():
            if key not in ALLOWED_SETTING_KEYS:
                continue
            row = db.get(AppSetting, key)
            if row:
                row.value = value.strip()
            else:
                db.add(AppSetting(key=key, value=value.strip()))
        db.commit()
        rows = db.query(AppSetting).all()
        stored = {r.key: r.value for r in rows}
        return {key: stored.get(key, "") for key in sorted(ALLOWED_SETTING_KEYS)}
    finally:
        db.close()


@app.get("/system/info")
def get_system_info() -> dict:
    """Live read-only facts about this deployment. Everything here is
    measured or read from real config at request time -- nothing is a
    hardcoded display value."""
    db = SessionLocal()
    try:
        reports = db.query(Report).all()
        channel_counts: dict[str, int] = {}
        photo_count = 0
        pdf_count = 0
        for r in reports:
            channel_counts[r.channel] = channel_counts.get(r.channel, 0) + 1
            photo_count += len(r.photo_paths or [])
            if r.pdf_path:
                pdf_count += 1

        storage_root = Path(settings.storage_dir)
        storage_bytes = 0
        if storage_root.exists():
            storage_bytes = sum(f.stat().st_size for f in storage_root.rglob("*") if f.is_file())

        return {
            "ai": {
                # The real chain this deployment will actually try, in order.
                "model_chain": [m.strip() for m in settings.gemini_model_chain.split(",") if m.strip()],
                "min_call_interval_seconds": settings.gemini_min_call_interval_seconds,
                "request_timeout_seconds": 45.0,
                # Never the key itself -- only whether one is present.
                "api_key_configured": bool(settings.gemini_api_key),
            },
            "channels": {
                "telegram_configured": bool(settings.telegram_bot_token),
                "whatsapp_configured": bool(settings.twilio_account_sid and settings.twilio_auth_token),
                "reports_by_channel": channel_counts,
            },
            "storage": {
                "reports": len(reports),
                "photos": photo_count,
                "pdfs": pdf_count,
                "bytes_used": storage_bytes,
                "storage_dir": settings.storage_dir,
            },
            "database": {
                # Scheme only -- the URL can contain credentials on a
                # non-sqlite deployment.
                "engine": settings.database_url.split("://", 1)[0],
            },
            "auth": {
                # Stated as a fact so the UI doesn't have to hardcode a
                # claim that could drift if auth is ever added.
                "configured": False,
            },
        }
    finally:
        db.close()


# =============================================================================
# Setup: operator-supplied API keys and connection tests
#
# The Settings page is a SETUP surface, not an internals dashboard -- the
# operator supplies credentials and gets told whether they work. Model
# chain, rate limits and timeouts are deliberately not exposed: they're
# implementation detail the operator can't act on, and the fallback
# behaviour is the system's job to handle silently.
#
# SECURITY: no endpoint here ever returns key material. /setup/llm-keys
# returns only an id, label, created_at and the last 4 characters -- enough
# to tell two keys apart in a list, useless to steal. Note this does NOT
# protect against tampering: with no authentication on this deployment,
# anyone who can reach the URL can add or delete keys.
# =============================================================================


class AddApiKeyRequest(BaseModel):
    apiKey: str
    label: Optional[str] = None


def _mask(key: str) -> str:
    return key[-4:] if len(key) >= 4 else "****"


@app.get("/setup/llm-keys")
def list_llm_keys() -> dict:
    db = SessionLocal()
    try:
        rows = db.query(ApiKey).filter(ApiKey.provider == "gemini").order_by(ApiKey.created_at).all()
        return {
            "keys": [
                {"id": r.id, "label": r.label or "", "last4": _mask(r.key), "created_at": r.created_at.isoformat()}
                for r in rows
            ],
            # Surfaces that drafting still works off the deploy-time env var
            # even with no keys added here, so an empty list doesn't look
            # like a broken install.
            "env_key_configured": bool(settings.gemini_api_key),
        }
    finally:
        db.close()


@app.post("/setup/llm-keys")
def add_llm_key(body: AddApiKeyRequest) -> dict:
    key = body.apiKey.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    db = SessionLocal()
    try:
        existing = db.query(ApiKey).filter(ApiKey.provider == "gemini", ApiKey.key == key).first()
        if existing:
            raise HTTPException(status_code=409, detail="That API key has already been added")
        row = ApiKey(provider="gemini", key=key, label=(body.label or "").strip())
        db.add(row)
        db.commit()
        reset_client_cache()
        return {"id": row.id, "label": row.label, "last4": _mask(row.key)}
    finally:
        db.close()


@app.delete("/setup/llm-keys/{key_id}")
def delete_llm_key(key_id: str) -> dict:
    db = SessionLocal()
    try:
        row = db.get(ApiKey, key_id)
        if not row:
            raise HTTPException(status_code=404, detail="API key not found")
        db.delete(row)
        db.commit()
        reset_client_cache()
        return {"deleted": key_id}
    finally:
        db.close()


@app.post("/setup/test/{service}")
async def test_service(service: str) -> dict:
    """Proves a credential actually works, rather than only checking that
    a non-empty string is present. Returns ok/false plus a short message
    for display -- never raises for a failed credential, since 'your key
    is wrong' is a normal result here, not a server error."""
    if service == "llm":
        keys = available_api_keys()
        if not keys:
            return {"ok": False, "message": "No API key configured"}

        def _probe(api_key: str) -> tuple[bool, str]:
            try:
                client = get_client(api_key)
                # Cheapest real call that still proves the key is accepted:
                # list models. Avoids spending generation quota just to
                # answer "does this key work".
                next(iter(client.models.list()), None)
                return True, "Key accepted"
            except Exception as exc:
                return False, str(exc)[:160]

        working = 0
        first_error = ""
        for k in keys:
            ok, msg = await asyncio.to_thread(_probe, k)
            if ok:
                working += 1
            elif not first_error:
                first_error = msg
        if working:
            return {"ok": True, "message": f"{working} of {len(keys)} key(s) working"}
        return {"ok": False, "message": first_error or "No key accepted"}

    if service == "telegram":
        if not settings.telegram_bot_token:
            return {"ok": False, "message": "No bot token configured"}

        def _probe_tg() -> tuple[bool, str]:
            try:
                import httpx

                r = httpx.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
                    timeout=10.0,
                )
                data = r.json()
                if r.status_code == 200 and data.get("ok"):
                    return True, f"Connected as @{data['result'].get('username', 'unknown')}"
                return False, str(data.get("description", "Token rejected"))[:160]
            except Exception as exc:
                return False, str(exc)[:160]

        ok, msg = await asyncio.to_thread(_probe_tg)
        return {"ok": ok, "message": msg}

    if service == "whatsapp":
        if not (settings.twilio_account_sid and settings.twilio_auth_token):
            return {"ok": False, "message": "Twilio credentials not configured"}

        def _probe_twilio() -> tuple[bool, str]:
            try:
                import httpx

                r = httpx.get(
                    f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}.json",
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                    timeout=10.0,
                )
                if r.status_code == 200:
                    return True, "Credentials accepted"
                return False, f"Twilio rejected the credentials ({r.status_code})"
            except Exception as exc:
                return False, str(exc)[:160]

        ok, msg = await asyncio.to_thread(_probe_twilio)
        return {"ok": ok, "message": msg}

    raise HTTPException(status_code=404, detail=f"Unknown service {service!r}")
