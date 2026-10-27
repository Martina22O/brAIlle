"""Speech pacing checks; camera, GPIO and speaker playback are not used."""

import array
import contextlib
import io
import shutil
import subprocess
import unittest
import wave
import xml.etree.ElementTree as ET
from unittest.mock import patch

import speech
from braille_keyboard import Speaker


class SpeechFormattingTests(unittest.TestCase):
    def test_page_boundaries_preserve_text(self):
        cases = [
            ("wrapped words", "this is\none sentence", 0),
            ("lowercase sentences", "one sentence. next sentence.", 1),
            ("sentence across a wrap", "one sentence.\nnext sentence.", 1),
            ("decimals", "the values are 3.14 and 1.5. next sentence.", 1),
            ("titles", "Dr. Cruz met Mr. Reyes. they talked.", 1),
            ("numbered labels", "read No. 5 and Fig. 2. next sentence.", 1),
            ("no as a sentence", "no. next sentence.", 1),
            ("abbreviations", "e.g. apples and pears. next sentence.", 1),
            ("dotted acronyms", "the U.S.A. and U.K. cooperate. next sentence.", 1),
            ("name initials", "J. P. Reyes spoke. then left.", 1),
            ("quoted ending", 'he said "done." next sentence.', 1),
            ("parenthesized ending", "he said (done.) next sentence.", 1),
            ("punctuation clusters", "wait... really?! yes.", 2),
            ("commas", "one, two, and three", 0),
            ("domain", "visit example.com. next sentence.", 1),
            ("xml characters", "A & B < C. next sentence.", 1),
            ("markup injection", 'read <break time="20s"/> now', 0),
            ("empty text", "  \n  ", 0),
        ]
        for name, text, count in cases:
            with self.subTest(name=name):
                root = ET.fromstring(speech.to_ssml(text))
                breaks = root.findall("break")
                self.assertEqual(len(breaks), count)
                self.assertTrue(all(item.attrib == {"time": "400ms"} for item in breaks))
                self.assertEqual("".join(root.itertext()), " ".join(text.split()))

    def test_explicit_line_pause_override(self):
        root = ET.fromstring(speech.to_ssml("one line\nanother line", 0.2))
        self.assertEqual([item.attrib for item in root.findall("break")],
                         [{"time": "200ms"}])

    def test_shared_command_defaults_and_overrides(self):
        with patch.object(speech, "ESPEAK", "/usr/bin/espeak-ng"):
            self.assertEqual(speech.espeak_cmd("test")[:3],
                             ["/usr/bin/espeak-ng", "-s", "140"])
            self.assertEqual(speech.espeak_cmd("test", 155, "en-us")[1:5],
                             ["-s", "155", "-v", "en-us"])

    def test_character_spelling_keeps_its_markup(self):
        command = speech.characters_cmd("<&")
        self.assertEqual(command[1:3], ["-s", "140"])
        root = ET.fromstring(command[-1])
        self.assertEqual(root.find("say-as").attrib, {"interpret-as": "characters"})
        self.assertEqual("".join(root.itertext()), "<&")

    def test_reader_speaker_uses_shared_pacing(self):
        with patch("braille_keyboard.shutil.which", return_value="/usr/bin/espeak-ng"), \
                patch("braille_keyboard.subprocess.Popen") as start, \
                contextlib.redirect_stdout(io.StringIO()):
            Speaker().sentence("this is\none sentence. next sentence.")
        command = start.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/espeak-ng", "-s", "140", "-m"])
        self.assertEqual(command[-1],
                         '<speak>this is one sentence.<break time="400ms"/>'
                         ' next sentence.</speak>')


@unittest.skipUnless(shutil.which("espeak-ng"), "eSpeak NG is not installed")
class RenderedAudioTests(unittest.TestCase):
    def metrics(self, text, speed=None, line_pause=None):
        command = speech.espeak_cmd(text, speed, "en-gb", line_pause)
        result = subprocess.run(command[:-1] + ["--stdout", command[-1]],
                                capture_output=True, check=True)
        with wave.open(io.BytesIO(result.stdout), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth()), (1, 2))
            rate = wav.getframerate()
            samples = array.array("h", wav.readframes(wav.getnframes()))
        voiced = [i for i, sample in enumerate(samples) if abs(sample) > 64]
        self.assertTrue(voiced, "Generated speech must contain audio")
        pauses, start = [], None
        for i in range(voiced[0], voiced[-1] + 1):
            if abs(samples[i]) <= 64:
                if start is None:
                    start = i
            elif start is not None:
                duration = (i - start) / rate
                if duration >= 0.15:
                    pauses.append(duration)
                start = None
        return len(samples) / rate, pauses

    def test_wrapped_words_have_no_added_silence(self):
        _, pauses = self.metrics("one\ntwo")
        self.assertEqual(pauses, [])

    def test_lowercase_period_has_one_sentence_pause(self):
        _, pauses = self.metrics("one. two.")
        self.assertEqual(len(pauses), 1)
        self.assertTrue(0.35 <= pauses[0] <= 0.55, pauses)

    def test_uppercase_period_does_not_double_the_pause(self):
        _, pauses = self.metrics("one. Two.")
        self.assertEqual(len(pauses), 1)
        self.assertTrue(0.35 <= pauses[0] <= 0.55, pauses)

    def test_explicit_line_pause_is_rendered(self):
        _, pauses = self.metrics("one\ntwo", line_pause=0.2)
        self.assertEqual(len(pauses), 1)
        self.assertTrue(0.18 <= pauses[0] <= 0.35, pauses)

    def test_default_pace_is_faster_than_120_wpm(self):
        text = "this is a short sentence, with a natural comma."
        normal, _ = self.metrics(text)
        slower, _ = self.metrics(text, speed=120)
        self.assertLess(normal, slower)


if __name__ == "__main__":
    unittest.main()
