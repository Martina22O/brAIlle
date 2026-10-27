#!/usr/bin/env python3
"""
speech.py - shared speech settings for every OBR program (espeak-ng).

  SPEED           words per minute.
  LINE_PAUSE      optional silence at a physical line break; zero joins wrapped lines.
  SENTENCE_PAUSE  silence between sentences, including lowercase braille text.

The --speed and --line-pause options still override their shared defaults.
"""

import re
import shutil
from xml.sax.saxutils import escape

SPEED = 140
LINE_PAUSE = 0.0
SENTENCE_PAUSE = 0.4

ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")

_SENTENCE_END = re.compile(r'''[.!?]+["')\]\u201d\u2019]*(?=\s|$)''')
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "e.g.", "i.e.", "dept.", "inc.", "etc.",
}


def _sentence_ssml(text):
    """Keep punctuation and add one explicit pause at sentence boundaries."""
    parts, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        punctuation = match.group().rstrip("\"')]\u201d\u2019")
        following = text[match.end():].strip()
        if punctuation == ".":
            word = re.search(r"[A-Za-z.]+\.$", text[:match.start() + 1])
            token = word.group() if word else ""
            # Titles, dotted acronyms and name initials are not sentence ends.
            if (token.lower() in _ABBREVIATIONS
                    or (token.lower() in {"no.", "fig."}
                        and re.match(r"\d", following))
                    or re.fullmatch(r"(?:[A-Za-z]\.){2,}", token)
                    or (re.fullmatch(r"[A-Z]\.", token)
                        and re.match(r"[A-Z]", following))):
                continue
        parts.append(escape(text[start:match.end()]))
        if following:
            parts.append(f'<break time="{int(SENTENCE_PAUSE * 1000)}ms"/>')
        start = match.end()
    parts.append(escape(text[start:]))
    return "".join(parts)


def to_ssml(text, line_pause=None):
    """Join wrapped lines by default and pause at sentence boundaries."""
    pause = LINE_PAUSE if line_pause is None else line_pause
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if pause > 0:
        gap = f'<break time="{int(pause * 1000)}ms"/>'
        body = gap.join(_sentence_ssml(line) for line in lines)
    else:
        body = _sentence_ssml(" ".join(lines))
    return "<speak>" + body + "</speak>"


def espeak_cmd(text, speed=None, voice=None, line_pause=None):
    """Build the speech command with shared speed and sentence pacing."""
    cmd = [ESPEAK, "-s", str(speed or SPEED)]
    if voice:
        cmd += ["-v", voice]
    return cmd + ["-m", to_ssml(text, line_pause)]


def characters_cmd(text, speed=None, voice=None):
    """espeak-ng command that names each character ("at sign", "b"), for symbols."""
    cmd = [ESPEAK, "-s", str(speed or SPEED)]
    if voice:
        cmd += ["-v", voice]
    return cmd + ["-m", f'<speak><say-as interpret-as="characters">{escape(text)}'
                        f'</say-as></speak>']
