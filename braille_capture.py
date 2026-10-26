#!/usr/bin/env python3
"""
braille_capture.py — photograph braille (camera or image file), decode it, and speak it aloud.

Pipeline:
  1. find the sheet of paper and flatten it (perspective correction)
  2. detect dots at several sizes (works for embossed/raised dots and printed dots)
  3. group dots into lines, fitting each line's own slope (handles slate & stylus work)
  4. build 6-dot cells from each line's local spacing
  5. try all 8 orientations (4 rotations x mirrored) and keep the one that reads best
  6. decode with braille_reader and speak

Needs braille_reader.py in the same folder.

Raspberry Pi 4B + Camera Module (NoIR v2) — install (Raspberry Pi OS Bookworm):
  sudo apt install -y python3-picamera2 python3-opencv python3-numpy espeak-ng python3-gpiozero
  (all from apt, so no pip or venv needed; if you do use a venv, create it with
   --system-site-packages so picamera2 is visible)

Other computers:
  pip install opencv-python numpy pyttsx3

Usage:
  python3 braille_capture.py                      # Pi desktop: preview, SPACE = read, Q = quit
  python3 braille_capture.py --headless           # no screen: press Enter to read
  python3 braille_capture.py --button 17          # push-button on GPIO17 (to GND) reads aloud
  python3 braille_capture.py --image page.jpg     # read a photo
  python3 braille_capture.py --image page.jpg --debug check.png   # save what it detected
  python3 braille_capture.py --voice en-us --speed 110 --line-pause 1
  python3 braille_capture.py --make-sample test.png --sample-text "Hello world"

Setup tips:
  * Mount the camera looking straight down at the page. The whole sheet should be in
    view against a darker surface, so it can find and straighten the page. Any rotation
    works; the first read works out which way is up and later reads reuse that.
  * Embossed braille: light from one side at a low angle so each dot casts a shadow.
    The NoIR camera sees infrared, so a low-angle IR LED works too; the pink tint
    of NoIR pictures does not matter, because it works in greyscale.
  * Camera v2 is focused for far away by default; at 25-40 cm the dots may blur.
    Turn the lens slightly with the focus tool that came with it, using --debug to check.
"""

import argparse
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

import speech
from braille_reader import decode, encode

WORK_SIZE = 1300          # long side of the flattened page, in pixels
MAX_DOTS = 4000           # a full A4 page of braille is ~2500 dots; more means texture


# ---------------------------------------------------------------------------
# 1. Find and flatten the page
# ---------------------------------------------------------------------------

def _order_corners(q):
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.array([q[s.argmin()], q[d.argmin()], q[s.argmax()], q[d.argmax()]], np.float32)


def find_paper(img):
    """Return the 4 corners of the largest bright sheet, or None."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.GaussianBlur(g, (9, 9), 0)
    _, m = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.15 * img.shape[0] * img.shape[1]:
        return None
    hull = cv2.convexHull(c)
    per = cv2.arcLength(hull, True)
    for eps in np.linspace(0.01, 0.1, 40):
        a = cv2.approxPolyDP(hull, eps * per, True)
        if len(a) == 4:
            q = _order_corners(a.reshape(4, 2).astype(np.float32))
            return q if _has_real_edges(g, q) else None
    return None


def _has_real_edges(gray, q, step=6, min_contrast=25):
    """A real sheet has a sharp brightness step along each side that isn't the photo's border.
    (Stops a lighting gradient from being mistaken for the edge of the paper.)"""
    h, w = gray.shape
    centre = q.mean(0)
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        mid = (a + b) / 2
        if min(mid[0], mid[1], w - 1 - mid[0], h - 1 - mid[1]) < 4:
            continue                                    # side lies on the image border
        normal = np.array([b[1] - a[1], a[0] - b[0]], np.float32)
        normal /= np.linalg.norm(normal) + 1e-6
        if np.dot(centre - mid, normal) < 0:
            normal = -normal                            # point inwards
        diffs = []
        for t in np.linspace(0.15, 0.85, 15):
            p = a + t * (b - a)
            pin, pout = (p + normal * step).astype(int), (p - normal * step).astype(int)
            if (0 <= pin[0] < w and 0 <= pin[1] < h and 0 <= pout[0] < w and 0 <= pout[1] < h):
                diffs.append(float(gray[pin[1], pin[0]]) - float(gray[pout[1], pout[0]]))
        if not diffs or np.median(diffs) < min_contrast:
            return False
    return True


def flatten_page(img, size=WORK_SIZE):
    """Perspective-correct the sheet (if found) and scale its long side to `size`
    (None = keep the camera's resolution, for OCR)."""
    q = find_paper(img)
    if q is not None:
        w = max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[3] - q[2]))
        h = max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2]))
        s = size / max(w, h) if size else 1.0
        W, H = int(w * s), int(h * s)
        M = cv2.getPerspectiveTransform(q, np.float32([[0, 0], [W, 0], [W, H], [0, H]]))
        page = cv2.warpPerspective(img, M, (W, H))
        border = int(0.012 * max(W, H))          # ignore the sheet's own edge
        return page, border
    if not size:
        return img, 0
    s = size / max(img.shape[:2])
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), 0


