#!/usr/bin/env python3
"""
braille_reader.py — translate Grade 1 (uncontracted) English braille to text and back.

Supports:
  * Unicode braille cells (U+2800–U+28FF), e.g. "⠠⠓⠑⠇⠇⠕"
  * Dot-number notation, e.g. "6 125 15 123 123 135" (cells separated by spaces, "/" = word gap)
  * Capital sign (⠠), capital-word sign (⠠⠠), number sign (⠼), common punctuation
  * Symbols @ # $ % & * + = / ( ) as their two-cell UEB forms (e.g. @ = ⠈⠁)

Usage:
  python braille_reader.py "⠠⠓⠑⠇⠇⠕ ⠺⠕⠗⠇⠙⠖"
  python braille_reader.py --dots "6 125 15 123 123 135"
  python braille_reader.py --encode "Hello, world 2026!"
  python braille_reader.py            # interactive mode
"""

import argparse
import sys

# --- Cell tables -------------------------------------------------------------

LETTERS = {
    "a": "⠁", "b": "⠃", "c": "⠉", "d": "⠙", "e": "⠑",
    "f": "⠋", "g": "⠛", "h": "⠓", "i": "⠊", "j": "⠚",
    "k": "⠅", "l": "⠇", "m": "⠍", "n": "⠝", "o": "⠕",
    "p": "⠏", "q": "⠟", "r": "⠗", "s": "⠎", "t": "⠞",
    "u": "⠥", "v": "⠧", "w": "⠺", "x": "⠭", "y": "⠽", "z": "⠵",
}
PUNCTUATION = {
    ",": "⠂", ";": "⠆", ":": "⠒", ".": "⠲", "!": "⠖",
    "?": "⠦", "'": "⠄", "-": "⠤",
}
DIGITS = {str((i + 1) % 10): LETTERS[c] for i, c in enumerate("abcdefghij")}
# common symbols, as two cells (Unified English Braille)
SYMBOLS = {
    "@": "⠈⠁", "#": "⠸⠹", "$": "⠈⠎", "%": "⠨⠴", "&": "⠈⠯", "*": "⠐⠔",
    "+": "⠐⠖", "=": "⠐⠶", "/": "⠸⠌", "(": "⠐⠣", ")": "⠐⠜",
}

CAPITAL = "⠠"
NUMBER = "⠼"
LETTER_IND = "⠰"   # grade-1 indicator: next a–j is a letter, not a digit
BLANK = "⠀"

CELL_TO_LETTER = {v: k for k, v in LETTERS.items()}
CELL_TO_PUNCT = {v: k for k, v in PUNCTUATION.items()}
CELL_TO_DIGIT = {v: k for k, v in DIGITS.items()}
CELLS_TO_SYMBOL = {v: k for k, v in SYMBOLS.items()}
SYMBOL_PREFIXES = {v[0] for v in SYMBOLS.values()}      # ⠈ ⠐ ⠸ ⠨


# --- Dot notation ------------------------------------------------------------

def dots_to_cell(dots: str) -> str:
    """'125' -> '⠓'. Dots 1-8 map to bits 0-7 of U+2800."""
    code = 0
    for d in dots.strip():
        if d not in "12345678":
            raise ValueError(f"invalid dot '{d}' in '{dots}'")
        code |= 1 << (int(d) - 1)
    return chr(0x2800 + code)


def cell_to_dots(cell: str) -> str:
    """'⠓' -> '125'."""
    code = ord(cell) - 0x2800
    return "".join(str(i + 1) for i in range(8) if code & (1 << i)) or "0"


def parse_dots(text: str) -> str:
    """'6 125 15 / 1 2' -> Unicode braille. '/' or '0' marks a blank cell."""
    out = []
    for tok in text.split():
        out.append(" " if tok in ("/", "0") else dots_to_cell(tok))
    return "".join(out)


# --- Decoding ----------------------------------------------------------------

