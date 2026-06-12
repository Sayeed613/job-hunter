"""Resume generator — produces DOCX and PDF files from tailored profiles."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from app.resume.models import ResumeProfile

try:
    from fpdf import FPDF
    _HAS_FPDF = True
except ImportError:
    _HAS_FPDF = False

_PDF_FONT = None
if _HAS_FPDF:
    for _path in [
        r"C:\Windows\Fonts\DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]:
        if os.path.exists(_path):
            _PDF_FONT = _path
            break

logger = logging.getLogger("job_automation_bot")

_SECTION_HEADING_SIZE = Pt(14)
_BODY_SIZE = Pt(11)
_NAME_SIZE = Pt(22)
_COLOR_PRIMARY = RGBColor(0x1A, 0x1A, 0x2E)
_MARGIN_INCHES = 0.75


class ResumeGenerator:
    """Generates formatted DOCX and PDF resume files from a ResumeProfile."""

    def __init__(self) -> None:
        self._has_fpdf = _HAS_FPDF
        if not self._has_fpdf:
            logger.warning("fpdf2 not installed — PDF generation will raise RuntimeError")

    def generate_docx(self, profile: ResumeProfile, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        doc = Document()
        for section in doc.sections:
            section.top_margin = Inches(_MARGIN_INCHES)
            section.bottom_margin = Inches(_MARGIN_INCHES)
            section.left_margin = Inches(_MARGIN_INCHES)
            section.right_margin = Inches(_MARGIN_INCHES)

        self._add_header(doc, profile)
        if profile.summary:
            self._add_section_heading(doc, "Professional Summary")
            self._add_body(doc, profile.summary)
        if profile.skills:
            self._add_section_heading(doc, "Skills")
            self._add_body(doc, ", ".join(profile.skills))
        if profile.experience:
            self._add_section_heading(doc, "Experience")
            for entry in profile.experience:
                self._add_bullet(doc, entry)
        if profile.projects:
            self._add_section_heading(doc, "Projects")
            for project in profile.projects:
                tech_str = f" — {', '.join(project.technologies)}" if project.technologies else ""
                self._add_bullet(doc, f"{project.name}{tech_str}")
                if project.description:
                    self._add_body(doc, project.description, indent=True)
        if profile.education:
            self._add_section_heading(doc, "Education")
            for entry in profile.education:
                self._add_bullet(doc, entry)
        if profile.certifications:
            self._add_section_heading(doc, "Certifications")
            for cert in profile.certifications:
                self._add_bullet(doc, cert)

        doc.save(str(output))
        logger.info("DOCX generated", extra={"path": str(output)})
        return output

    def generate_pdf(self, profile: ResumeProfile, output_path: str | Path) -> Path:
        if not self._has_fpdf:
            raise RuntimeError("PDF generation requires fpdf2. Install with: pip install fpdf2")
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        if _PDF_FONT:
            pdf.add_font("DejaVu", "", _PDF_FONT, uni=True)
            pdf.add_font("DejaVu", "B", _PDF_FONT, uni=True)
        _FONT = "DejaVu" if _PDF_FONT else "Helvetica"

        pdf.set_font(_FONT, "B", 20)
        pdf.cell(0, 10, profile.name, new_x="LMARGIN", new_y="NEXT", align="C")
        contact_parts = [p for p in [profile.email, profile.phone, profile.location] if p]
        if contact_parts:
            pdf.set_font(_FONT, "", 9)
            pdf.cell(0, 6, "  |  ".join(contact_parts), new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(4)

        def _write(title: str, lines: list[str]) -> None:
            pdf.set_font(_FONT, "B", 12)
            pdf.set_text_color(0x1A, 0x1A, 0x2E)
            pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(0x1A, 0x1A, 0x2E)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(2)
            pdf.set_font(_FONT, "", 10)
            pdf.set_text_color(0, 0, 0)
            for line in lines:
                pdf.multi_cell(0, 5, f"• {line}")
                pdf.ln(1)

        if profile.summary:
            _write("Professional Summary", [profile.summary])
        if profile.skills:
            _write("Skills", [", ".join(profile.skills)])
        if profile.experience:
            _write("Experience", profile.experience)
        if profile.projects:
            p_lines = []
            for p in profile.projects:
                line = p.name
                if p.technologies:
                    line += f" ({', '.join(p.technologies)})"
                if p.description:
                    line += f" — {p.description[:80]}"
                p_lines.append(line)
            _write("Projects", p_lines)
        if profile.education:
            _write("Education", profile.education)
        if profile.certifications:
            _write("Certifications", profile.certifications)

        pdf.output(str(output))
        logger.info("PDF generated", extra={"path": str(output)})
        return output

    @staticmethod
    def _add_header(doc: Document, profile: ResumeProfile) -> None:
        name_para = doc.add_paragraph()
        name_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = name_para.add_run(profile.name)
        run.bold = True
        run.font.size = _NAME_SIZE
        run.font.color.rgb = _COLOR_PRIMARY
        contact_parts = [p for p in [profile.email, profile.phone, profile.location] if p]
        if contact_parts:
            contact_para = doc.add_paragraph()
            contact_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = contact_para.add_run("  |  ".join(contact_parts))
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    @staticmethod
    def _add_section_heading(doc: Document, title: str) -> None:
        heading = doc.add_paragraph()
        run = heading.add_run(title.upper())
        run.bold = True
        run.font.size = _SECTION_HEADING_SIZE
        run.font.color.rgb = _COLOR_PRIMARY
        heading.paragraph_format.space_before = Pt(12)
        heading.paragraph_format.space_after = Pt(4)

    @staticmethod
    def _add_body(doc: Document, text: str, indent: bool = False) -> None:
        para = doc.add_paragraph(text)
        para.paragraph_format.space_after = Pt(2)
        if indent:
            para.paragraph_format.left_indent = Inches(0.25)
        for run in para.runs:
            run.font.size = _BODY_SIZE

    @staticmethod
    def _add_bullet(doc: Document, text: str) -> None:
        para = doc.add_paragraph(text, style="List Bullet")
        para.paragraph_format.space_after = Pt(1)
        for run in para.runs:
            run.font.size = _BODY_SIZE
