"""One-off: render the benefits enrollment guide to a real multi-section PDF so
the corpus exercises the pypdf ingestion path. Run: python -m backend.scripts.generate_benefits_pdf"""
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

OUT = "backend/data/docs/benefits/benefits-enrollment-guide.pdf"
BODY = [
    ("Benefits Enrollment Guide — GSVH Corp", "h1"),
    ("Open enrollment runs the first two weeks of November; coverage is effective January 1. New hires enroll within 30 days of start.", "n"),
    ("Medical", "h2"),
    ("Three PPO tiers. Employee-only premium $0 (Standard), $80/mo (Plus), $160/mo (Premium). GSVH Corp covers 90% of dependent premiums.", "n"),
    ("Retirement", "h2"),
    ("401(k) with a 4% employer match, vested immediately. Contributions up to the IRS annual limit.", "n"),
    ("Wellness", "h2"),
    ("$600/year wellness stipend; annual physical covered at 100%.", "n"),
]

def main():
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(OUT, pagesize=LETTER)
    flow = []
    for text, kind in BODY:
        style = {"h1": styles["Title"], "h2": styles["Heading2"], "n": styles["BodyText"]}[kind]
        flow.append(Paragraph(text, style))
        flow.append(Spacer(1, 8))
    doc.build(flow)
    print("wrote", OUT)

if __name__ == "__main__":
    main()
