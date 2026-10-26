#!/usr/bin/env python3
"""
obr_main.py — the whole OBR device in one program, driven by three buttons on the Pi.

  OBR  button (GPIO17)  press once: "Braille mode, place the page"
                        press again: photograph the page and read the BRAILLE aloud
  OCR  button (GPIO27)  same, for PRINTED TEXT
                        after each read it asks again: "Press OBR for braille, or OCR for text"
  TYPE button (GPIO22)  turn the ESP32 braille keyboard on / off
                        (keyword + Enter speaks its sentence from phrases.txt)

  After the first press you have 30 s to press again (--arm-timeout); pressing the
  other camera button switches mode. --one-press reads on the first press instead.

Each button connects its GPIO pin to GND (no resistors needed):

  GPIO17 = physical pin 11     GPIO27 = pin 13     GPIO22 = pin 15     GND = pin 9 or 14

Run:
  python3 obr_main.py                         # buttons, camera and ESP32 keyboard
  python3 obr_main.py --obr-pin 5 --ocr-pin 6 --type-pin 13   # other pins (BCM numbers)
  python3 obr_main.py --type-off              # start with the keyboard switched off
  python3 obr_main.py --lang eng+fil          # Filipino printed text too

With --preview on the Pi desktop (or VNC), a live camera window opens: it shows the
mode, whether the page is found, and a Focus meter (turn the lens until it's highest).
Over plain SSH there is no window.

Without buttons (testing), type a letter + Enter in the terminal instead:
  b = OBR (braille)   t = OCR (printed text)   k = TYPE (keyboard on/off)   q = quit

Use this instead of obr.service + obr-keyboard.service, not together with them:
the camera and the ESP32's USB port can only be used by one program at a time.
"""

import argparse
import os
import queue
import sys
import threading
import time

import braille_capture as bc
import speech
from braille_keyboard import Phrases, Speaker, Typist, open_keyboard

HERE = os.path.dirname(os.path.abspath(__file__))
CHOOSE = "Press O B R for braille, or O C R for text."          # spoken after every read
BUTTON_NAME = {"braille": "O B R", "print": "O C R"}


