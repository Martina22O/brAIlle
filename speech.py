#!/usr/bin/env python3
"""
speech.py — shared speech settings for every OBR program (espeak-ng).

  SPEED       words per minute. espeak-ng's own default is 175; OBR reads slower.
  LINE_PAUSE  seconds of silence after each line, so lines are easier to follow.

Change the two numbers below to adjust all programs at once, or use the
--speed / --line-pause options of a program.
"""

import shutil
from xml.sax.saxutils import escape

SPEED = 120          # words per minute
LINE_PAUSE = 0.8     # seconds of silence after each line

ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")


def to_ssml(text, line_pause=None):
    """Text -> SSML with a pause after every non-empty line."""
    pause = LINE_PAUSE if line_pause is None else line_pause
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    gap = f'<break time="{int(pause * 1000)}ms"/>' if pause > 0 else " "
    return "<speak>" + gap.join(escape(line) for line in lines) + "</speak>"


def espeak_cmd(text, speed=None, voice=None, line_pause=None):
    """espeak-ng command that reads `text` slowly, pausing after each line."""
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