# ---------------------------------------------------------------------------
# 1b. ArUco sheet (from make_aruco_sheet.py): exact flattening, scale and "up"
# ---------------------------------------------------------------------------

# must match make_aruco_sheet.py (mm from the page's top-left)
SHEET_LAYOUTS = {
    "180 mm square": {      # camera sees a 180 x 180 mm square; marker IDs 4-7
        "marker": 12.0,
        "markers": {4: (18.0, 23.0), 5: (180.0, 23.0), 6: (180.0, 185.0), 7: (18.0, 185.0)},
        "write": (17.0, 38.0, 193.0, 182.0),
    },
    "full A4": {            # markers in the page corners; IDs 0-3
        "marker": 20.0,
        "markers": {0: (8.0, 8.0), 1: (182.0, 8.0), 2: (182.0, 269.0), 3: (8.0, 269.0)},
        "write": (10.0, 32.0, 200.0, 265.0),
    },
}
PX_PER_MM = 6.0                                  # 2.5 mm dot spacing -> 15 px


def find_sheet_markers(img):
    """(homography to sheet pixels, markers seen, layout) for the best-matching layout, or None."""
    if not hasattr(cv2, "aruco"):
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    if hasattr(cv2.aruco, "ArucoDetector"):                       # OpenCV 4.7+
        corners, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(gray)
    else:                                                         # OpenCV 4.6 (Pi OS apt)
        corners, ids, _ = cv2.aruco.detectMarkers(gray, d, parameters=cv2.aruco.DetectorParameters_create())
    if ids is None:
        return None
    ids = [int(i) for i in ids.ravel()]
    best = None
    for name, L in SHEET_LAYOUTS.items():
        src, dst = [], []
        for c, i in zip(corners, ids):
            if i in L["markers"]:
                x, y = L["markers"][i]
                m = L["marker"]
                src.extend(c.reshape(4, 2))
                dst.extend([(x, y), (x + m, y), (x + m, y + m), (x, y + m)])
        if len(src) >= 12 and (best is None or len(src) > best[0]):   # 3+ markers
            best = (len(src), src, dst, name)
    if best is None:
        return None
    n, src, dst, name = best
    x0, y0 = SHEET_LAYOUTS[name]["write"][:2]
    # map straight into writing-area pixels (origin at the write area's top-left)
    dst = (np.float32(dst) - np.float32([x0, y0])) * PX_PER_MM
    H, _ = cv2.findHomography(np.float32(src), dst, cv2.RANSAC, 5.0)
    return H, n // 4, name


def flatten_sheet(img):
    """(writing area upright at PX_PER_MM, markers seen, layout name), or None."""
    found = find_sheet_markers(img)
    if found is None:
        return None
    H, n, name = found
    x0, y0, x1, y1 = SHEET_LAYOUTS[name]["write"]
    size = (int((x1 - x0) * PX_PER_MM), int((y1 - y0) * PX_PER_MM))
    return cv2.warpPerspective(img, H, size, borderValue=(255, 255, 255)), n, name


# ---------------------------------------------------------------------------
# 2. Dot detection (difference of Gaussians, several sizes)
# ---------------------------------------------------------------------------

def detect_dots(gray, sigma, invert=False, border=0):
    """Blob centres at one scale. Dark-ish blobs by default (shadows / ink)."""
    src = gray.astype(np.float32) if invert else 255 - gray.astype(np.float32)
    dog = cv2.GaussianBlur(src, (0, 0), sigma) - cv2.GaussianBlur(src, (0, 0), 2.8 * sigma)
    k = max(3, int(round(3 * sigma)) | 1)
    peaks = (dog == cv2.dilate(dog, np.ones((k, k)))) & (dog > 0)
    b = max(border, k)
    peaks[:b] = peaks[-b:] = False
    peaks[:, :b] = peaks[:, -b:] = False
    ys, xs = np.nonzero(peaks)
    if len(xs) == 0:
        return np.empty((0, 2))
    v = dog[ys, xs]
    thr = max(2.0, 0.25 * np.percentile(v, 99.5))
    keep = v > thr
    xs, ys = xs[keep], ys[keep]
    if len(xs) > MAX_DOTS:          # more blobs than any page of braille: texture, not dots
        return np.empty((0, 2))
    return np.c_[xs, ys].astype(float)


