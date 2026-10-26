#!/usr/bin/env python3
"""
make_aruco_sheet.py — printable A4 braille sheets with ArUco markers.

The markers let braille_capture.py flatten the page exactly, know its scale in
millimetres, and know which way is up. Layouts here must match SHEET_LAYOUTS in
braille_capture.py.

Layouts:
  sq180  (default) a 180 x 180 mm camera square near the top of the page, markers
         (IDs 4-7) in its corners. For a camera that only sees ~180 x 180 mm.
  a4     markers (IDs 0-3) in the page corners; uses the whole sheet.

  python3 make_aruco_sheet.py                       # braille_sheet_sq180.pdf
  python3 make_aruco_sheet.py --layout a4           # braille_sheet_a4.pdf
  python3 make_aruco_sheet.py --pages 10
"""

import argparse

import cv2
from reportlab.lib.colors import Color, black
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = 210.0, 297.0          # A4, mm
DICT = cv2.aruco.DICT_4X4_50

# All positions in mm from the page's top-left corner.
#   markers: id -> (x, y) of the marker's top-left;  marker: side length
#   write:   x0, y0, x1, y1 of the area to write in;  frame: area the camera must see
SHEET_LAYOUTS = {
    "sq180": {
        "marker": 12.0,
        "markers": {4: (18.0, 23.0), 5: (180.0, 23.0), 6: (180.0, 185.0), 7: (18.0, 185.0)},
        "write": (17.0, 38.0, 193.0, 182.0),
        "frame": (15.0, 20.0, 195.0, 200.0),
    },
    "a4": {
        "marker": 20.0,
        "markers": {0: (8.0, 8.0), 1: (182.0, 8.0), 2: (182.0, 269.0), 3: (8.0, 269.0)},
        "write": (10.0, 32.0, 200.0, 265.0),
        "frame": (0.0, 0.0, PAGE_W, PAGE_H),
    },
}


def marker_bits(marker_id):
    """6x6 grid (border + 4x4 data) of 1 = black."""
    d = cv2.aruco.getPredefinedDictionary(DICT)
    size = 6 * 10
    if hasattr(cv2.aruco, "generateImageMarker"):
        img = cv2.aruco.generateImageMarker(d, marker_id, size)
    else:                                              # OpenCV < 4.7 (Raspberry Pi OS)
        img = cv2.aruco.drawMarker(d, marker_id, size)
    return [[1 if img[r * 10 + 5, c * 10 + 5] < 128 else 0 for c in range(6)] for r in range(6)]


def y(top_mm):                                         # reportlab measures from the bottom
    return (PAGE_H - top_mm) * mm


def corner_marks(c, box, arm, colour, width):
    x0, y0, x1, y1 = box
    c.setStrokeColor(colour)
    c.setLineWidth(width)
    for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x1, y1, -1, -1), (x0, y1, 1, -1)):
        c.line(cx * mm, y(cy), (cx + dx * arm) * mm, y(cy))
        c.line(cx * mm, y(cy), cx * mm, y(cy + dy * arm))


def draw_page(c, layout):
    L = SHEET_LAYOUTS[layout]
    side = L["marker"]
    cell = side / 6
    c.setFillColor(black)
    for mid, (mx, my) in L["markers"].items():         # vector squares: razor-sharp print
        bits = marker_bits(mid)
        for r in range(6):
            for col in range(6):
                if bits[r][col]:
                    c.rect((mx + col * cell) * mm, y(my + (r + 1) * cell),
                           cell * mm + 0.05, cell * mm + 0.05, stroke=0, fill=1)

    faint = Color(0.82, 0.82, 0.82)                    # too faint to look like dots
    corner_marks(c, L["write"], 5, faint, 0.3)
    if layout != "a4":
        corner_marks(c, L["frame"], 8, Color(0.6, 0.6, 0.6), 0.4)   # camera square

    grey = Color(0.45, 0.45, 0.45)
    c.setFillColor(grey)
    if layout == "a4":
        title_y, scale_y = 15, 277
    else:
        title_y, scale_y = L["frame"][3] + 12, L["frame"][3] + 40
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(PAGE_W / 2 * mm, y(title_y), "BRAILLE READER SHEET")
    c.setFont("Helvetica", 7)
    lines = ["Print at 100% / Actual size.  Place this printed side DOWN in the slate, top edge up,",
             "so the dots rise on this side. Write only between the small corner marks."]
    if layout != "a4":
        lines.append("Point the camera at the large corner marks: the 180 x 180 mm square above.")
    for i, t in enumerate(lines):
        c.drawCentredString(PAGE_W / 2 * mm, y(title_y + 5 + 4 * i), t)

    bx = PAGE_W / 2 - 25                                # 50 mm scale bar
    c.setStrokeColor(grey)
    c.setLineWidth(0.4)
    c.line(bx * mm, y(scale_y), (bx + 50) * mm, y(scale_y))
    for t in range(0, 51, 10):
        c.line((bx + t) * mm, y(scale_y - 1.5), (bx + t) * mm, y(scale_y + (1.5 if t % 50 == 0 else 0)))
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(PAGE_W / 2 * mm, y(scale_y + 4.5), "this bar must measure exactly 50 mm")


def main():
    ap = argparse.ArgumentParser(description="Make an ArUco braille sheet (A4)")
    ap.add_argument("--layout", choices=SHEET_LAYOUTS, default="sq180")
    ap.add_argument("-o", "--output")
    ap.add_argument("--pages", type=int, default=1)
    args = ap.parse_args()
    out = args.output or f"braille_sheet_{args.layout}.pdf"

    c = canvas.Canvas(out, pagesize=(PAGE_W * mm, PAGE_H * mm))
    c.setTitle(f"Braille reader sheet ({args.layout})")
    for _ in range(args.pages):
        draw_page(c, args.layout)
        c.showPage()
    c.save()
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
