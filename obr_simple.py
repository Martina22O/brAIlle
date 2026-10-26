#!/usr/bin/env python3
"""
obr_simple.py — live camera window + button on GPIO17: press to read BRAILLE aloud.

The braille partner of ocr_simple.py. It uses the ArUco marker sheet
(braille_sheet_sq180.pdf or braille_sheet_a4.pdf) automatically when the camera
sees at least 3 of its markers: exact flattening, scale and "which way is up", so
the read is fast and reliable. Without markers it still works, just slower.

The live window shows:
  - each ArUco marker it can see (green outline) and "Markers 3/4", "4/4" ...
  - READY when at least 3 markers are visible: that's the moment to press the button
After a read, a second window shows the dots it found.

Needs braille_capture.py and braille_reader.py in the same folder.
Run on the Pi desktop / VNC:   python3 obr_simple.py
Don't run it together with obr_main.py (both use the camera and GPIO17).
"""

import shutil
import subprocess
import time

import cv2

try:
    import speech                 # shared speed + line pauses (speech.py, same folder)
except ImportError:
    speech = None

import braille_capture as bc

BUTTON_PIN = 17                       # GPIO17 = physical pin 11
ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")


def speak(text):
    if not text.strip():
        text = "No readable text found."
    print("Speaking:", " ".join(text.split()))
    if speech is not None and speech.ESPEAK:
        subprocess.run(speech.espeak_cmd(text), check=False)   # slow, pause after each line
    elif ESPEAK:
        subprocess.run([ESPEAK, "-s", "120", " ".join(text.split())], check=False)


def find_markers(gray):
    """ArUco markers in a (small) frame: (corners, ids) for drawing, plus which sheet."""
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    if hasattr(cv2.aruco, "ArucoDetector"):
        corners, ids, _ = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters()).detectMarkers(gray)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(gray, d, parameters=cv2.aruco.DetectorParameters_create())
    found = [] if ids is None else [int(i) for i in ids.ravel()]
    best_name, best_n = None, 0
    for name, layout in bc.SHEET_LAYOUTS.items():
        n = sum(i in layout["markers"] for i in found)
        if n > best_n:
            best_name, best_n = name, n
    return corners, ids, best_name, best_n


def overlay(view, corners, ids, sheet, n):
    h, w = view.shape[:2]
    if ids is not None:
        cv2.aruco.drawDetectedMarkers(view, corners, ids, (0, 255, 0))
    if n >= 3:
        line, colour = f"READY: {sheet} sheet, markers {n}/4. Press GPIO17 button", (0, 255, 0)
    elif n > 0:
        line, colour = f"Markers {n}/4: move so at least 3 are in view", (0, 200, 255)
    else:
        line, colour = "No markers: press GPIO17 anyway (slower), or use the marker sheet", (255, 255, 255)
    cv2.rectangle(view, (0, 0), (w, 34), (0, 0, 0), -1)
    cv2.putText(view, line, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)


def main():
    from gpiozero import Button
    from picamera2 import Picamera2

    state = {"capture": False}
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.3)
    button.when_pressed = lambda: state.update(capture=True)

    camera = Picamera2()
    preview_cfg = camera.create_preview_configuration(main={"size": (1280, 720), "format": "RGB888"})
    still_cfg = camera.create_still_configuration(main={"size": (3280, 2464), "format": "RGB888"})
    camera.configure(preview_cfg)
    camera.start()
    time.sleep(2)

    hint = None                       # remembered page orientation: later reads are faster
    print("OBR BRAILLE READER READY")
    print("Press the button on GPIO17 to capture. Press Q in the camera window to quit.")
    speak("Braille reader ready.")
    try:
        n_frame, marks = 0, (None, None, None, 0)
        while True:
            frame = camera.capture_array()            # already B,G,R: no conversion
            view = frame.copy()                       # drawings go on a copy only
            if n_frame % 2 == 0:                      # marker check, several times a second
                marks = find_markers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            n_frame += 1
            overlay(view, *marks)
            cv2.imshow("OBR Braille Reader", view)

            if state["capture"]:
                state["capture"] = False
                print("\nCapturing full-resolution image...")
                speak("Reading braille")
                image = camera.switch_mode_and_capture_array(still_cfg, "main")
                cv2.imwrite("captured_braille.jpg", image)
                print("Image saved: captured_braille.jpg")
                t0 = time.time()
                braille, text, ann, dots, settings = bc.read_image(image, None, hint)
                if settings is not None:
                    hint = settings
                print(f"Page: {bc.read_image.mode}")
                print(f"Dots found: {dots}   ({time.time() - t0:.1f} s)")
                print("\n--- BRAILLE ---")
                print(braille or "(none)")
                print("--- TEXT ---")
                print(text or "No braille found.")
                print("------------\n")
                s = 900 / max(ann.shape[:2])
                cv2.imshow("OBR result (dots found)", cv2.resize(ann, None, fx=s, fy=s))
                speak(text or "No braille found. Check the page is in view, sharp and lit "
                              "from the side.")
                print("Ready for the next capture.")

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.stop()
        cv2.destroyAllWindows()
        button.close()


if __name__ == "__main__":
    main()