# ---------------------------------------------------------------------------
# 3. Dots -> lines -> cells
# ---------------------------------------------------------------------------

CHUNK = 256   # rows per block in pairwise steps: keeps memory small on a Raspberry Pi


def _nearest(p):
    """Squared distance to, and index of, each dot's nearest neighbour."""
    p = p.astype(np.float32)
    dist2, idx = np.empty(len(p), np.float32), np.empty(len(p), int)
    for s in range(0, len(p), CHUNK):
        blk = p[s:s + CHUNK]
        d2 = ((blk[:, None, :] - p[None, :, :]) ** 2).sum(-1)
        d2[np.arange(len(blk)), np.arange(s, s + len(blk))] = np.inf   # not itself
        idx[s:s + len(blk)] = d2.argmin(1)
        dist2[s:s + len(blk)] = d2[np.arange(len(blk)), idx[s:s + len(blk)]]
    return dist2, idx


def dot_spacing(p, nearest=None):
    """Median nearest-neighbour distance = spacing of adjacent dots in a cell."""
    dist2, _ = nearest if nearest is not None else _nearest(p)
    return float(np.sqrt(np.median(dist2)))


GRID_MIN = 0.40   # measured: braille 0.64-0.92 at the right dot size; random texture 0.26-0.29


def grid_alignment(p, nearest=None):
    """Share of dots whose nearest neighbour is straight across or straight up/down
    (braille sits on a grid; texture and specks don't)."""
    _, idx = nearest if nearest is not None else _nearest(p)
    v = p[idx] - p
    th = np.arctan2(v[:, 1], v[:, 0])
    dom = np.arctan2(np.sin(4 * th).mean(), np.cos(4 * th).mean()) / 4   # page tilt
    dev = np.abs((th - dom + np.pi / 4) % (np.pi / 2) - np.pi / 4)
    return float((dev < np.radians(12)).mean())


def _components(p, d):
    """Link dots that are close sideways and within a cell's height vertically."""
    n = len(p)
    # pairs, found by sorting left-to-right and only comparing sideways neighbours
    order = np.argsort(p[:, 0], kind="stable")
    xs, ys = p[order, 0], p[order, 1]
    reach = np.searchsorted(xs, xs + 3.2 * d, side="right")   # first dot out of range
    pairs_i, pairs_j = [], []
    k = 1
    while True:
        i = np.flatnonzero(np.arange(n) + k < reach)
        if len(i) == 0:
            break
        j = i + k
        ok = np.abs(ys[j] - ys[i]) <= 1.35 * d
        pairs_i.append(order[i[ok]])
        pairs_j.append(order[j[ok]])
        k += 1
    I = np.concatenate(pairs_i) if pairs_i else np.empty(0, int)
    J = np.concatenate(pairs_j) if pairs_j else np.empty(0, int)

    # connected groups, in whole-array steps (fast on a Pi): every dot takes the smallest
    # label among its linked neighbours until nothing changes
    lab = np.arange(n)
    while len(I):
        low = np.minimum(lab[I], lab[J])
        new = lab.copy()
        np.minimum.at(new, I, low)
        np.minimum.at(new, J, low)
        new = new[new]                                 # jump ahead along chains
        if np.array_equal(new, lab):
            break
        lab = new
    order = np.argsort(lab, kind="stable")
    cuts = np.flatnonzero(np.diff(lab[order])) + 1
    return np.split(order, cuts)


def group_lines(p, d):
    comps = _components(p, d)
    comp_y = [float(np.median(p[c, 1])) for c in comps]    # each median worked out once
    lines, line_y = [], []                                  # line_y: kept up to date
    for i in sorted(range(len(comps)), key=comp_y.__getitem__):
        y = comp_y[i]
        for j, ly in enumerate(line_y):
            if abs(ly - y) < 1.3 * d:                       # word on the same line
                lines[j].extend(comps[i])
                line_y[j] = float(np.median(p[lines[j], 1]))
                break
        else:
            lines.append(list(comps[i]))
            line_y.append(y)
    return [np.array(L) for L in lines]


