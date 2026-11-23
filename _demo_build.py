"""Build a small proof-of-capability deck + document in this workspace."""

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

INK = RGBColor(0x11, 0x1B, 0x2B)
ACCENT = RGBColor(0x2E, 0x6B, 0xFF)
MUTED = RGBColor(0x5A, 0x66, 0x7A)
PAPER = RGBColor(0xF7, 0xF8, 0xFA)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)


def blank():
    return prs.slides.add_slide(prs.slide_layouts[6])


def fill_bg(slide, color=PAPER):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color


def text(slide, left, top, width, height, body, size, color=INK, bold=False):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = body
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.name = "Segoe UI"
    return box


# --- Slide 1: title ---
s = blank()
fill_bg(s, INK)
bar = s.shapes.add_shape(1, Inches(0.9), Inches(2.5), Inches(0.12), Inches(2.2))
bar.fill.solid()
bar.fill.fore_color.rgb = ACCENT
bar.line.fill.background()
text(s, Inches(1.25), Inches(2.4), Inches(10.5), Inches(1.1),
     "Generated inside this session", 44, RGBColor(0xFF, 0xFF, 0xFF), bold=True)
text(s, Inches(1.28), Inches(3.6), Inches(10.5), Inches(1.0),
     "A real .pptx and .pdf built by the agent you are talking to,\nnot a mock-up.",
     20, RGBColor(0xB8, 0xC4, 0xD8))
text(s, Inches(1.28), Inches(5.6), Inches(10.5), Inches(0.6),
     "{MODEL} Harness  ·  office toolchain demo", 14, RGBColor(0x7C, 0x8A, 0xA3))

# --- Slide 2: what actually works ---
s = blank()
fill_bg(s)
text(s, Inches(0.75), Inches(0.55), Inches(11.8), Inches(0.8),
     "What the toolchain actually does", 32, bold=True)
accent_rule = s.shapes.add_shape(1, Inches(0.78), Inches(1.45), Inches(1.4), Inches(0.06))
accent_rule.fill.solid()
accent_rule.fill.fore_color.rgb = ACCENT
accent_rule.line.fill.background()

rows = [
    ("PowerPoint", "python-pptx 1.0.2", "Editable slides, native charts, tables"),
    ("Word", "python-docx 1.2.0", "Headings, styles, tables, inline images"),
    ("Excel", "openpyxl / XlsxWriter", "Formulas, formats, recalculated values"),
    ("PDF", "LibreOffice Kit", "True PDF export with %PDF- signature"),
    ("Images", "Pillow 12.3.0", "Render slides to PNG for visual checks"),
]
y = 1.85
for name, lib, note in rows:
    text(s, Inches(0.8), Inches(y), Inches(2.5), Inches(0.45), name, 17, bold=True)
    text(s, Inches(3.3), Inches(y), Inches(3.0), Inches(0.45), lib, 14, MUTED)
    text(s, Inches(6.4), Inches(y), Inches(6.2), Inches(0.45), note, 14, INK)
    rule = s.shapes.add_shape(1, Inches(0.8), Inches(y + 0.62), Inches(11.8), Inches(0.012))
    rule.fill.solid()
    rule.fill.fore_color.rgb = RGBColor(0xDD, 0xE1, 0xE8)
    rule.line.fill.background()
    y += 0.85

# --- Slide 3: native chart ---
s = blank()
fill_bg(s)
text(s, Inches(0.75), Inches(0.5), Inches(11.8), Inches(0.7),
     "Native, editable chart — not an image", 30, bold=True)

data = CategoryChartData()
data.categories = ["PPTX", "DOCX", "XLSX", "PDF", "PNG"]
data.add_series("Formats produced", [1, 1, 1, 1, 1])
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                        Inches(0.8), Inches(1.55), Inches(11.7), Inches(4.9), data)
chart = gf.chart
chart.has_legend = False
chart.value_axis.maximum_scale = 1.4
chart.value_axis.has_major_gridlines = False
plot = chart.plots[0]
plot.gap_width = 120
plot.series[0].format.fill.solid()
plot.series[0].format.fill.fore_color.rgb = ACCENT
chart.font.size = Pt(13)
chart.font.name = "Segoe UI"

# --- Slide 4: closing ---
s = blank()
fill_bg(s, INK)
text(s, Inches(1.1), Inches(2.9), Inches(11.2), Inches(1.0),
     "Everything here is editable.", 40, RGBColor(0xFF, 0xFF, 0xFF), bold=True)
text(s, Inches(1.13), Inches(4.05), Inches(11.2), Inches(1.2),
     "Open the .pptx and move anything. Change the chart data, re-run the export,\n"
     "and the PDF follows. That is the difference that matters.",
     19, RGBColor(0xB8, 0xC4, 0xD8))

out = r"C:\Users\Ravikiran\Downloads\filmsclub\agent-capability-demo.pptx"
prs.save(out)
print("saved", out, "slides:", len(prs.slides.__iter__.__self__._sldIdLst))
