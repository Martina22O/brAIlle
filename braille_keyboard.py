#!/usr/bin/env python3
"""
braille_keyboard.py — ESP32 braille keyboard for OBR: spell a keyword, press Enter,
and the Pi speaks the ready-made sentence for it.

  Type a word in braille (dot keys) ... each letter is spoken as you type it
  Enter                              ... speaks the sentence for that word
  Space                              ... adds a space (for keywords of several words)
  Backspace                          ... deletes the last cell
  Space + Backspace together         ... clears the word
  Enter with nothing typed           ... repeats the last sentence
  "help" + Enter                     ... reads out all the keywords
  A word that isn't a keyword        ... is read out as typed ("Hello" -> "Hello")
  Numbers and symbols                ... number sign + a-j for digits; , ; : . ! ? ' -
                                         and @ # $ % & * + = / ( ) (two-cell UEB forms)

Keywords and sentences are in phrases.txt (keyword = sentence). Edit it any time;
changes are picked up without restarting. A keyword with one wrong letter still
matches if it's clearly closest to one keyword.

Install on the Pi:   sudo apt install -y python3-serial espeak-ng
Run:
  python3 braille_keyboard.py                 # finds the ESP32 on USB by itself
  python3 braille_keyboard.py --port /dev/ttyUSB0
  python3 braille_keyboard.py --simulate      # no ESP32: type dot numbers instead
  python3 braille_keyboard.py --voice en-us --speed 110 --no-letters

Needs braille_reader.py in the same folder; the ESP32 runs
esp32_braille_keyboard/esp32_braille_keyboard.ino.
"""

import argparse
import difflib
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from xml.sax.saxutils import escape

import speech
from braille_reader import SYMBOL_PREFIXES, decode, encode

HELLO = "HELLO OBR-KB"
BAUD = 115200
SPACE, BACKSPACE, ENTER = 64, 128, 256          # key bits in "X:<n>" from the ESP32
HERE = os.path.dirname(os.path.abspath(__file__))


def normalise(text):
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------
# Phrase list
# ---------------------------------------------------------------------------