def fit_rows(q, d):
    """Fit a sloped 3-row grid to one line. Returns row (0-2, or -1 = stray), slope, top."""
    slopes = np.linspace(-0.08, 0.08, 81)[:, None]
    ang = 2 * np.pi * (q[None, :, 1] - slopes * q[None, :, 0]) / d
    c, s = np.cos(ang).mean(1), np.sin(ang).mean(1)
    i = int(np.argmax(np.hypot(c, s)))
    b, ph = float(slopes[i, 0]), float(np.arctan2(s[i], c[i]))

    # follow gentle curvature: local row phase from nearby dots, unwrapped along the line
    resid = q[:, 1] - b * q[:, 0]
    ang = 2 * np.pi * resid / d
    # sum over the dots within 6 dot-spacings sideways, via running sums (no n x n table)
    order = np.argsort(q[:, 0], kind="stable")
    xs = q[order, 0]
    cs_sin = np.concatenate(([0.0], np.cumsum(np.sin(ang[order]))))
    cs_cos = np.concatenate(([0.0], np.cumsum(np.cos(ang[order]))))
    lo = np.searchsorted(xs, xs - 6 * d, side="right")
    hi = np.searchsorted(xs, xs + 6 * d, side="left")
    local = np.empty(len(q))
    local[order] = np.arctan2(cs_sin[hi] - cs_sin[lo], cs_cos[hi] - cs_cos[lo])
    local[order] = np.unwrap(local[order])
    local -= 2 * np.pi * np.round((np.median(local) - ph) / (2 * np.pi))  # keep near global phase
    a_x = local * d / (2 * np.pi)
    n = np.round((resid - a_x) / d).astype(int)
    a = float(np.median(a_x))
    vals, cnt = np.unique(n, return_counts=True)
    start = max(range(n.min(), n.max() + 1),
                key=lambda s0: cnt[(vals >= s0) & (vals <= s0 + 2)].sum())
    rows = n - start
    rows[(rows < 0) | (rows > 2)] = -1
    return rows, b, a + start * d


def fit_cells(xs, d):
    """Assign each dot a cell (by its left-column x) and a column (0 = left, 1 = right)."""
    srt = np.sort(xs)
    cols = [[srt[0]]]
    for v in srt[1:]:
        if v - cols[-1][-1] <= 0.45 * d:
            cols[-1].append(v)
        else:
            cols.append([v])
    cx = np.array([np.mean(c) for c in cols])
    gaps = np.diff(cx)
    # columns ~1 dot-spacing apart are the two halves of a cell (cells are >= 1.4 apart)
    lefts = np.array(sorted(cx[i] for i in range(len(gaps)) if 0.7 * d <= gaps[i] <= 1.2 * d))

    pitch = 2.5 * d
    if len(lefts) > 1:
        diffs = np.diff(lefts)
        diffs = diffs[(diffs > 1.6 * d) & (diffs < 3.5 * d)]
        if len(diffs):
            pitch = float(np.median(diffs))

    # keep only cell starts that agree with their neighbours on the cell grid
    # (rejects a right column wrongly paired with the next cell's left column)
    if len(lefts) > 2:
        keep = []
        for L in lefts:
            near = lefts[(np.abs(lefts - L) < 8 * pitch) & (lefts != L)]
            if len(near) == 0:
                keep.append(L)
                continue
            k = (near - L) / pitch
            agree = np.abs(k - np.round(k)) * pitch < 0.3 * d
            if agree.mean() >= 0.5:
                keep.append(L)
        if keep:
            lefts = np.array(keep)

    cell, col = np.zeros(len(xs)), np.zeros(len(xs), int)
    for i, x in enumerate(xs):
        ref = lefts[np.argmin(np.abs(lefts - x))] if len(lefts) else cx[0]  # nearest known cell
        k = np.floor((x - ref + 0.45 * d) / pitch)
        off = x - ref - k * pitch
        col[i] = 0 if off < 0.5 * d else 1
        cell[i] = ref + k * pitch
    return cell, col, pitch


def dots_to_braille(p, d):
    """Return (Unicode braille text, list of cell boxes)."""
    out, boxes = [], []
    for idx in group_lines(p, d):
        q = p[idx]
        if len(q) < 3:
            continue
        rows, slope, top = fit_rows(q, d)
        ok = rows >= 0
        q, rows = q[ok], rows[ok]
        if len(q) < 3 or len(set(rows)) < 2:      # single-row lines are noise
            continue
        cell, col, pitch = fit_cells(q[:, 0], d)
        cells = {}
        for c, co, r in zip(np.round(cell).astype(int), col, rows):
            cells[c] = cells.get(c, 0) | (1 << (r + 3 * co))
        # real text is mostly multi-dot cells; a line of lone dots is stray marks
        multi = sum(bin(v).count("1") >= 2 for v in cells.values())
        if multi < 2 or multi < 0.3 * len(cells):
            continue
        s, prev = "", None
        for k in sorted(cells):
            if prev is not None and round((k - prev) / pitch) >= 2:
                s += " "
            s += chr(0x2800 + cells[k])
            prev = k
            y = top + slope * k
            boxes.append((int(k - 0.6 * d), int(y - 0.6 * d), int(k + 1.6 * d), int(y + 2.6 * d)))
        out.append((np.median(q[:, 1]), s))
    out.sort()
    return "\n".join(s for _, s in out), boxes


