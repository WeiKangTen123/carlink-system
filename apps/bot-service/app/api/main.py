import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.ai.extraction import draft_report
from app.channels.whatsapp import router as whatsapp_router
from app.config import settings
from app.reports.db import SessionLocal, init_db
from app.reports.models import Report
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
        return [
            {
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "channel": r.channel,
                "created_at": r.created_at.isoformat(),
                "location": (r.data or {}).get("location"),
                "category": (r.data or {}).get("category"),
                "thumbnail_url": to_public_url(r.photo_paths[0]) if r.photo_paths else None,
            }
            for r in reports
        ]
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
        draft = draft_report(description, saved_paths)
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
def update_report(report_id: str, draft: SecurityIncidentDraft) -> dict:
    db = SessionLocal()
    try:
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Update draft data dictionary
        r.data = draft.model_dump()
        
        # Re-render PDF with updated content
        pdf_path = r.pdf_path or report_pdf_path(r.id)
        render_pdf(r.data, r.photo_paths or [], pdf_path, report_id=r.id)
        r.pdf_path = pdf_path

        db.commit()
        return {"id": r.id, "status": r.status, "pdf_url": to_public_url(r.pdf_path)}
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
        data = dict(r.data or {})
        sign_off = data.get("sign_off") or {}
        sign_off["status"] = "Signed Off"
        sign_off["reviewed_by"] = reviewer_name
        data["sign_off"] = sign_off
        r.data = data
        
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
        
        for r in reports:
            cats = (r.data or {}).get("category") or ["Vehicle Collision or Damage"]
            for c in cats:
                category_counts[c] = category_counts.get(c, 0) + 1
            
            sev = (r.data or {}).get("severity_level") or "Moderate"
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            
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
            "recent_activity": recent_activity,
            "avg_resolution_time": "1.4 hours",
            "ai_confidence_avg": "93.8%",
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

