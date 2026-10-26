#!/usr/bin/env python3
"""
ocr_simple.py — live camera window + button on GPIO27: press to read printed text aloud.

Fixed version of the simple OCR reader:
  1. OCR runs on the clean camera picture (not the frame with the green label on it)
  2. no colour swap: Picamera2 "RGB888" is already OpenCV's B,G,R order
  3. small fast preview, but each capture is taken at the camera's full resolution
  4. uses ocr_reader.py (lighting fix, auto-rotate, drops unsure words) if it's in the
     same folder; otherwise plain pytesseract

Install:  sudo apt install -y python3-opencv python3-picamera2 python3-gpiozero \
                              tesseract-ocr python3-pytesseract espeak-ng
Run on the Pi desktop / VNC:   python3 ocr_simple.py
Don't run it together with obr_main.py (both use the camera and GPIO27).
"""

import shutil
import subprocess
import time

import cv2

try:
    import speech                 # shared speed + line pauses (speech.py, same folder)
except ImportError:
    speech = None
from gpiozero import Button
from picamera2 import Picamera2

try:
    import ocr_reader                 # the OBR OCR module, if it's next to this file
except ImportError:
    ocr_reader = None
    import pytesseract

button = Button(27, pull_up=True, bounce_time=0.3)    # GPIO27 = physical pin 13
capture_requested = False


def request_capture():
    global capture_requested
    capture_requested = True


button.when_pressed = request_capture

ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")


def speak(text):
    if not text.strip():
        text = "No readable text found."
    print("Speaking:", " ".join(text.split()))
    if speech is not None and speech.ESPEAK:
        subprocess.run(speech.espeak_cmd(text), check=False)   # slow, pause after each line
    elif ESPEAK:
        subprocess.run([ESPEAK, "-s", "120", " ".join(text.split())], check=False)


def read_text(image):
    if ocr_reader is not None:
        text, conf, words, _ = ocr_reader.read_text(image, "eng")
        print(f"({words} words, {conf:.0f}% confident)")
        if words and conf < 65:
            text = "This may not be accurate. " + text
        return text
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return pytesseract.image_to_string(gray, lang="eng", config="--psm 3").strip()


camera = Picamera2()
preview_cfg = camera.create_preview_configuration(main={"size": (1280, 720), "format": "RGB888"})
still_cfg = camera.create_still_configuration(main={"size": (3280, 2464), "format": "RGB888"})
camera.configure(preview_cfg)
camera.start()
time.sleep(2)

print("OCR TEXT READER READY")
print("Press the button on GPIO27 to capture. Press Q in the camera window to quit.")

try:
    while True:
        frame = camera.capture_array()               # already B,G,R: no conversion
        view = frame.copy()                          # label goes on a copy only
        cv2.putText(view, "Press GPIO27 button to read text", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("OCR Text Reader", view)

        if capture_requested:
            capture_requested = False
            print("\nCapturing full-resolution image...")
            image = camera.switch_mode_and_capture_array(still_cfg, "main")
            cv2.imwrite("captured_text.jpg", image)
            print("Image saved: captured_text.jpg")
            print("Recognizing text...")
            text = read_text(image)
            print("\n--- RECOGNIZED TEXT ---")
            print(text or "No readable text found.")
            print("-----------------------\n")
            speak(text)
            print("Ready for the next capture.")

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
finally:
    camera.stop()
    cv2.destroyAllWindows()
    button.close()
