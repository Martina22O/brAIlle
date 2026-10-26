#!/usr/bin/env python3
"""
ocr_reader.py — read printed text (labels, letters, book pages) from a camera image
with Tesseract OCR. Used by braille_capture.py; also runs on its own:

  python3 ocr_reader.py photo.jpg [--lang eng+fil]

Install on the Pi:  sudo apt install -y tesseract-ocr
Filipino:           sudo apt install -y tesseract-ocr-fil   then use --lang eng+fil
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

TESSERACT = shutil.which("tesseract")
MIN_WORD_CONF = 40          # words Tesseract is less sure of than this are dropped
TIME_LIMIT = 25             # seconds for a whole read (both passes); longer = unreadable photo


def available():
    return TESSERACT is not None


def _run(args, timeout=None):
    """Tesseract's output, or "" if it gives up or takes too long."""
    timeout = timeout or TIME_LIMIT
    if timeout < 1:
        return ""
    try:
        r = subprocess.run([TESSERACT] + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  (OCR stopped after {timeout:.0f} s: the photo is probably too blurry)")
        return ""
    return r.stdout


def even_lighting(gray):
    """Divide out the slow brightness changes (shadows, uneven lamp light)."""
    k = max(31, (min(gray.shape) // 15) | 1)
    bg = cv2.GaussianBlur(gray, (k, k), 0)
    flat = cv2.divide(gray, bg, scale=255)
    return cv2.normalize(flat, None, 0, 255, cv2.NORM_MINMAX)


def fix_rotation(gray, path, timeout=None):
    """Use Tesseract's orientation detection to turn the page upright."""
    out = _run([path, "stdout", "--psm", "0"], timeout)
    rot = re.search(r"Rotate:\s*(\d+)", out)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not rot or not conf or float(conf.group(1)) < 1.0:
        return gray, 0
    deg = int(rot.group(1)) % 360
    turn = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(deg)
    return (cv2.rotate(gray, turn) if turn is not None else gray), deg


def _parse_tsv(tsv):
    """Words from Tesseract TSV, grouped back into lines and paragraphs."""
    words = []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        text, conf = f[11].strip(), float(f[10])
        if text:
            words.append({"block": int(f[2]), "par": int(f[3]), "line": int(f[4]),
                          "x": int(f[6]), "y": int(f[7]), "w": int(f[8]), "h": int(f[9]),
                          "conf": conf, "text": text})
    return words


def _junk(word):
    """Specks and smudges OCR'd as stray symbols."""
    letters = sum(c.isalnum() for c in word)
    return letters == 0 or (len(word) >= 3 and letters / len(word) < 0.5)


def tidy(words):
    """Speakable text: confident words, one printed line per line (so speech can pause
    after each), with words split by a hyphen at a line end joined back together."""
    lines, cur, key = [], [], None
    for w in words:
        if w["conf"] < MIN_WORD_CONF or _junk(w["text"]):
            continue
        k = (w["block"], w["par"], w["line"])
        if key is not None and k != key and cur:
            lines.append(" ".join(cur))
            cur = []
        key = k
        cur.append(w["text"])
    if cur:
        lines.append(" ".join(cur))
    out = []
    for line in lines:
        if out and re.search(r"\w-$", out[-1]) and re.match(r"\w", line):
            first, _, rest = line.partition(" ")       # "read-" + "ing more" -> "reading"
            out[-1] = out[-1][:-1] + first
            if rest:
                out.append(rest)
        else:
            out.append(line)
    return "\n".join(out)


def read_text(img, lang="eng"):
    """OCR a (flattened) page image. Returns (text, mean confidence 0-100, word count,
    annotated image)."""
    if not available():
        raise RuntimeError("Tesseract is not installed (sudo apt install tesseract-ocr)")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if max(gray.shape) < 1600:                       # Tesseract likes ~300 dpi text
        s = 1600 / max(gray.shape)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    gray = even_lighting(gray)

    deadline = time.time() + TIME_LIMIT                 # one budget for both passes
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        cv2.imwrite(path, gray)
        gray, _ = fix_rotation(gray, path, 0.4 * TIME_LIMIT)
        cv2.imwrite(path, gray)
        words = _parse_tsv(_run([path, "stdout", "-l", lang, "--psm", "3", "tsv"],
                                deadline - time.time()))
    finally:
        os.remove(path)

    good = [w for w in words if w["conf"] >= MIN_WORD_CONF and not _junk(w["text"])]
    conf = float(np.mean([w["conf"] for w in good])) if good else 0.0
    ann = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for w in words:
        ok = w["conf"] >= MIN_WORD_CONF and not _junk(w["text"])
        cv2.rectangle(ann, (w["x"], w["y"]), (w["x"] + w["w"], w["y"] + w["h"]),
                      (0, 170, 0) if ok else (0, 0, 255), 1)
    return tidy(words), conf, len(good), ann


def main():
    ap = argparse.ArgumentParser(description="Read printed text from an image")
    ap.add_argument("image")
    ap.add_argument("--lang", default="eng", help="Tesseract languages, e.g. eng+fil")
    args = ap.parse_args()
    img = cv2.imread(args.image)
    if img is None:
        sys.exit(f"Could not read {args.image}")
    text, conf, n, _ = read_text(img, args.lang)
    print(f"{n} words, {conf:.0f}% confident\n")
    print(text or "(no text found)")


if __name__ == "__main__":
    main()