def decode(braille: str) -> str:
    out = []
    cap_next = cap_word = number_mode = letter_next = False
    i = 0
    while i < len(braille):
        ch = braille[i]

        pair = braille[i:i + 2]
        if pair in CELLS_TO_SYMBOL:                 # @ # $ % & * + = / ( )
            out.append(CELLS_TO_SYMBOL[pair])
            number_mode = cap_next = letter_next = False
            i += 2
            continue
        if ch in (" ", BLANK, "\n", "\t"):
            out.append("\n" if ch == "\n" else " ")
            cap_next = cap_word = number_mode = letter_next = False
        elif ch == CAPITAL:
            if i + 1 < len(braille) and braille[i + 1] == CAPITAL:
                cap_word = True
                i += 1
            else:
                cap_next = True
        elif ch == NUMBER:
            number_mode = True
        elif ch == LETTER_IND:
            letter_next = True
            number_mode = False
        elif number_mode and ch in CELL_TO_DIGIT and not letter_next:
            out.append(CELL_TO_DIGIT[ch])
        elif ch in CELL_TO_LETTER:
            number_mode = False
            c = CELL_TO_LETTER[ch]
            out.append(c.upper() if (cap_next or cap_word) else c)
            cap_next = letter_next = False
        elif ch in CELL_TO_PUNCT:
            p = CELL_TO_PUNCT[ch]
            # "." and "," inside a number (3.14, 1,000) keep number mode
            if not (number_mode and p in ".,"):
                number_mode = False
            out.append(p)
        elif "\u2800" <= ch <= "\u28ff":
            out.append(f"[{cell_to_dots(ch)}]")   # unknown cell: show its dots
        else:
            out.append(ch)                       # pass through non-braille
        i += 1
    return "".join(out)


# --- Encoding ----------------------------------------------------------------

def encode(text: str) -> str:
    out = []
    for word in text.split(" "):
        letters = [c for c in word if c.isalpha()]
        all_caps = len(letters) > 1 and all(c.isupper() for c in letters)
        if all_caps:
            out.append(CAPITAL * 2)
        number_mode = False
        for c in word:
            if c.isdigit():
                if not number_mode:
                    out.append(NUMBER)
                    number_mode = True
                out.append(DIGITS[c])
            elif c.isalpha():
                low = c.lower()
                if low not in LETTERS:
                    out.append(c)
                    continue
                if number_mode and low in "abcdefghij":
                    out.append(LETTER_IND)
                number_mode = False
                if c.isupper() and not all_caps:
                    out.append(CAPITAL)
                out.append(LETTERS[low])
            elif c in PUNCTUATION:
                if not (number_mode and c in ".,"):
                    number_mode = False
                out.append(PUNCTUATION[c])
            elif c in SYMBOLS:
                number_mode = False
                out.append(SYMBOLS[c])
            else:
                out.append(c)
        out.append(" ")
    return "".join(out).rstrip(" ")


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Grade 1 braille reader/writer")
    ap.add_argument("text", nargs="?", help="braille (or plain text with --encode)")
    ap.add_argument("--dots", action="store_true", help="input is dot numbers, e.g. '6 125 15'")
    ap.add_argument("--encode", action="store_true", help="convert plain text to braille")
    ap.add_argument("--show-dots", action="store_true", help="with --encode, also print dot numbers")
    args = ap.parse_args()

    def run(s: str) -> None:
        if args.encode:
            b = encode(s)
            print(b)
            if args.show_dots:
                print(" ".join("/" if c == " " else cell_to_dots(c) for c in b))
        else:
            print(decode(parse_dots(s) if args.dots else s))

    if args.text is not None:
        run(args.text)
    elif not sys.stdin.isatty():
        run(sys.stdin.read().rstrip("\n"))
    else:
        mode = "text" if args.encode else ("dot numbers" if args.dots else "braille")
        print(f"Enter {mode} (Ctrl+D / Ctrl+Z to quit):")
        try:
            while True:
                run(input("> "))
        except (EOFError, KeyboardInterrupt):
            print()


if __name__ == "__main__":
    main()
