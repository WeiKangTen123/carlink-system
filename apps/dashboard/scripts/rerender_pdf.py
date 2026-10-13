from app.reports.db import SessionLocal
from app.reports.models import Report
from app.rendering.renderer import render_pdf

db = SessionLocal()
report = db.get(Report, "2790f292c0e3")
if report and report.pdf_path:
    print(f"Re-rendering PDF for {report.id} to {report.pdf_path}...")
    render_pdf(report.data, report.photo_paths, report.pdf_path, report_id=report.id)
    print("PDF re-rendered successfully!")
else:
    print("Report or pdf_path not found!")
db.close()
