"""Convert a Markdown file to PDF with CJK (Chinese) support."""
import sys
import markdown
from xhtml2pdf import pisa
from pathlib import Path


def md_to_pdf(md_path: str, pdf_path: str | None = None):
    md_file = Path(md_path)
    if pdf_path is None:
        pdf_path = md_file.with_suffix(".pdf")

    md_text = md_file.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite", "toc"],
    )

    # Wrap with full HTML including CJK font support
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
@page {{
    size: A4;
    margin: 2cm;
}}
body {{
    font-family: "Microsoft YaHei", "SimSun", "STSong", sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #222;
}}
h1 {{ font-size: 20pt; margin-top: 20pt; border-bottom: 1px solid #ccc; padding-bottom: 4pt; }}
h2 {{ font-size: 16pt; margin-top: 16pt; }}
h3 {{ font-size: 13pt; margin-top: 12pt; }}
blockquote {{
    border-left: 3px solid #999;
    padding-left: 10px;
    color: #555;
    margin: 8px 0;
}}
code {{
    background: #f4f4f4;
    padding: 1px 4px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 10pt;
}}
pre {{
    background: #f4f4f4;
    padding: 8px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
    white-space: pre-wrap;
    word-wrap: break-word;
}}
table {{
    border-collapse: collapse;
    margin: 8px 0;
}}
th, td {{
    border: 1px solid #999;
    padding: 4px 8px;
    font-size: 10pt;
}}
th {{
    background: #eee;
}}
hr {{
    border: none;
    border-top: 1px solid #ccc;
    margin: 12pt 0;
}}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    with open(pdf_path, "wb") as f:
        status = pisa.CreatePDF(html, dest=f)

    if status.err:
        print(f"Error generating PDF: {status.err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"PDF saved to: {pdf_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python md2pdf.py <input.md> [output.pdf]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    md_to_pdf(sys.argv[1], out)