# ---------------------------------------------------------------------------
# 4. Orientation search + full read
# ---------------------------------------------------------------------------

def _transform(img, pts, mirror, rot):
    """Mirror (left-right) then rotate 90° counter-clockwise `rot` times — image and points together."""
    h, w = img.shape[:2]
    p = pts.copy()
    if mirror:
        img = cv2.flip(img, 1)
        p[:, 0] = w - 1 - p[:, 0]
    for _ in range(rot):
        h, w = img.shape[:2]
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        p = np.c_[p[:, 1], w - 1 - p[:, 0]]
    return img, p


def readability(text):
    """Higher = more like real words. Language-neutral (works for English, Filipino, ...)."""
    letters = sum(c.isalpha() for c in text)
    unknown = text.count("[")
    words = re.findall(r"[A-Za-z]{2,}", text)
    voweled = sum(1 for w in words if re.search("[aeiouyAEIOUY]", w))
    return letters - 3 * unknown + 2 * voweled


GOOD_ENOUGH = 20   # readability score at which a cached setting is trusted


def looks_like_text(text):
    """Real braille decodes to mostly letters/digits with few unknown cells.
    (Measured: real pages >= 0.79 letters/digits, <= 0.01 unknown; texture 0.55 / 0.18.)"""
    cells = re.findall(r"\[\d+\]|\S", text)
    if not cells:
        return False
    unknown = text.count("[") / len(cells)
    alnum = sum(c.isalnum() for c in cells if len(c) == 1) / len(cells)   # "[123]" isn't a digit
    if alnum < 0.6 or unknown > 0.12:
        return False
    # words have vowels; smudges read as dots give "ll lk kl" (English and Filipino alike)
    words = re.findall(r"[A-Za-z]{2,}", text)
    if words:
        voweled = sum(bool(re.search("[aeiouyAEIOUY]", w)) for w in words)
        if voweled / len(words) < 0.5:
            return False
    return True


def read_image(img, invert=None, hint=None):
    """Decode braille in an image.

    invert: True/False forces dot polarity; None = dark dots first, light dots if that fails.
    hint:   settings returned by a previous call (same camera setup). Tried first, which
            skips the slow orientation search when the page is in the same position.
    Returns (braille, text, annotated_image, dot_count, settings).
    """
    sheet = flatten_sheet(img)
    if sheet is not None:
        # ArUco sheet: exact geometry, page is upright, scale known (2.5 mm dots = 15 px)
        page, border = sheet[0], 4
        orientations = [(False, 0), (False, 2)]       # as printed, or slate used upside down
        sigmas = (1.4, 1.8, 2.3)
        read_image.mode = f"ArUco sheet, {sheet[2]} ({sheet[1]} of 4 markers)"
    else:
        page, border = flatten_page(img)
        orientations = [(m, r) for m in (False, True) for r in range(4)]
        sigmas = (1.2, 1.8, 2.7, 4.0, 6.0)
        read_image.mode = "no markers (auto page & orientation)"
    if hint is not None and ((hint[2], hint[3]) not in orientations or hint[1] not in sigmas
                             or (invert is not None and hint[0] != invert)):
        hint = None                     # from the other mode, or against --invert: don't reuse
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY) if page.ndim == 3 else page
    page = page if page.ndim == 3 else cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)
    cache = {}

    def dots(inv, sigma):
        if (inv, sigma) not in cache:
            pts = detect_dots(gray, sigma, inv, border)
            if len(pts) >= 6:
                near = _nearest(pts)
                cache[(inv, sigma)] = (pts, dot_spacing(pts, near), grid_alignment(pts, near))
            else:
                cache[(inv, sigma)] = (pts, 0.0, 0.0)
        return cache[(inv, sigma)][:2]

    def gridlike(inv, sigma):
        dots(inv, sigma)
        return cache[(inv, sigma)][2] >= GRID_MIN

    def try_setting(inv, sigma, mirror, rot):
        pts, d = dots(inv, sigma)
        if len(pts) < 6:
            return None
        q = _transform_points(pts, page.shape, mirror, rot)
        braille, _ = dots_to_braille(q, d)
        return readability(decode(braille))

    best = None
    if hint is not None:
        sc = try_setting(*hint)
        if sc is not None and sc >= GOOD_ENOUGH:
            best = (sc, *hint)

    if best is None:
        polarities = [False, True] if invert is None else [invert]
        for inv in polarities:
            for sigma in sigmas:
                pts, d = dots(inv, sigma)
                # braille dots sit ~2-3 dot-diameters apart; a spacing that doesn't fit
                # this blob size means we're detecting texture or dot rims, not dots
                if len(pts) < 6 or not (4.5 < d / sigma < 14) or not gridlike(inv, sigma):
                    continue
                for mirror, rot in orientations:
                    sc = try_setting(inv, sigma, mirror, rot)
                    if sc is not None and (best is None or sc > best[0]):
                        best = (sc, inv, sigma, mirror, rot)
            if best is not None and best[0] >= GOOD_ENOUGH:
                break                       # dark dots worked; skip the light-dot pass

    if best is None:
        return "", "", page, 0, None

    _, inv, sigma, mirror, rot = best
    pts, d = dots(inv, sigma)
    view, q = _transform(page, pts, mirror, rot)
    braille, boxes = dots_to_braille(q, d)
    text = decode(braille).strip()
    settings = (inv, sigma, mirror, rot)
    if not looks_like_text(text):          # don't speak gibberish (and don't cache it)
        read_image.mode += "; result doesn't look like braille, ignored"
        text, settings = "", None

    ann = view.copy()
    for x, y in q:
        cv2.circle(ann, (int(x), int(y)), max(3, int(d * 0.3)), (0, 0, 255), 1)
    for x1, y1, x2, y2 in boxes:
        cv2.rectangle(ann, (x1, y1), (x2, y2), (0, 170, 0), 1)
    return braille, text, ann, len(pts), settings