class Phrases:
    """keyword = sentence pairs from a text file, reloaded when the file changes."""

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.table = {}
        self.reload()

    def reload(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            if self.mtime is not None or not self.table:
                print(f"Phrase file not found: {self.path}")
            self.mtime, self.table = None, {}
            return
        if mtime == self.mtime:
            return
        table = {}
        with open(self.path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    print(f"{self.path}:{n}: skipped, no '=': {line}")
                    continue
                key, sentence = line.split("=", 1)
                key, sentence = normalise(key), sentence.strip()
                if key and sentence:
                    table[key] = sentence
        self.table, self.mtime = table, mtime
        print(f"Loaded {len(table)} phrases from {self.path}")

    def lookup(self, word):
        """(keyword, sentence) for an exact or clearly-closest match, else (None, None)."""
        self.reload()
        if word in self.table:
            return word, self.table[word]
        close = difflib.get_close_matches(word, list(self.table), n=2, cutoff=0.75)
        if not close:
            return None, None
        if len(close) == 2:
            r = [difflib.SequenceMatcher(None, word, c).ratio() for c in close]
            if r[0] - r[1] < 0.1:                   # two keywords equally close: don't guess
                return None, None
        return close[0], self.table[close[0]]


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------

class Speaker:
    """espeak-ng in the background. Sentences interrupt anything; letter echoes never
    interrupt a sentence (the listener should hear it whole)."""

    def __init__(self, voice=None, speed=None, quiet=False, line_pause=None):
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")
        self.voice, self.quiet = voice, quiet
        self.speed = speed or speech.SPEED
        self.line_pause = speech.LINE_PAUSE if line_pause is None else line_pause
        self.proc, self.proc_is_sentence = None, False
        self.gen = 0                          # counts things said (for after())
        self.lock = threading.Lock()          # buttons, keyboard and camera may all speak
        if not self.exe and not quiet:
            print("No espeak-ng found (sudo apt install espeak-ng): printing only.")

    def _busy_with_sentence(self):
        return self.proc is not None and self.proc.poll() is None and self.proc_is_sentence

    def _start(self, args, sentence, unless_sentence=False):
        with self.lock:
            if unless_sentence and self._busy_with_sentence():
                return
            self.gen += 1
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            if self.quiet or not self.exe:
                return
            cmd = [self.exe, "-s", str(self.speed)]
            if self.voice:
                cmd += ["-v", self.voice]
            self.proc = subprocess.Popen(cmd + args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
            self.proc_is_sentence = sentence

    def sentence(self, text):
        """Main output (a phrase, a page that was read): interrupts anything.
        Read slowly, with a pause after each line."""
        print(f"  >> {text}")
        self._start(["-m", speech.to_ssml(text, self.line_pause)], sentence=True)

    def spell(self, text):
        """Main output that is only symbols ("?", "@#"): say each one's name."""
        print(f"  >> {text}")
        self._start(speech.characters_cmd(text)[3:], sentence=True)

    def status(self, text):
        """Reply to a button press: interrupts anything, but typing may interrupt it."""
        print(f"  [{text}]")
        self._start([text], sentence=False)

    def after(self, text):
        """Say text once the current speech has finished, unless something else is said
        first (e.g. a button is pressed). Never cuts the current speech off."""
        with self.lock:
            gen, proc = self.gen, self.proc

        def wait_then_say():
            if proc is not None:
                proc.wait()
            with self.lock:
                if self.gen != gen:
                    return
            self.status(text)

        threading.Thread(target=wait_then_say, daemon=True).start()

    def feedback(self, text):
        print(f"  ({text})")
        self._start([text], sentence=False, unless_sentence=True)

    def letter(self, ch):
        """Say one character by its name ("b", not a word), via SSML say-as."""
        print(f"  ({ch})")
        ssml = f'<say-as interpret-as="characters">{escape(ch)}</say-as>'
        self._start(["-m", ssml], sentence=False, unless_sentence=True)

    def wait(self):
        if self.proc is not None:
            self.proc.wait()


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------

INDICATORS = {"⠠": "capital", "⠼": "number", "⠰": "letter sign"}


class Typist:
    def __init__(self, phrases, speaker, echo_letters=True):
        self.phrases, self.speaker, self.echo_letters = phrases, speaker, echo_letters
        self.cells = []
        self.last_sentence = None

    def text(self, cells=None):
        return decode("".join(self.cells if cells is None else cells))

    def handle(self, line):
        """One protocol line from the ESP32."""
        line = line.strip()
        try:
            if line.startswith("C:"):
                self.add_cell(int(line[2:]))
            elif line == "SP":
                self.cells.append(" ")
                self.speaker.feedback("space")
            elif line == "BS":
                self.backspace()
            elif line == "EN":
                self.enter()
            elif line.startswith("X:"):
                keys = int(line[2:])
                if keys == SPACE | BACKSPACE:
                    self.cells = []
                    self.speaker.feedback("cleared")
            # HELLO and anything unknown are ignored
        except ValueError:
            print(f"Ignored bad line from keyboard: {line!r}")

    def add_cell(self, bits):
        if not 1 <= bits <= 63:
            return
        cell = chr(0x2800 + bits)
        before = self.text()
        self.cells.append(cell)
        after = self.text()
        if not self.echo_letters:
            return
        new = after[len(before):] if after.startswith(before) else after[-1:]
        if cell in INDICATORS and not new:
            self.speaker.feedback(INDICATORS[cell])
        elif cell in SYMBOL_PREFIXES and new.startswith("["):
            pass                                   # first half of @ # $ %...: wait
        elif new.startswith("[") and new.endswith("]"):
            self.speaker.feedback("dots " + " ".join(new[1:-1]))      # unknown cell
        elif new:
            self.speaker.letter(new)

    def backspace(self):
        if not self.cells:
            self.speaker.feedback("empty")
            return
        gone = self.cells.pop()
        name = INDICATORS.get(gone) or ("space" if gone == " " else decode(gone).strip("[]"))
        self.speaker.feedback(f"delete {name}")

    def enter(self):
        typed = " ".join(self.text().split())      # as typed (keeps capitals, numbers, symbols)
        word = normalise(typed)                    # for looking up a keyword
        self.cells = []
        print(f"Typed: {typed!r}")
        if not word:
            if self.last_sentence:
                self.speaker.sentence(self.last_sentence)
            else:
                self.speaker.feedback("Type a word first")
            return
        if word == "help" and "help" not in self.phrases.table:
            keys = sorted(self.phrases.table)
            self.speaker.sentence("Words: " + ", ".join(keys) if keys else "The phrase list is empty")
            return
        key, sentence = self.phrases.lookup(word)
        if sentence:
            if key != word:
                print(f"  (closest keyword: {key!r})")
            self.speaker.sentence(sentence)
            self.last_sentence = sentence
            return
        # not a keyword: just read out what was typed ("Hello" -> "Hello")
        plain = " ".join(re.sub(r"\[\d+\]", " ", typed).split())   # drop unknown cells
        if not plain:
            self.speaker.feedback("Unknown braille cell. Please type it again.")
        elif not any(c.isalnum() for c in plain):
            self.speaker.spell(plain)              # only symbols: "?" -> "question mark"
            self.last_sentence = plain
        else:
            self.speaker.sentence(plain)
            self.last_sentence = plain


# ---------------------------------------------------------------------------
# ESP32 over USB serial
# ---------------------------------------------------------------------------

def candidate_ports():
    seen, out = set(), []
    for pattern in ("/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        for path in sorted(glob.glob(pattern)):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                out.append(path)
    return out


def open_keyboard(port=None, wait=4.0):
    """Open the first port that answers HELLO OBR-KB. Returns (serial, port) or (None, None)."""
    import serial
    for p in ([port] if port else candidate_ports()):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = p, BAUD, 0.25
        s.dtr = s.rts = False          # don't hold the ESP32 in reset / boot mode
        try:
            s.open()
        except (OSError, serial.SerialException):
            continue
        deadline, asked = time.time() + wait, 0.0
        try:
            while time.time() < deadline:
                if time.time() - asked > 1.0:
                    s.write(b"?\n")
                    asked = time.time()
                line = s.readline().decode("utf-8", "ignore").strip()
                if line.startswith(HELLO):
                    return s, p
        except (OSError, serial.SerialException):
            pass
        s.close()
    return None, None


def run_serial(args, typist, speaker):
    import serial
    waiting_said = False
    while True:
        s, port = open_keyboard(args.port)
        if s is None:
            if not waiting_said:
                print("Waiting for the braille keyboard (plug in the ESP32)...")
                waiting_said = True
            time.sleep(2)
            continue
        waiting_said = False
        print(f"Braille keyboard connected on {port}")
        speaker.feedback("Keyboard ready")
        try:
            while True:
                raw = s.readline()
                if raw:
                    line = raw.decode("utf-8", "ignore").strip()
                    if line:
                        typist.handle(line)
        except (OSError, serial.SerialException):
            print("Braille keyboard disconnected.")
            speaker.feedback("Keyboard disconnected")
            try:
                s.close()
            except Exception:
                pass
            time.sleep(1)


def run_simulate(typist):
    """Type on the computer keyboard: dot numbers per cell, e.g.  125 15 123 123 135 en"""
    print("Simulator: cells as dot numbers (125 = h), 'sp', 'bs', 'en', 'clr',")
    print("or w:<word> to type a whole word, e.g.  w:water en      (Ctrl+D to quit)")
    for line in sys.stdin:
        for tok in line.split():
            t = tok.lower()
            if t in ("sp", "bs", "en"):
                typist.handle(t.upper())
            elif t == "clr":
                typist.handle(f"X:{SPACE | BACKSPACE}")
            elif t.startswith("w:"):
                for cell in encode(tok[2:]):
                    typist.handle("SP" if cell == " " else f"C:{ord(cell) - 0x2800}")
            elif re.fullmatch(r"[1-6]+", t):
                typist.handle(f"C:{sum(1 << (int(d) - 1) for d in set(t))}")
            else:
                print(f"  ? {tok}")


def main():
    ap = argparse.ArgumentParser(description="ESP32 braille keyboard -> spoken sentences")
    ap.add_argument("--port", help="serial port (default: find the ESP32 automatically)")
    ap.add_argument("--phrases", default=os.path.join(HERE, "phrases.txt"),
                    help="keyword = sentence file (default: phrases.txt next to this script)")
    ap.add_argument("--voice", help="espeak-ng voice, e.g. en-us (espeak-ng --voices)")
    ap.add_argument("--speed", type=int, default=speech.SPEED,
                    help=f"speech words per minute (default {speech.SPEED})")
    ap.add_argument("--line-pause", type=float, default=speech.LINE_PAUSE,
                    help=f"seconds of silence after each line (default {speech.LINE_PAUSE})")
    ap.add_argument("--no-letters", action="store_true", help="don't speak each letter typed")
    ap.add_argument("--quiet", action="store_true", help="print only, no speech")
    ap.add_argument("--simulate", action="store_true", help="no ESP32: type dot numbers instead")
    args = ap.parse_args()

    speaker = Speaker(args.voice, args.speed, args.quiet, args.line_pause)
    typist = Typist(Phrases(args.phrases), speaker, echo_letters=not args.no_letters)
    try:
        if args.simulate:
            run_simulate(typist)
            speaker.wait()
        else:
            run_serial(args, typist, speaker)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