class OBR:
    def __init__(self, args):
        self.args = args
        self.speaker = Speaker(args.voice, args.speed, args.quiet, args.line_pause)
        self.typist = Typist(Phrases(args.phrases), self.speaker,
                             echo_letters=not args.no_letters)
        self.type_mode = not args.type_off
        self.reads = queue.Queue()                 # camera jobs: "braille" / "print" / "quit"
        self.busy = threading.Event()              # a camera read is running
        self.armed = None                          # "braille" / "print" after a first press
        self.armed_at = 0.0
        self.lock = threading.Lock()
        self.hint = None                           # remembered braille orientation
        self.keyboard_connected = False
        self.keyboard_checked = threading.Event()  # first look for the ESP32 is done
        self.off_warned_at = 0.0
        self.buttons = []                          # keep gpiozero objects alive

    # ---- buttons ---------------------------------------------------------

    def press(self, what):
        """A button (or terminal key) was pressed. Runs in the button's own thread."""
        if what == "type":
            self.type_mode = not self.type_mode
            self.typist.cells = []
            if self.type_mode:
                self.speaker.status("Type mode on" if self.keyboard_connected
                                    else "Type mode on. The keyboard is not connected.")
            else:
                self.speaker.status("Type mode off")
        elif what in ("braille", "print"):
            with self.lock:
                if self.busy.is_set():
                    print("  (still reading; press ignored)")
                    return
                name = "Braille" if what == "braille" else "Text"
                if self.args.one_press or (self.armed == what and
                                           time.time() - self.armed_at < self.args.arm_timeout):
                    self.armed = None              # second press: take the picture now
                    self.busy.set()
                    self.reads.put(what)
                    return
                self.armed, self.armed_at = what, time.time()   # first press: get ready
            self.speaker.status(f"{name} mode. Place the page, then press "
                                f"{BUTTON_NAME[what]} again.")
            threading.Timer(self.args.arm_timeout, self.disarm, args=(what, self.armed_at)).start()
        elif what == "quit":
            self.reads.put("quit")

    def disarm(self, what, armed_at):
        """Nobody pressed a second time: cancel, so a later press starts afresh."""
        with self.lock:
            if self.armed == what and self.armed_at == armed_at and not self.busy.is_set():
                self.armed = None
            else:
                return
        self.speaker.status("Cancelled.")
        self.ask()

    def ask(self):
        """Ask which kind of reading is next (spoken once the current speech ends)."""
        print("Choose: b = braille, t = printed text   (k = type mode, q = quit)", flush=True)
        self.speaker.after(CHOOSE)

    def setup_buttons(self):
        pins = {"braille": self.args.obr_pin, "print": self.args.ocr_pin,
                "type": self.args.type_pin}
        try:
            from gpiozero import Button
        except ImportError:
            print("gpiozero not available: no buttons (sudo apt install python3-gpiozero).")
            return False
        try:
            for what, pin in pins.items():
                b = Button(pin, bounce_time=0.1)   # pin to GND, internal pull-up
                b.when_pressed = lambda w=what: self.press(w)
                self.buttons.append(b)
        except Exception as err:                   # not a Pi, or pin already in use
            print(f"Buttons not available: {err}")
            return False
        names = {"braille": "OBR (braille)", "print": "OCR (printed text)",
                 "type": "TYPE (keyboard on/off)"}
        for what, pin in pins.items():
            print(f"  GPIO{pin}: {names[what]}")
        return True

    def terminal_keys(self):
        keys = {"b": "braille", "t": "print", "k": "type", "q": "quit"}
        for line in sys.stdin:
            what = keys.get(line.strip().lower())
            if what:
                self.press(what)
            else:
                print("  b = braille, t = printed text, k = type mode, q = quit")
        self.press("quit")

    # ---- ESP32 keyboard --------------------------------------------------

    def keyboard_loop(self):
        import serial
        waiting_said = False
        while True:
            s, port = open_keyboard(self.args.port)
            first_look = not self.keyboard_checked.is_set()
            if s is None:
                self.keyboard_checked.set()
                if not waiting_said:
                    print("Waiting for the ESP32 braille keyboard...")
                    waiting_said = True
                time.sleep(2)
                continue
            waiting_said = False
            self.keyboard_connected = True
            self.keyboard_checked.set()
            print(f"Braille keyboard connected on {port}")
            if not first_look:                     # at start-up the welcome message says it
                self.speaker.feedback("Keyboard ready")
            try:
                while True:
                    raw = s.readline()
                    line = raw.decode("utf-8", "ignore").strip() if raw else ""
                    if not line or line.startswith("HELLO"):
                        continue
                    if self.type_mode:
                        self.typist.handle(line)
                    elif time.time() - self.off_warned_at > 5:     # don't nag every key
                        self.off_warned_at = time.time()
                        self.speaker.feedback("Type mode is off. Press the type button.")
            except (OSError, serial.SerialException):
                self.keyboard_connected = False
                print("Braille keyboard disconnected.")
                self.speaker.feedback("Keyboard disconnected")
                try:
                    s.close()
                except Exception:
                    pass
                time.sleep(1)

    # ---- camera ----------------------------------------------------------

    def read(self, cam, mode):
        frame = cam.frame()
        if frame is None:
            print("Read failed: no picture from the camera")
            self.speaker.sentence("Sorry, the camera gave no picture. Please try again.")
            self.busy.clear()
            self.ask()
            return
        self.read_frame(frame, mode)

    def read_frame(self, frame, mode):
        self.speaker.status("Capturing. Reading braille" if mode == "braille"
                            else "Capturing. Reading text")
        try:
            t0 = time.time()
            r = bc.read_page(frame, mode, None, self.hint, self.args.lang)
            if r["settings"] is not None:
                self.hint = r["settings"]
            bc.report(r)
            print(f"({time.time() - t0:.1f} s)")
            if self.args.debug:
                import cv2
                cv2.imwrite(self.args.debug, r["ann"])
                cv2.imwrite(os.path.splitext(self.args.debug)[0] + "_raw.jpg", frame)
            self.speaker.sentence(r["speech"] or bc.NOTHING[mode])
        except Exception as err:                   # one bad read must not stop the device
            print(f"Read failed: {err!r}")
            self.speaker.sentence("Sorry, that read failed. Please try again.")
        finally:
            self.busy.clear()
            self.ask()                             # back to the start: ask again

    # ---- live camera window (Pi desktop / VNC) -----------------------------

    def state_text(self):
        if self.busy.is_set():
            return "READING...  please wait", (0, 200, 255)
        if self.armed == "braille":
            return "BRAILLE mode: place the page, press OBR (b) again", (0, 255, 0)
        if self.armed == "print":
            return "TEXT mode: place the page, press OCR (t) again", (0, 255, 0)
        return "Ready: OBR (b) = braille, OCR (t) = text", (255, 255, 255)

    def preview_loop(self, cam):
        """Live camera window with mode, page outline and a focus meter. Reads run in
        the background so the picture stays live."""
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")   # Pi desktop (Wayland): use X
        import cv2
        win = "OBR camera  (b = braille, t = text, k = type mode, q = quit)"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        best, n, page = 0.0, 0, None
        print("Camera window open. Turn the lens until 'Focus' is as high as it goes.")
        while True:
            frame = cam.frame()
            if frame is None:
                break
            try:
                job = self.reads.get_nowait()
            except queue.Empty:
                job = None
            if job == "quit":
                break
            if job:                                # capture this exact frame and read it
                threading.Thread(target=self.read_frame, args=(frame.copy(), job),
                                 daemon=True).start()

            scale = 960 / max(frame.shape[:2])
            view = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            h, w = view.shape[:2]
            gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
            centre = gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
            focus = float(cv2.Laplacian(centre, cv2.CV_64F).var())   # higher = sharper
            best = max(best, focus)
            if n % 3 == 0:                         # page outline, a few times a second
                page = bc.find_paper(view)
            n += 1

            if page is not None:
                cv2.polylines(view, [page.astype(int)], True, (0, 255, 0), 2)
            text, colour = self.state_text()
            cv2.rectangle(view, (0, 0), (w, 34), (0, 0, 0), -1)
            cv2.putText(view, text, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
            cv2.rectangle(view, (0, h - 58), (w, h), (0, 0, 0), -1)
            bar = int((w - 20) * min(1.0, focus / max(best, 1e-6)))
            cv2.rectangle(view, (10, h - 18), (10 + bar, h - 8), (0, 255, 255), -1)
            cv2.putText(view, f"Focus {focus:6.0f}   (best {best:.0f})    "
                              f"Page: {'found' if page is not None else 'not found'}",
                        (10, h - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.rectangle(view, (w // 4, h // 4), (3 * w // 4, 3 * h // 4), (90, 90, 90), 1)
            cv2.imshow(win, view)

            key = cv2.waitKey(30) & 0xFF
            what = {ord("b"): "braille", ord("t"): "print", ord("k"): "type",
                    ord("q"): "quit", 27: "quit"}.get(key)
            if what == "quit":
                break
            if what:
                self.press(what)
            if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:   # window closed
                break
        cv2.destroyAllWindows()

    # ---- main ------------------------------------------------------------

    def run(self):
        try:
            cam = bc.Camera(self.args.camera)
        except SystemExit as err:                  # no camera: say so out loud, then stop
            print(err)
            self.speaker.sentence("Camera not found. Check the camera cable.")
            self.speaker.wait()
            sys.exit(1)                            # the start-up service tries again
        print(f"{cam.name} ready.")
        has_buttons = self.setup_buttons()
        if sys.stdin and sys.stdin.isatty() or not has_buttons:
            threading.Thread(target=self.terminal_keys, daemon=True).start()
            print("Terminal: b = braille, t = printed text, k = type mode, q = quit")
        if not self.args.no_keyboard:
            threading.Thread(target=self.keyboard_loop, daemon=True).start()
            self.keyboard_checked.wait(timeout=8)  # one welcome message, not two
            kb = " Keyboard connected." if self.keyboard_connected else " Keyboard not connected."
        else:
            kb = ""
        self.speaker.sentence("O B R ready." + kb + " Type mode " +
                              ("on." if self.type_mode else "off."))
        self.ask()
        desktop = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        try:
            if self.args.preview and desktop:
                self.preview_loop(cam)             # live window; reads run alongside
            else:
                while True:
                    job = self.reads.get()
                    if job == "quit":
                        break
                    self.read(cam, job)
        except KeyboardInterrupt:
            print()
        finally:
            cam.close()
            self.speaker.wait()


def main():
    ap = argparse.ArgumentParser(description="OBR: braille + printed text + braille keyboard")
    ap.add_argument("--obr-pin", type=int, default=17, help="OBR (braille) button, BCM pin")
    ap.add_argument("--ocr-pin", type=int, default=27, help="OCR (printed text) button, BCM pin")
    ap.add_argument("--type-pin", type=int, default=22, help="TYPE (keyboard) button, BCM pin")
    ap.add_argument("--type-off", action="store_true", help="start with type mode off")
    ap.add_argument("--one-press", action="store_true",
                    help="OBR / OCR read on the first press (no 'press again')")
    ap.add_argument("--arm-timeout", type=float, default=30,
                    help="seconds to wait for the second press (default 30)")
    ap.add_argument("--no-keyboard", action="store_true", help="don't look for the ESP32")
    ap.add_argument("--port", help="ESP32 serial port (default: find it automatically)")
    ap.add_argument("--phrases", default=os.path.join(HERE, "phrases.txt"))
    ap.add_argument("--lang", default="eng", help="OCR languages, e.g. eng+fil")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--voice", help="espeak-ng voice, e.g. en-us")
    ap.add_argument("--speed", type=int, default=speech.SPEED,
                    help=f"speech words per minute (default {speech.SPEED})")
    ap.add_argument("--line-pause", type=float, default=speech.LINE_PAUSE,
                    help=f"seconds of silence after each line (default {speech.LINE_PAUSE})")
    ap.add_argument("--no-letters", action="store_true", help="don't speak each typed letter")
    ap.add_argument("--debug", metavar="PNG", help="save what the camera saw on each read")
    ap.add_argument("--preview", action="store_true",
                    help="live camera window with focus meter (Pi desktop / VNC only)")
    ap.add_argument("--quiet", action="store_true", help="print only, no speech")
    OBR(ap.parse_args()).run()


if __name__ == "__main__":
    main()