def _transform_points(pts, shape, mirror, rot):
    h, w = shape[:2]
    p = pts.copy()
    if mirror:
        p[:, 0] = w - 1 - p[:, 0]
    for _ in range(rot):
        p = np.c_[p[:, 1], w - 1 - p[:, 0]]
        w, h = h, w
    return p


# ---------------------------------------------------------------------------
# 5. Speech
# ---------------------------------------------------------------------------

SPEECH = {"voice": None, "speed": speech.SPEED,        # set from --voice / --speed /
          "line_pause": speech.LINE_PAUSE}               # --line-pause
_engine = None
_talking = None                          # background speech still playing, if any


def speak(text, wait=True):
    """Say text aloud, slowly, pausing after each line. espeak-ng first (fast on a
    Raspberry Pi), then pyttsx3, then macOS say. wait=False returns at once; the next
    speak() waits for it, so speech never overlaps."""
    global _engine, _talking
    if _talking is not None:
        _talking.wait()
        _talking = None
    if not text.strip():
        return
    if speech.ESPEAK:
        cmd = speech.espeak_cmd(text, SPEECH["speed"], SPEECH["voice"], SPEECH["line_pause"])
        if wait:
            subprocess.run(cmd, check=False)
        else:
            _talking = subprocess.Popen(cmd)
        return
    text = " ".join(text.split())
    try:
        import pyttsx3
        if _engine is None:
            _engine = pyttsx3.init()
            _engine.setProperty("rate", SPEECH["speed"])
        _engine.say(text)
        _engine.runAndWait()
        return
    except Exception:
        pass
    if shutil.which("say"):
        subprocess.run(["say", text], check=False)
        return
    print("  (no speech engine — on a Raspberry Pi: sudo apt install espeak-ng)")


# ---------------------------------------------------------------------------
# 6. Sample generator (for testing without real braille)
# ---------------------------------------------------------------------------

def make_sample(text, path, d=22, radius=7, margin=60):
    lines = [encode(line) for line in text.split("\n")]
    pitch, line_h = int(2.4 * d), int(4.2 * d)
    width = margin * 2 + pitch * max(len(l) for l in lines)
    height = margin * 2 + line_h * len(lines)
    img = np.full((height, width, 3), 245, np.uint8)
    for li, line in enumerate(lines):
        for ci, cell in enumerate(line):
            if cell == " ":
                continue
            bits = ord(cell) - 0x2800
            for b in range(6):
                if bits & (1 << b):
                    col, row = divmod(b, 3)
                    cx = margin + ci * pitch + col * d
                    cy = margin + li * line_h + row * d
                    cv2.circle(img, (cx, cy), radius, (30, 30, 30), -1, cv2.LINE_AA)
    cv2.imwrite(path, img)
    return img


# ---------------------------------------------------------------------------
# 7. Braille or print: one read
# ---------------------------------------------------------------------------

OCR_MIN_CONF = 50       # below this, OCR text is treated as "nothing found"
OCR_UNSURE_CONF = 65    # below this, the spoken text starts with a warning


