#!/usr/bin/env python3
"""
test_buttons.py — check the OBR, OCR and TYPE buttons on the Raspberry Pi.

  python3 test_buttons.py                 # default pins: OBR=GPIO17, OCR=GPIO27, TYPE=GPIO22
  python3 test_buttons.py --speak         # also say the button's name out loud
  python3 test_buttons.py --obr-pin 5 --ocr-pin 6 --type-pin 13

Press each button: the terminal shows it go DOWN and UP. Press Ctrl+C to finish and
see which buttons worked.

Wiring: each button goes between its GPIO pin and GND (pin 9 or 14). No resistors.
  OBR  = GPIO17 = physical pin 11
  OCR  = GPIO27 = physical pin 13
  TYPE = GPIO22 = physical pin 15
"""

import argparse
import shutil
import subprocess
import sys
import time

PHYSICAL = {17: 11, 27: 13, 22: 15, 5: 29, 6: 31, 13: 33, 19: 35, 26: 37,
            23: 16, 24: 18, 25: 22, 12: 32, 16: 36, 20: 38, 21: 40, 4: 7, 18: 12}


def pin_name(gpio):
    phys = PHYSICAL.get(gpio)
    return f"GPIO{gpio}" + (f" (pin {phys})" if phys else "")


def main():
    ap = argparse.ArgumentParser(description="Check the OBR / OCR / TYPE buttons")
    ap.add_argument("--obr-pin", type=int, default=17)
    ap.add_argument("--ocr-pin", type=int, default=27)
    ap.add_argument("--type-pin", type=int, default=22)
    ap.add_argument("--speak", action="store_true", help="say each button's name (espeak-ng)")
    args = ap.parse_args()

    buttons_wanted = [("OBR", args.obr_pin), ("OCR", args.ocr_pin), ("TYPE", args.type_pin)]
    if len({p for _, p in buttons_wanted}) < 3:
        sys.exit("Two buttons are set to the same pin. Give each button its own GPIO.")

    try:
        from gpiozero import Button
    except ImportError:
        sys.exit("gpiozero is missing. Install it with:  sudo apt install -y python3-gpiozero")

    espeak = shutil.which("espeak-ng") or shutil.which("espeak") if args.speak else None
    if args.speak and not espeak:
        print("(no espeak-ng: install with  sudo apt install -y espeak-ng ; printing only)")

    t0 = time.time()
    presses = {name: 0 for name, _ in buttons_wanted}
    down_at = {}

    def stamp():
        return f"[{time.time() - t0:6.1f}s]"

    def on_press(name, gpio):
        presses[name] += 1
        down_at[name] = time.time()
        print(f"{stamp()}  {name:<4}  DOWN   {pin_name(gpio)}   (press #{presses[name]})",
              flush=True)
        if espeak:
            subprocess.Popen([espeak, name if name != "OBR" else "O B R"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def on_release(name, gpio):
        held = time.time() - down_at.pop(name, time.time())
        print(f"{stamp()}  {name:<4}  up     {pin_name(gpio)}   (held {held:.2f}s)", flush=True)

    buttons = {}
    for name, gpio in buttons_wanted:
        try:
            b = Button(gpio, pull_up=True, bounce_time=0.05)
        except Exception as err:
            msg = str(err) or type(err).__name__
            print(f"\nCould not use {pin_name(gpio)} for {name}: {msg}")
            if "busy" in msg.lower() or "in use" in msg.lower():
                print("Another program is using the pin. If OBR is running as a service, stop it:\n"
                      "  sudo systemctl stop obr-main obr obr-keyboard")
            sys.exit(1)
        b.when_pressed = lambda n=name, g=gpio: on_press(n, g)
        b.when_released = lambda n=name, g=gpio: on_release(n, g)
        buttons[name] = (b, gpio)

    print("Button test. Press each button once (Ctrl+C to finish).\n")
    for name, (b, gpio) in buttons.items():
        state = "PRESSED already!" if b.is_pressed else "ok, not pressed"
        print(f"  {name:<4} on {pin_name(gpio):<18} {state}")
    stuck = [n for n, (b, _) in buttons.items() if b.is_pressed]
    if stuck:
        print("\n  A button reading 'pressed' before you touch it usually means its pin is")
        print("  wired straight to GND, the button is a 'normally closed' type, or the two")
        print("  legs used are always connected (on 4-leg buttons, use DIAGONAL legs).")
    print()

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass

    print("\n\nResult:")
    all_ok = True
    for name, (b, gpio) in buttons.items():
        if presses[name]:
            print(f"  {name:<4} {pin_name(gpio):<18} WORKING  ({presses[name]} press{'es' if presses[name] != 1 else ''})")
        else:
            all_ok = False
            print(f"  {name:<4} {pin_name(gpio):<18} NO PRESS SEEN")
    if all_ok:
        print("\nAll three buttons work. You can run:  python3 obr_main.py")
    else:
        print("\nFor a button that was never seen:")
        print("  - check its wire is on the right pin (GPIO number is not the pin number:")
        print("    GPIO17 = pin 11, GPIO27 = pin 13, GPIO22 = pin 15)")
        print("  - check its other leg goes to a Ground pin (9 or 14)")
        print("  - on 4-leg buttons, use two DIAGONAL legs (pairs on one side are joined)")
        print("  - a breadboard's two halves aren't connected across the middle gap")


if __name__ == "__main__":
    main()
