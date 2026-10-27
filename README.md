# OBR — Optical Braille Recognition (Raspberry Pi)

Point a camera at braille **or printed text**, press a button, hear it read aloud.

Built for a **Raspberry Pi 4B + Camera Module NoIR v2**, but also runs on any
computer with a webcam or on saved photos.

## The device: three buttons (recommended)

`obr_main.py` runs everything in one program: the camera reader, OCR and the ESP32
braille keyboard, controlled by three push-buttons on the Pi.

| Button | Pi GPIO (physical pin) | What it does |
|---|---|---|
| **OBR** | GPIO17 (pin 11) | Press once: "Braille mode, place the page". Press again: photographs and reads the **braille** aloud |
| **OCR** | GPIO27 (pin 13) | The same for **printed text** |
| **TYPE** | GPIO22 (pin 15) | Turns the ESP32 braille keyboard on or off |
| (all) | GND (pin 9 or 14) | The other leg of every button |

Each button goes between its GPIO pin and GND. No resistors are needed, because the
Pi's internal pull-ups are used. Check the wiring with `python3 test_buttons.py`:
press each button, then Ctrl+C to see which ones worked. You can use other pins with
`--obr-pin`, `--ocr-pin` and `--type-pin` (BCM numbers).

```bash
python3 obr_main.py                   # buttons + camera + ESP32 keyboard
python3 obr_main.py --lang eng+fil    # also Filipino printed text
python3 obr_main.py --type-off        # start with the keyboard switched off
```

At start-up it says, for example, "OBR ready. Keyboard connected. Type mode on."

- After the first press you have 30 seconds to press again (`--arm-timeout 60` for
  longer); after that it says "Cancelled". Pressing the other camera button switches
  mode. `--one-press` makes OBR and OCR read on the first press instead.
- Pressing OBR or OCR again while it is still reading is ignored.
- With type mode off, typing on the ESP32 reminds you to press TYPE.
- Without buttons connected, you can type `b`, `t`, `k` or `q` and Enter in the
  terminal instead.