def read_print(img, lang="eng"):
    """OCR a photo of printed text. Returns (text, confidence, words, annotated)."""
    import ocr_reader
    page, _ = flatten_page(img, size=None)          # full camera resolution for OCR
    return ocr_reader.read_text(page, lang)


def read_page(img, mode="auto", invert=None, hint=None, lang="eng"):
    """Read braille and/or print. mode: "auto" (braille, else print), "braille", "print".
    Returns a dict: kind ("braille" / "print" / None), text, speech, braille, ann,
    dots, settings, conf, note."""
    r = {"kind": None, "text": "", "speech": "", "braille": "", "ann": img, "dots": 0,
         "settings": None, "conf": None, "note": ""}

    if mode in ("auto", "braille"):
        braille, text, ann, n, settings = read_image(img, invert, hint)
        r.update(braille=braille, ann=ann, dots=n, settings=settings,
                 note=getattr(read_image, "mode", ""))
        if text:
            r.update(kind="braille", text=text, speech=text)
            return r
        if mode == "braille" or (mode == "auto" and read_image.mode.startswith("ArUco")):
            return r                                  # braille sheet: never OCR it

    try:
        import ocr_reader
        if not ocr_reader.available():
            r["note"] += ("; " if r["note"] else "") + "OCR not installed (sudo apt install tesseract-ocr)"
            return r
        text, conf, words, ann = read_print(img, lang)
    except Exception as err:
        r["note"] += ("; " if r["note"] else "") + f"OCR failed: {err}"
        return r
    r.update(conf=conf, note=f"printed text, {words} words, {conf:.0f}% confident")
    if text and conf >= OCR_MIN_CONF:
        warn = "This may not be accurate. " if conf < OCR_UNSURE_CONF else ""
        r.update(kind="print", text=text, speech=warn + text, ann=ann)
    return r


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

NOTHING = {"auto": "No braille or printed text found. Check the page is in view and well lit.",
           "braille": "No braille found. Check the page is in view and well lit.",
           "print": "No printed text found. Check the page is in view and well lit."}


def report(r):
    print(f"\nPage:       {r['note']}")
    if r["kind"] == "braille" or (r["kind"] is None and r["dots"]):
        print(f"Dots found: {r['dots']}")
        print("Braille:")
        print(r["braille"] or "(none)")
    print("Text:" if r["kind"] != "print" else "Printed text:")
    print(r["text"] or "(nothing recognised)")


class Camera:
    """Raspberry Pi camera (NoIR v2 etc.) through picamera2; falls back to a USB webcam."""

    def __init__(self, index=0, size=(2592, 1944)):
        self.pi = self.cap = None
        try:
            from picamera2 import Picamera2
            self.pi = Picamera2(index)
            cfg = self.pi.create_still_configuration(
                main={"size": size, "format": "RGB888"}, buffer_count=2)
            self.pi.configure(cfg)
            self.pi.start()
            time.sleep(2)                         # let exposure / white balance settle
            self.name = "Pi camera"
        except Exception as err:
            self.pi = None
            self.cap = cv2.VideoCapture(index)
            if not self.cap.isOpened():
                sys.exit(f"No camera found. Pi camera error: {err}\n"
                         "Check the ribbon cable, then run:  rpicam-hello --list-cameras")
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self.name = "USB camera"

    def frame(self):
        if self.pi is not None:
            return self.pi.capture_array("main")   # "RGB888" is B,G,R order: OpenCV-ready
        ok, f = self.cap.read()
        return f if ok else None

    def close(self):
        if self.pi is not None:
            self.pi.stop()
        if self.cap is not None:
            self.cap.release()


