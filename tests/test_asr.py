"""Tests for the dragon_asr reference implementation.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import array
import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dragon_asr import dfile as df, vocab as vocab_mod
from dragon_asr.decoder import Decoder, Lattice, Template, dtw
from dragon_asr.engine import EngineControl, RDApi_GetDFileInfo, RDApi_IsDFile, Recognizer
from dragon_asr.features import Mfcc, MfccConfig, WavFile, detect_speech, hz_to_mel, mel_to_hz
from dragon_asr.transcribe import synth_word_frames

ASRFILES = os.path.join(ROOT, "asrfiles")
ASRBINS = os.path.join(ROOT, "asrbins")


def first_existing(*paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


class TestDFileContainer(unittest.TestCase):
    def test_header_invariants(self):
        path = first_existing(os.path.join(ASRFILES, "lexicon.enh"),
                              os.path.join(ASRBINS, "vocdelta.voc"))
        if not path:
            self.skipTest("ASR assets not extracted")
        d = df.DFile(path)
        self.assertEqual(d.raw[:9], b"DFILE v10")
        self.assertEqual(d.header.version, 10)
        self.assertEqual(d.header.flags, 0xFFFE0002)
        self.assertEqual(d.header.page_size, 0x1000)
        self.assertEqual(d.header.marker, 0xEDACFF0E)
        self.assertEqual(d.remainder, 0, "DFILE files are page aligned")
        self.assertTrue(d.trailer_ok, "trailer marker 0xEDCAFF2E present")
        self.assertGreater(d.header.entry_count, 0)

    def test_all_shipped_assets_parse(self):
        paths = []
        for base in (ASRFILES, ASRBINS):
            if os.path.isdir(base):
                for fn in os.listdir(base):
                    if fn.lower().endswith((".usr", ".psh", ".sig", ".enh", ".voc", ".svc")):
                        paths.append(os.path.join(base, fn))
        if not paths:
            self.skipTest("no ASR assets")
        for p in paths:
            try:
                d = df.DFile(p)
            except ValueError:
                continue                       # not a DFILE (e.g. vocrejct.dat)
            self.assertEqual(d.header.version, 10, p)
            self.assertEqual(d.header.page_size, 0x1000, p)
            self.assertEqual(d.size % 0x1000, 0, p)

    def test_rdapi_helpers(self):
        path = first_existing(os.path.join(ASRFILES, "aco_bm4_dnn.enh"),
                              os.path.join(ASRBINS, "vocdelta.voc"))
        if not path:
            self.skipTest("ASR assets not extracted")
        self.assertTrue(RDApi_IsDFile(path))
        info = RDApi_GetDFileInfo(path)
        for key in ("version", "page_size", "entries", "pages", "size", "trailer_ok"):
            self.assertIn(key, info)
        self.assertFalse(RDApi_IsDFile(__file__))


class TestFeatures(unittest.TestCase):
    def test_mel_roundtrip(self):
        for hz in (100.0, 1000.0, 4000.0):
            self.assertAlmostEqual(mel_to_hz(hz_to_mel(hz)), hz, places=6)

    def test_mfcc_shape(self):
        cfg = MfccConfig(sample_rate=16000, num_ceps=13, use_deltas=True)
        m = Mfcc(cfg)
        samples = [0] * 16000                     # 1 second
        frames = m.frames(samples)
        self.assertGreater(len(frames), 90)       # ~100 frames at 10 ms hop
        self.assertEqual(len(frames[0]), 26)      # 13 cepstra + 13 deltas

    def test_wav_roundtrip_and_detection(self):
        import math
        cfg = MfccConfig(sample_rate=16000)
        m = Mfcc(cfg)
        samples = [int(8000 * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(16000)]
        lo, hi = detect_speech(m.energies(samples))
        self.assertGreater(hi - lo, 10)
        pcm = b"".join(struct.pack("<h", s) for s in samples)
        wav = os.path.join(ROOT, "tests", "_t.wav")
        with open(wav, "wb") as fh:
            fh.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                     struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) +
                     b"data" + struct.pack("<I", len(pcm)) + pcm)
        w = WavFile(wav)
        self.assertEqual((w.rate, w.channels, w.bits), (16000, 1, 16))
        self.assertAlmostEqual(w.duration, 1.0, places=2)
        os.unlink(wav)


class TestDecoder(unittest.TestCase):
    def test_dtw_identical_zero(self):
        seq = [[1.0, 2.0], [2.0, 1.0], [1.5, 1.5]]
        cost, path = dtw(seq, seq)
        self.assertAlmostEqual(cost, 0.0, places=9)
        self.assertEqual(len(path), len(seq))

    def test_dtw_different_positive(self):
        a = [[1.0, 0.0]] * 5
        b = [[0.0, 1.0]] * 5
        cost, _ = dtw(a, b)
        self.assertGreater(cost, 1.0)

    def test_segmental_decode_picks_template(self):
        t1 = Template("alpha", ["AE"], [[1.0, 1.0]] * 20)
        t2 = Template("beta", ["B"], [[9.0, 9.0]] * 20)
        frames = [[1.0, 1.0]] * 20 + [[9.0, 9.0]] * 20
        dec = Decoder([t1, t2])
        res = dec.decode(frames)
        self.assertEqual(res.text, "alpha beta")
        self.assertGreater(res.phrases[0].confidence, 0.5)

    def test_lattice_and_nbest(self):
        t1 = Template("one", ["W"], [[1.0]] * 10)
        t2 = Template("two", ["T"], [[5.0]] * 10)
        dec = Decoder([t1, t2])
        frames = [[1.0]] * 10
        nb = dec.nbest(frames, 2)
        self.assertTrue(nb)
        lat = Lattice(nb)
        self.assertIsNotNone(lat.best())
        self.assertTrue(lat.sausages())


class TestVocabulary(unittest.TestCase):
    def test_g2p_and_entries(self):
        v = vocab_mod.Vocabulary()
        v.add("hello")
        self.assertIn("HH", v.pronunciation("hello"))
        self.assertEqual(len(v), 1)
        v.add("HELLO")                     # case-insensitive duplicate
        self.assertEqual(len(v), 1)

    def test_reject_list(self):
        path = first_existing(os.path.join(ASRFILES, "vocrejct.dat"),
                              os.path.join("asrfiles", "vocrejct.dat"))
        if not path:
            self.skipTest("vocrejct.dat not extracted")
        v = vocab_mod.Vocabulary()
        n = v.load_reject(path)
        self.assertGreater(n, 0)
        self.assertTrue(v.rejected_phones or v.rejected)

    def test_word_file(self):
        p = os.path.join(ROOT, "tests", "_words.txt")
        with open(p, "w") as fh:
            fh.write("# comment\nhello\nworld  testing\n")
        v = vocab_mod.Vocabulary()
        n = v.load_words(p)
        self.assertEqual(n, 3)
        self.assertIn("world", [w.lower() for w in v.words()])
        os.unlink(p)


class TestEngine(unittest.TestCase):
    def test_end_to_end_wav_to_text(self):
        """Synthesise two words with dragon_tts, then recognise them with dragon_asr."""
        from dragon_tts import phonemes
        from dragon_tts import synth as tts_synth
        words = ["hello", "world"]
        templates = [Template(w, vocab_mod.g2p(w), synth_word_frames(w)) for w in words]
        rec = Recognizer(templates)
        pcm = tts_synth.FormantSynthesiser().render(
            tts_synth.RenderRequest(phones=phonemes.text_to_phones("hello world"), fs=16000))
        a = array.array("h")
        a.frombytes(pcm)
        result = rec.transcribe_samples(a, 16000)
        found = set(result.text.lower().split())
        self.assertTrue(found & {"hello", "world"}, f"got {result.text!r}")
        self.assertIn("phrases", result.as_dict())

    def test_engine_control_events_and_writer(self):
        words = ["hello"]
        rec = Recognizer([Template("hello", ["HH"], synth_word_frames("hello"))])
        ctl = EngineControl(rec)
        events = []
        written = []
        ctl.on("RecognitionStarting", lambda: events.append("start"))
        ctl.on("UtteranceEnd", lambda: events.append("end"))
        ctl.writer = written.append
        self.assertIn("FeatureDictation", ctl.EngineFeatures)
        ctl.Microphone = True
        self.assertTrue(ctl.Microphone)
        path = os.path.join(ROOT, "tests", "_hello.wav")
        from dragon_tts import phonemes
        from dragon_tts import synth as tts_synth
        pcm = tts_synth.FormantSynthesiser().render(
            tts_synth.RenderRequest(phones=phonemes.text_to_phones("hello"), fs=16000))
        with open(path, "wb") as fh:
            fh.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
                     struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) +
                     b"data" + struct.pack("<I", len(pcm)) + pcm)
        res = ctl.recognize_file(path)
        os.unlink(path)
        self.assertEqual(events, ["start", "end"])
        self.assertTrue(res.phrases)


class TestRecognitionAccuracy(unittest.TestCase):
    """Isolated-word round trip: synthesise with dragon_tts, recognise with dragon_asr."""

    WORDS = ["hello", "world", "testing", "dragon", "speech", "system"]

    def test_isolated_word_accuracy(self):
        from dragon_tts import phonemes
        from dragon_tts import synth as tts_synth
        templates = [Template(w, vocab_mod.g2p(w), synth_word_frames(w)) for w in self.WORDS]
        rec = Recognizer(templates)
        engine = tts_synth.FormantSynthesiser()
        hit = 0
        for w in self.WORDS:
            pcm = engine.render(tts_synth.RenderRequest(phones=phonemes.text_to_phones(w), fs=16000))
            a = array.array("h")
            a.frombytes(pcm)
            got = rec.transcribe_samples(a, 16000).text.lower().split()
            if w in got:
                hit += 1
        self.assertGreaterEqual(hit, len(self.WORDS) - 1,
                                f"isolated-word accuracy {hit}/{len(self.WORDS)}")

    def test_two_word_phrase(self):
        from dragon_tts import phonemes
        from dragon_tts import synth as tts_synth
        templates = [Template(w, vocab_mod.g2p(w), synth_word_frames(w)) for w in ("hello", "world")]
        rec = Recognizer(templates)
        pcm = tts_synth.FormantSynthesiser().render(
            tts_synth.RenderRequest(phones=phonemes.text_to_phones("hello world"), fs=16000))
        a = array.array("h")
        a.frombytes(pcm)
        got = rec.transcribe_samples(a, 16000).text.lower().split()
        self.assertEqual(got, ["hello", "world"])


class TestPayloadClasses(unittest.TestCase):
    def test_coded_vs_table_pages(self):
        path = first_existing(os.path.join(ASRFILES, "token_s.enh"),
                              os.path.join(ASRBINS, "vocdelta.voc"))
        if not path:
            self.skipTest("ASR assets not extracted")
        d = df.DFile(path)
        counts = d.payload_classes()
        self.assertEqual(sum(counts.values()), d.pages)
        self.assertIn("coded", d.subfile_hint())

    def test_plaintext_table_pages(self):
        path = first_existing(os.path.join(ASRBINS, "vocdelta.voc"),
                              os.path.join(ASRFILES, "aco_bm4_dnn.enh"))
        if not path:
            self.skipTest("ASR assets not extracted")
        counts = df.DFile(path).payload_classes()
        self.assertGreater(counts["tables"], counts["coded"], f"{path}: {counts}")


class TestSdkDocs(unittest.TestCase):
    """The shipped SDK help (dnsapi.chm) documents the automation surface; sanity-check it."""

    def test_api_reference_present(self):
        sdk = os.path.join(ROOT, "sdkdocs")
        if not os.path.isdir(sdk):
            self.skipTest("SDK docs not extracted")
        names = set(os.listdir(sdk))
        for expected in ("API_Overview.htm", "DgnEngineControl_Methods.htm",
                         "DgnEngineControl_Constants.htm", "IDgnEngineControlAuto.htm"):
            self.assertIn(expected, names)
        joined = "\n".join(names)
        self.assertIn("DgnSpeechEngine", joined)
        self.assertIn("IDgnResult", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