To start it at boot, use the user service `obr-user.service` (speech then reaches
the Pi's speaker; no login needed):

```bash
mkdir -p ~/.config/systemd/user
cp ~/obr/obr-user.service ~/.config/systemd/user/obr.service
sudo loginctl enable-linger admin        # start it at boot without logging in
systemctl --user daemon-reload
systemctl --user enable --now obr
journalctl --user -u obr -f              # see what it's doing
```

Stop it with `systemctl --user stop obr` before running any other OBR script by
hand (the camera can only be used by one program). `systemctl --user disable --now obr`
turns the auto-start off.

The separate programs below (`braille_capture.py`, `braille_keyboard.py`) still
work on their own for testing.

## Files

| File | What it is |
|---|---|
| `obr_main.py` | **The device:** OBR, OCR and TYPE buttons + camera + ESP32 keyboard |
| `test_buttons.py` | Checks the three buttons' wiring |
| `obr-user.service` | Start `obr_main.py` at boot (recommended) |
| `obr-main.service` | Older system-wide version of the same (use one, not both) |
| `braille_capture.py` | Main program: camera/photo → dots → braille → text → speech |
| `braille_reader.py` | Braille ⇄ text translator (Grade 1, uncontracted). Also usable on its own |
| `ocr_reader.py` | Reads printed text with Tesseract OCR (a module: don't replace it) |
| `ocr_simple.py` | Simple standalone OCR reader: live camera window + GPIO27 button |
| `obr_simple.py` | Simple standalone braille reader: live window shows the ArUco markers + GPIO17 button |
| `make_aruco_sheet.py` | Regenerates the printable marker sheets |
| `braille_sheet_sq180.pdf` | A4 sheet, 180 × 180 mm camera square, markers 4–7 (**recommended**) |
| `braille_sheet_a4.pdf` | A4 sheet, markers in the page corners, IDs 0–3 |
| `obr.service` | Optional: start OBR automatically at boot (button mode) |
| `braille_keyboard.py` | ESP32 braille keyboard: spell a keyword, press Enter, hear a sentence |
| `phrases.txt` | Keywords and their sentences (edit freely) |
| `speech.py` | Shared speech settings: reading speed, wrapped lines and sentence pauses |
| `test_speech.py` | Speech formatting and optional generated-audio checks without speaker playback |
| `esp32_braille_keyboard/esp32_braille_keyboard.ino` | Firmware for the ESP32 DevKit |
| `obr-keyboard.service` | Optional: start the keyboard program at boot |

## Install (Raspberry Pi OS Bookworm)

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-numpy espeak-ng python3-gpiozero tesseract-ocr
```

For Filipino printed text, also install the Filipino OCR data, then run with
`--lang eng+fil`. If the package name isn't found, `apt search tesseract-ocr-` lists them.

```bash
sudo apt install -y tesseract-ocr-fil
```

Check the camera and the marker detector:

```bash
rpicam-hello --list-cameras
python3 -c "import cv2; print(cv2.aruco)"
```

(`make_aruco_sheet.py` also needs `sudo apt install python3-reportlab`, but only to make new sheets.)

## Wireless SSH

On Raspberry Pi OS with NetworkManager, connect the Pi to Wi-Fi before removing
Ethernet. Replace `YOUR_SSID` with the network name and enter its password at the
prompt:

```bash
sudo nmcli --ask device wifi connect "YOUR_SSID" ifname wlan0
nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS device show wlan0
```

NetworkManager saves the connection and reconnects automatically. From another
computer, check `ssh admin@PI_WIFI_IP`, replacing `PI_WIFI_IP` with the wireless
address shown above. Disconnect Ethernet only after that login succeeds. The
address is assigned by DHCP and can change.

## Optional headless startup

For an enclosed reader that uses buttons and speech, the desktop can stay off.
Use the recommended user-service setup above, including lingering, and first
confirm the reader and speaker work. Keep the user audio services enabled:

```bash
loginctl show-user admin -p Linger
systemctl --user is-enabled obr.service pipewire.service pipewire-pulse.service wireplumber.service
systemctl get-default                 # record this setting for rollback
sudo systemctl set-default multi-user.target
sudo systemctl reboot
```

After reconnecting over SSH, verify the services and listen for the ready message:

```bash
systemctl get-default
systemctl --user is-active obr.service pipewire.service pipewire-pulse.service wireplumber.service
journalctl -b _SYSTEMD_USER_UNIT=obr.service -n 30 --no-pager
```

The desktop and desktop-based VNC stay off in this mode. Wi-Fi, SSH and the reader
remain available. Restore graphical startup with:

```bash
sudo systemctl set-default graphical.target
sudo systemctl reboot
```

If the recorded original target differs, restore that target instead.

Headless startup passed a clean-reboot check on a Pi 4, including speaker output,
camera startup and keyboard detection. A cold power-on speed improvement has not
been established. Compare the same power-to-first-ready-speech measurement before
and after changing the target, and shut down cleanly before disconnecting power.

## Use

```bash
cd obr
python3 braille_capture.py                    # with a monitor: SPACE = read, Q = quit
python3 braille_capture.py --headless         # no monitor: Enter = read, q = quit
python3 braille_capture.py --button 17        # push-button between GPIO17 and GND
python3 braille_capture.py --button 17 --print-button 27   # 2nd button: printed text
python3 braille_capture.py --image page.jpg   # read a saved photo
python3 braille_capture.py --debug check.png  # save what it saw (plus check_raw.jpg)
```

### Braille or printed text

| Mode | What it reads |
|---|---|
| `--mode auto` (default) | Braille if it finds any, otherwise printed text. A marker sheet is always braille |
| `--mode braille` | Braille only |
| `--mode print` | Printed text only (labels, letters, book pages) |

| Read with | Monitor | No monitor | Buttons |
|---|---|---|---|
| the chosen mode | SPACE | Enter | `--button` |
| braille | B | `b` + Enter | |
| printed text | T | `t` + Enter | `--print-button` |

Printed text is read with Tesseract OCR. If it isn't sure of the words, it says
"This may not be accurate" before reading them. A blurry photo is reported as
"No printed text found". OCR takes a few seconds per page on a Pi 4.

More options: `--voice en-us`, `--speed 140`, `--quiet` (no speech), `--invert`
(force light dots on dark), `--camera 1`, `--lang eng+fil`.

Read printed text from a saved photo without speech:

```bash
python3 ocr_reader.py label.jpg --lang eng
```

Translate typed braille or text directly:

```bash
python3 braille_reader.py "⠠⠓⠑⠇⠇⠕"
python3 braille_reader.py --dots "6 125 15 123 123 135"
python3 braille_reader.py --encode "Hello world"
```

## Writing on the marker sheet

1. Print `braille_sheet_sq180.pdf` at **100% / Actual size**. The scale bar must measure 50 mm.
2. Put the **printed side down** in the slate, top edge up, so the dots rise on the printed side.
3. Write only inside the small faint corner marks (up to 14 lines × 29 cells).
4. Point the camera at the 180 × 180 mm square. At least 3 of the 4 markers must be visible.

Without a marker sheet it still works: it finds the paper's edges and tries all
orientations, which is slower and a bit less reliable.

## Camera setup tips

- Mount the camera straight above the page.
- **Focus:** the v2 lens is set for far away. Turn it a little with the focus tool so
  close-up dots are sharp, and check with `--debug`.
- **Light:** shine light from one side at a low angle so raised dots cast shadows.
  On the NoIR camera an infrared LED works well; the pink tint doesn't matter.
- Keep hands and shadows off the page.
- **Printed text** needs even, bright light instead (no low side light) and a sharp
  focus. Fill as much of the frame with the page as possible.

## Start at boot (button mode)

`obr.service` is set for user `admin` in `/home/admin/obr`. If yours differ, edit it, then:

```bash
sudo cp obr.service /etc/systemd/system/
sudo systemctl enable --now obr
journalctl -u obr -f        # see what it reads
```

## Speech speed and pauses

The shared default is **140 words per minute**. Wrapped lines are joined for
speech, so a short detected line does not force a pause halfway through a
sentence. Sentence endings (`.`, `!`, `?`) receive a **400 ms pause**, including
when the next word starts with a lowercase letter. Commas keep eSpeak's natural
pacing.

The formatter preserves the recognized text and protects decimals, common
abbreviations, dotted acronyms and name initials. Ambiguous abbreviations can
still need manual correction. A missing or misread period must be corrected in
recognition; the speech formatter cannot recover punctuation it never receives.

- Change it for one run: `python3 obr_main.py --speed 110 --line-pause 1.2`
- `--line-pause` adds an explicit pause after each detected line when requested.
- Change the shared defaults in `speech.py`: `SPEED`, `LINE_PAUSE` and `SENTENCE_PAUSE`.
- Restart the running OBR user service after changing those defaults:
  `systemctl --user restart obr.service`.

### Speech regression checks

Run the formatting and shared-reader checks with Python's standard library:

```bash
python3 -B -m unittest -v test_speech
```

When `espeak-ng` is installed, the same command also synthesizes audio in memory
and checks sentence pauses, wrapped lines and speaking speed. Those checks use
`--stdout` and do not play sound or open the camera or GPIO. Audio checks skip when
the engine is absent. A listening check with the actual speaker and Braille page
is still needed to judge clarity.

## Braille keyboard (ESP32 DevKit + 9 buttons)

Spell a short keyword in braille, press Enter, and the Pi speaks the sentence that
goes with it, for example `water` → "Could I have some water, please?".

### Wiring

Each button goes between its GPIO pin and **GND**. No resistors are needed.

| Key | GPIO | | Key | GPIO |
|---|---|---|---|---|
| dot 1 | 13 | | dot 4 | 26 |
| dot 2 | 14 | | dot 5 | 25 |
| dot 3 | 27 | | dot 6 | 33 |
| space | 32 | | backspace | 18 |
| enter | 19 | | | |

Perkins layout, like a braille writer:

```
 [dot3] [dot2] [dot1]        [dot4] [dot5] [dot6]
 [backspace]          [ SPACE ]          [enter]
```

### Flash the ESP32

1. In the Arduino IDE, add the **esp32 by Espressif** board package (Boards Manager).
2. Open `esp32_braille_keyboard/esp32_braille_keyboard.ino`.
3. Board: **ESP32 Dev Module**, choose its port, and Upload.
4. Check it in Serial Monitor at 115200 baud: pressing dots 1-2-5 together shows `C:19`.

### Run on the Pi

Plug the ESP32 into the Pi with USB, then:

```bash
sudo apt install -y python3-serial espeak-ng
python3 braille_keyboard.py            # finds the ESP32 by itself
python3 braille_keyboard.py --simulate # try it without the ESP32: type "125 15 en"
```

| Do this | What happens |
|---|---|
| Press a cell's dots together, then let go | The letter is typed and spoken |
| Enter | The sentence for the typed keyword is spoken |
| Enter with nothing typed | The last sentence is repeated |
| Backspace | Deletes the last cell |
| Space + Backspace together | Clears the word |
| Type `help` + Enter | Reads out all the keywords |
| Type a word that isn't a keyword + Enter | Reads the word as typed ("Hello" → "Hello") |
| Numbers | Number sign (dots 3-4-5-6), then a-j for 1-0, e.g. `2026` |
| Symbols | `, ; : . ! ? ' -` as one cell; `@ # $ % & * + = / ( )` as two cells (UEB), e.g. `@` = dot 4, then dot 1 |

Edit `phrases.txt` (`keyword = sentence`, one per line) to add your own. Changes
work immediately. A keyword with one wrong letter still matches if it is clearly
closest to one keyword. Any other word, number or symbol is simply read out as typed. Options: `--voice en-us`, `--speed 140`, `--no-letters`
(don't speak each letter), `--port /dev/ttyUSB0`.

The English voice will pronounce Filipino sentences imperfectly. `espeak-ng --voices`
lists the voices installed.

To start it at boot, edit the user and paths in `obr-keyboard.service`, then:

```bash
sudo cp obr-keyboard.service /etc/systemd/system/
sudo systemctl enable --now obr-keyboard
```

It can run at the same time as the camera reader (`obr.service`).

## Limits

- Reads **Grade 1 (uncontracted)** braille: letters, numbers, capitals and common
  punctuation. Contracted (Grade 2) braille needs Liblouis, which isn't added yet.
- Double-sided (interpoint) pages may pick up dents from the back.
- The marker layouts in `make_aruco_sheet.py` and `braille_capture.py`
  (`SHEET_LAYOUTS`) must match. Change both together.
- If the result doesn't look like text (for example the camera sees fabric or a blank
  page), it says "No braille found" instead of reading out gibberish.
- OCR reads printed type, not handwriting. Shiny packaging, curved labels on bottles
  and very small print can fail; flatten the label and fill the frame with it.
- OCR was tested on simulated camera photos with the same Tesseract version Pi OS
  uses (5.3), not yet on photos from the Pi camera.

## Hardware validation

The updated speech helper was deployed on a Raspberry Pi 4 Model B with an IMX219
camera and eSpeak NG 1.52.0. A user listening check on the same Braille page
confirmed clear pacing and improved pauses. Wireless SSH and headless user-service
startup were also checked, including speaker output, camera initialization and
ESP32 serial detection.

Those checks do not establish recognition accuracy, a cold-start speedup or
complete hardware coverage. Still verify these on the intended device:

- Camera focus, lighting and marker-detection accuracy. The marker detector's
  compatibility path for OpenCV 4.6 still needs checking; the live Pi used 4.10.
- GPIO wiring and audio for the selected service arrangement. The recommended
  user service was checked with PipeWire; legacy system-wide services can have
  different audio access.
- The ESP32 firmware with the real board compiler and hardware. Earlier compile
  and simulation checks used a stand-in Arduino and serial device; successful Pi
  serial detection does not replace a firmware build or a complete button test.

## How it works

1. **Page:** find the ArUco markers and map the writing area to exact millimetres,
   or, with no markers, find the paper's edges and flatten it.
2. **Dots:** difference-of-Gaussians blob detection at several sizes.
3. **Lines:** link nearby dots, then fit each line's own slope and gentle curve.
4. **Cells:** pair dot columns into 2 × 3 cells using each line's local spacing.
5. **Orientation:** fixed by the markers, or the best-reading of 8 rotations/mirrors.
6. **Text & speech:** decode with `braille_reader.py`, speak with espeak-ng.
7. **Printed text:** in auto mode, if no braille is found, the page is flattened at
   full camera resolution, the lighting is evened out, Tesseract turns it upright and
   reads it, and words it isn't confident about are dropped.
# Braille-Reader