def run_camera(args, invert):
    cam = Camera(args.camera)
    hint = None
    has_desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    gui = bool(has_desktop) and not args.headless

    def do_read(frame, mode):
        try:
            _do_read(frame, mode)
        except Exception as err:                 # one bad frame must not stop the reader
            print(f"Read failed: {err!r}")
            if not args.quiet:
                speak("Sorry, that read failed. Please try again.")

    def _do_read(frame, mode):
        nonlocal hint
        if not args.quiet:
            speak({"auto": "Reading", "braille": "Reading braille",
                   "print": "Reading text"}[mode], wait=False)
        t0 = time.time()
        r = read_page(frame, mode, invert, hint, args.lang)
        if r["settings"] is not None:
            hint = r["settings"]                 # same mount next time: skips the slow search
        report(r)
        print(f"({time.time() - t0:.1f} s)")
        if args.debug:
            cv2.imwrite(args.debug, r["ann"])
            cv2.imwrite(os.path.splitext(args.debug)[0] + "_raw.jpg", frame)
        if gui:
            s = 800 / max(r["ann"].shape[:2])
            cv2.imshow("Detected", cv2.resize(r["ann"], None, fx=s, fy=s))
        if not args.quiet:
            speak(r["speech"] or NOTHING[mode])

    print(f"{cam.name} ready.")
    if not args.quiet:
        speak("Ready")

    if gui:
        print(f"SPACE = read ({args.mode}), B = braille, T = printed text, Q / Esc = quit.")
        while True:
            frame = cam.frame()
            if frame is None:
                break
            s = 800 / max(frame.shape[:2])
            view = cv2.resize(frame, None, fx=s, fy=s)
            cv2.putText(view, "SPACE: read  B: braille  T: text  Q: quit", (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow("Braille camera", view)
            key = cv2.waitKey(30) & 0xFF
            if key == ord(" "):
                do_read(frame, args.mode)
            elif key == ord("b"):
                do_read(frame, "braille")
            elif key == ord("t"):
                do_read(frame, "print")
            elif key in (ord("q"), 27):
                break
        cv2.destroyAllWindows()
    else:
        events = queue.Queue()
        buttons = []                                   # keep references alive
        if args.button is not None or args.print_button is not None:
            from gpiozero import Button
            for pin, mode in ((args.button, args.mode), (args.print_button, "print")):
                if pin is not None:
                    b = Button(pin, bounce_time=0.1)   # button between GPIO pin and GND
                    b.when_pressed = lambda m=mode: events.put(m)
                    buttons.append(b)
                    print(f"Button on GPIO{pin}: read {mode}.")

        def keyboard():
            for line in sys.stdin:
                t = line.strip().lower()
                events.put({"q": "quit", "b": "braille", "t": "print"}.get(t, args.mode))

        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=keyboard, daemon=True).start()
            print(f"Enter = read ({args.mode}), b + Enter = braille, t + Enter = printed text, "
                  "q + Enter = quit.")
        try:
            while True:
                ev = events.get()
                if ev == "quit":
                    break
                frame = cam.frame()
                if frame is not None:
                    do_read(frame, ev)
                while not events.empty():             # ignore presses made while busy
                    if events.get() == "quit":
                        raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
    cam.close()


def main():
    ap = argparse.ArgumentParser(description="Read braille or printed text aloud")
    ap.add_argument("--image", help="read this image instead of the camera")
    ap.add_argument("--mode", choices=("auto", "braille", "print"), default="auto",
                    help="auto (default): braille, or printed text if no braille is found")
    ap.add_argument("--lang", default="eng",
                    help="OCR languages, e.g. eng+fil (needs tesseract-ocr-fil)")
    ap.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--headless", action="store_true",
                    help="no preview window (automatic when there is no desktop)")
    ap.add_argument("--button", type=int, metavar="GPIO",
                    help="push-button on this GPIO pin (BCM number) reads in --mode")
    ap.add_argument("--print-button", type=int, metavar="GPIO",
                    help="second push-button that always reads printed text")
    ap.add_argument("--voice", help="espeak-ng voice, e.g. en-us, en-gb (espeak-ng --voices)")
    ap.add_argument("--speed", type=int, default=speech.SPEED,
                    help=f"speech words per minute (default {speech.SPEED})")
    ap.add_argument("--line-pause", type=float, default=speech.LINE_PAUSE,
                    help=f"seconds of silence after each line (default {speech.LINE_PAUSE})")
    ap.add_argument("--invert", action="store_true",
                    help="force: dots are lighter than the paper (default: auto)")
    ap.add_argument("--debug", metavar="PNG", help="save an annotated detection image")
    ap.add_argument("--quiet", action="store_true", help="print only, don't speak")
    ap.add_argument("--make-sample", metavar="PNG", help="generate a test image and exit")
    ap.add_argument("--sample-text", default="Hello world", help="text for --make-sample")
    args = ap.parse_args()
    invert = True if args.invert else None
    SPEECH["voice"], SPEECH["speed"], SPEECH["line_pause"] = args.voice, args.speed, args.line_pause

    if args.make_sample:
        make_sample(args.sample_text, args.make_sample)
        print(f"Wrote {args.make_sample}")
        return

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            sys.exit(f"Could not read {args.image}")
        print(f"Reading {args.image} ... (a few seconds on a Raspberry Pi)", flush=True)
        t0 = time.time()
        r = read_page(img, args.mode, invert, lang=args.lang)
        report(r)
        print(f"({time.time() - t0:.1f} s)")
        if args.debug:
            cv2.imwrite(args.debug, r["ann"])
            print(f"\nAnnotated image: {args.debug}")
        if not args.quiet:
            speak(r["speech"] or NOTHING[args.mode])
    else:
        run_camera(args, invert)


if __name__ == "__main__":
    main()
