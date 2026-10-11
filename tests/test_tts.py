"""Tests for the dragon_tts reference implementation.

Run from the repository root:
    python3 -m unittest discover -s tests -v

Some tests need the extracted assets (voicefiles/, allprog/); they skip when absent.
"""

from __future__ import annotations

import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dragon_tts import broker as broker_mod, datfile, hdr, lhs_tts, phonemes, sapi4, synth
from dragon_tts.pipeline import Pipeline
from dragon_tts.voicebank import VoiceBank

VOICEFILES = os.path.join(ROOT, "voicefiles")
ALLPROG = os.path.join(ROOT, "allprog")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "broker")
REALSPEAK_DIR = FIXTURES if os.path.isdir(FIXTURES) else ""


def have(*paths: str) -> bool:
    return all(os.path.exists(p) for p in paths)


# ---------------------------------------------------------------------------
class TestDatContainer(unittest.TestCase):
    def test_pp_chunk_inventory(self):
        path = os.path.join(VOICEFILES, "pp.dat")
        if not os.path.exists(path):
            self.skipTest("pp.dat not extracted")
        df = datfile.DatFile(path)
        self.assertEqual(df.banner, "PP\x00\x00LIST")
        tags = [t for t, _ in df.inventory()]
        for expected in ("INFO", "ICOP", "ICRD", "VERS", "CFIG", "BRKD", "ESCD", "EOSD"):
            self.assertIn(expected, tags)
        self.assertEqual(df.get("CFIG").payload, b"\x01\x00")

    def test_synth_vinf_metadata(self):
        path = os.path.join(VOICEFILES, "synth_serena_full_155mrf22.dat")
        if not os.path.exists(path):
            self.skipTest("synth file not extracted")
        df = datfile.DatFile(path)
        self.assertEqual(df.banner, "VFNTLIST")
        self.assertEqual([t for t, _ in df.inventory()][:5],
                         ["INFO", "ICOP", "ICRD", "VERS", "VINF"])
        kv = dict(df.keys("VINF"))
        self.assertEqual(kv["Voice"], "serena")
        self.assertEqual(kv["Freq"], "22050")
        self.assertEqual(kv["Coder"], "155mrf22")
        self.assertEqual(kv["BitRate"], "270")
        sizes = dict(df.inventory())
        for tag in ("CODR", "CTUN", "UNIT", "SPCH"):
            self.assertIn(tag, sizes)
        self.assertGreater(sizes["SPCH"], sizes["UNIT"])

    def test_uselect_inventory(self):
        path = os.path.join(VOICEFILES, "uselect_serena_full.dat")
        if not os.path.exists(path):
            self.skipTest("uselect file not extracted")
        df = datfile.DatFile(path)
        self.assertEqual(df.banner, "USELLIST")
        tags = [t for t, _ in df.inventory()]
        for expected in ("VINF", "SYMB", "UNIT", "CDBK", "TRI ", "CCOS", "PHIS"):
            self.assertIn(expected, tags)
        self.assertEqual(df.get("CTUN").payload, b"\x82\x39\x04\x00")  # 276354

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            datfile.DatFile(os.path.join(VOICEFILES, "clc_enu_samantha_cfg3.dat"))


class TestHdrDescriptors(unittest.TestCase):
    def test_core_descriptor(self):
        path = os.path.join(REALSPEAK_DIR, "rsstdv2_core.hdr")
        if not os.path.exists(path):
            self.skipTest("rsstdv2_core.hdr not extracted")
        e = hdr.parse_lhs(path)
        self.assertEqual(e.comptype, "CORE")
        self.assertEqual(e.brokerstring, "ttscore")
        self.assertEqual(e.engine, "eng/rsstdv2")
        self.assertEqual(e.dll, "rsstdv2_core.dll")
        self.assertEqual(e.getinterface, "CoreGetInterface")

    def test_decoder_descriptor(self):
        path = os.path.join(REALSPEAK_DIR, "rsstdv2_dec11.hdr")
        if not os.path.exists(path):
            self.skipTest("rsstdv2_dec11.hdr not extracted")
        e = hdr.parse_lhs(path)
        self.assertEqual(e.comptype, "DECODER")
        self.assertEqual(e.brokerstring, "decoder/83swf11")
        self.assertEqual(e.getinterface, "gsmc_GetInterface")

    def test_pipeline_manifest(self):
        cands = [os.path.join(VOICEFILES, "vauto_pipeline_enu_samantha_22_full_155mrf22.hdr"),
                 os.path.join(ROOT, "voice", "vauto_pipeline_enu_samantha_22_full_155mrf22.hdr")]
        path = next((p for p in cands if os.path.exists(p)), None)
        if path is None:
            self.skipTest("pipeline manifest not extracted")
        m = hdr.parse_pipeline(path)
        self.assertEqual(m.langcode, "ENU")
        self.assertEqual(m.voice, "Samantha")
        self.assertEqual(m.frequency, 22050)
        self.assertEqual(m.sample_bits, 16)
        self.assertEqual(m.coder, "155mrf22")
        for comp in ("fe/fe_lid", "fe/tokentn", "phonmap/mrk", "uselect/bet1", "synth/bet1",
                     "audioinserter"):
            self.assertIn(comp, m.components)


class TestBroker(unittest.TestCase):
    def test_resolution(self):
        if not REALSPEAK_DIR:
            self.skipTest("program tree not extracted")
        b = broker_mod.Broker(roots=[REALSPEAK_DIR])
        self.assertGreaterEqual(b.scan(), 2)
        self.assertEqual(b.broker_string_query("TTSENGINE"), "eng/rsstdv2")
        engine = b.obj_open("eng/rsstdv2")
        ref = b.interface_query(engine, "ttscore")
        self.assertEqual(ref.dll, "rsstdv2_core.dll")
        self.assertEqual(ref.entry, "CoreGetInterface")
        dec = b.interface_query(engine, "decoder/83swf11")
        self.assertEqual(dec.entry, "gsmc_GetInterface")


class TestFrontEnd(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(phonemes.normalise("I have 3 cats"), "I have three cats")
        self.assertIn("forty", phonemes.normalise("42"))
        self.assertIn("dollars", phonemes.normalise("$5"))

    def test_phones(self):
        phones = [p for p, _ in phonemes.text_to_phones("hello world") if p not in ("_", ".")]
        self.assertIn("HH", phones)
        self.assertTrue(any(p[-1].isdigit() for p in phones if p and p[0] in "AEIOU"))


class TestSynthesis(unittest.TestCase):
    def test_pcm_and_wav(self):
        s = synth.FormantSynthesiser()
        req = synth.RenderRequest(phones=phonemes.text_to_phones("the quick brown fox"),
                                  fs=22050)
        pcm = s.render(req)
        self.assertGreater(len(pcm), 22050)                     # > 0.5 s
        self.assertEqual(len(pcm) % 2, 0)
        wav = synth.pcm_to_wav(pcm, 22050)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        size, audio_fmt, channels, rate, _byte_rate, _align, bits = \
            struct.unpack_from("<IHHIIHH", wav, 16)
        self.assertEqual((size, audio_fmt, channels, rate, bits), (16, 1, 1, 22050, 16))
        peak = max(abs(v) for v in struct.unpack("<%dh" % (len(pcm) // 2),
                                                 pcm[:len(pcm) // 2 * 2]))
        self.assertGreater(peak, 1000)                          # not silence

    def test_louder_text_is_longer(self):
        s = synth.FormantSynthesiser()
        short = s.render(synth.RenderRequest(phones=phonemes.text_to_phones("hi")))
        long_ = s.render(synth.RenderRequest(phones=phonemes.text_to_phones(
            "hi there this is a much longer sentence to synthesize")))
        self.assertGreater(len(long_), len(short))


class TestVoiceTables(unittest.TestCase):
    def test_pp_escape_schema(self):
        from dragon_tts import pmtables
        path = os.path.join(VOICEFILES, "pp.dat")
        if not os.path.exists(path):
            self.skipTest("pp.dat not extracted")
        names = pmtables.decode_escape_schema(path)
        for expected in ("basicesc", "basicescwitharg", "enhancedescseq", "esctoi"):
            self.assertIn(expected, names, f"escape schema names: {names[:20]}")

    def test_uselect_geometry_and_properties(self):
        from dragon_tts import pmtables
        path = os.path.join(VOICEFILES, "uselect_serena_full.dat")
        if not os.path.exists(path):
            self.skipTest("uselect not extracted")
        t = pmtables.VoiceTables(path)
        self.assertGreater(t.geometry.units, 100000)
        self.assertAlmostEqual(t.geometry.per_unit.get("UNIT", 0.0), 20.0, places=6)
        self.assertAlmostEqual(t.geometry.per_unit.get("SYMB", 0.0), 9.0, places=6)
        tags = [tag for tag, _ in t.properties]
        self.assertIn("ALPH", tags)

    def test_synth_geometry(self):
        from dragon_tts import pmtables
        path = os.path.join(VOICEFILES, "synth_serena_full_155mrf22.dat")
        if not os.path.exists(path):
            self.skipTest("synth not extracted")
        t = pmtables.VoiceTables(path)
        self.assertAlmostEqual(t.geometry.per_unit.get("UNIT", 0.0), 8.0, places=6)
        self.assertEqual(t.strings.get("VINF") and "serena" in " ".join(t.strings["VINF"]), True)

    def test_engine_header_chunk(self):
        from dragon_tts import pmtables
        path = os.path.join(VOICEFILES, "synth_med_fxd_bet2f22.dat")
        if not os.path.exists(path):
            self.skipTest("base voice not extracted")
        t = pmtables.VoiceTables(path)
        joined = " ".join(t.strings.get("EXHD", []))
        self.assertIn("pls65_dsp179", joined)
        self.assertIn("sr22", joined)


class TestCoderGeometry(unittest.TestCase):
    def test_frame_derivation(self):
        from dragon_tts import pmtables
        path = os.path.join(VOICEFILES, "synth_serena_full_155mrf22.dat")
        if not os.path.exists(path):
            self.skipTest("synth not extracted")
        g = pmtables.coder_geometry(path)
        self.assertAlmostEqual(g["bytes_per_unit"], 152.825, places=2)
        self.assertAlmostEqual(g["frames_per_second"], 220.84, places=1)
        self.assertGreater(g["bank_seconds"], 1000.0)


class TestPipeline(unittest.TestCase):
    def test_component_names_match_manifest(self):
        p = Pipeline.for_voice(None)
        for name in ("fe/fe_lid", "fe/fe_udwl", "fe/tokentn", "fe/fe_phrasing", "phonmap",
                     "uselect", "synth", "xcoder", "audioinserter"):
            self.assertIn(name, p.components)
        wav = p.synthesize("hello world")
        self.assertEqual(wav[:4], b"RIFF")

    def test_manifest_instantiation(self):
        cands = [os.path.join(VOICEFILES, "vauto_pipeline_enu_samantha_22_full_155mrf22.hdr"),
                 os.path.join(ROOT, "voice", "vauto_pipeline_enu_samantha_22_full_155mrf22.hdr")]
        path = next((p for p in cands if os.path.exists(p)), None)
        if path is None:
            self.skipTest("manifest not extracted")
        m = hdr.parse_pipeline(path)
        p = Pipeline(manifest=m, frequency=m.frequency)
        p.build()
        self.assertEqual(p.frequency, 22050)
        # every manifest component must be accounted for (implemented or pass-through)
        for c in m.components:
            self.assertIn(c, p.components)


class TestVoiceBank(unittest.TestCase):
    def test_bank_load(self):
        if not os.path.isdir(VOICEFILES):
            self.skipTest("voicefiles missing")
        bank = VoiceBank.load(VOICEFILES)
        names = {v.voice.lower() for v in bank.voices.values()}
        self.assertTrue({"serena", "samantha"} & names or names, f"no voices: {names}")
        v = bank.voice("serena")
        self.assertEqual(v.frequency, 22050)
        self.assertEqual(v.coder, "155mrf22")


class TestLhsTtsApi(unittest.TestCase):
    def setUp(self):
        bank = VoiceBank.load(VOICEFILES) if os.path.isdir(VOICEFILES) else None
        self.mod = lhs_tts.configure(bank=bank)

    def test_full_cycle(self):
        self.assertEqual(self.mod.InitializeEx(0, b"\x00" * 8, 1), lhs_tts.LHS_OK)
        rc, h = self.mod.ttsOpen(None, "RealSpeak Standard v2")
        self.assertEqual(rc, lhs_tts.LHS_OK)
        self.assertNotEqual(h, 0)
        self.assertEqual(self.mod.ttsSetParamList(h, [(lhs_tts.PARAM_LANGUAGE, "American English"),
                                                      (lhs_tts.PARAM_OUTPUTCFG, 0x202)]),
                         lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsSetText(h, "hello world"), lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsSpeak(h), lhs_tts.LHS_OK)
        inst = self.mod.system.instances[h]
        self.assertGreater(len(inst.last_pcm), 8000)
        out = os.path.join(ROOT, "tests", "_out.wav")
        self.assertEqual(self.mod.ttsGenOutputFile(h, out), lhs_tts.LHS_OK)
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(4), b"RIFF")
        os.unlink(out)
        self.assertEqual(self.mod.ttsStop(h), lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsPause(h), lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsResume(h), lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsClose(h), lhs_tts.LHS_OK)
        self.assertEqual(self.mod.ttsClose(h), lhs_tts.LHS_E_INVALIDHANDLE)

    def test_error_paths(self):
        self.assertEqual(self.mod.ttsSpeak(999), lhs_tts.LHS_E_INVALIDHANDLE)
        rc, h = self.mod.ttsOpen(None, "default")
        self.assertEqual(self.mod.ttsSpeak(h), lhs_tts.LHS_E_INVALIDARG)  # no text set
        self.assertEqual(lhs_tts.ErrorGetString(lhs_tts.LHS_E_INVALIDHANDLE), "invalid handle")

    def test_output_callback(self):
        rc, h = self.mod.ttsOpen(None, "default")
        seen = []
        self.mod.ttsSetOutDevice(h, lambda pcm: seen.append(len(pcm)))
        self.mod.ttsSetText(h, "callback test")
        self.mod.ttsSpeak(h)
        self.assertEqual(len(seen), 1)
        self.assertGreater(seen[0], 8000)


class TestSapi4(unittest.TestCase):
    def test_mode_enumeration_and_speak(self):
        bank = VoiceBank.load(VOICEFILES) if os.path.isdir(VOICEFILES) else None
        spoken = []
        eng = sapi4.Engine(voice_bank=bank, speak=spoken.append)
        rc, central = eng.create_and_select("American English Jennifer")
        if central is None:
            central = eng.create_and_select("")
        self.assertIsNotNone(central)
        self.assertEqual(central.Register(sapi4.NotifySink()), (0, 1))
        rc = central.TextData(0, 0, "hello", 10, None, sapi4.GUID_TEXT_FORMAT)
        self.assertEqual(rc, 0)
        self.assertEqual(central.UnRegister(1), 0)
        self.assertEqual(central.UnRegister(1), sapi4.TTSERR_INVALIDKEY)
        self.assertEqual(spoken, ["hello"])

    def test_registration_script(self):
        script = sapi4.registry_script(r"C:\x\rsstdv2.dll")
        self.assertIn(sapi4.CLSID_ENGINE, script)
        self.assertIn("Software\\Voice\\TextToSpeech\\Engine", script)
        self.assertIn("ThreadingModel", script)
        self.assertIn("Apartment", script)


class TestCHeader(unittest.TestCase):
    def test_header_lists_exports(self):
        path = os.path.join(ROOT, "include", "lhs_tts.h")
        if not os.path.exists(path):
            self.skipTest("header missing")
        with open(path) as fh:
            text = fh.read()
        for name in ("lhs_ttsOpenW", "lhs_ttsSetParamListW", "lhs_ttsGenOutputFileW",
                     "lhs_ttsGetInstStatus", "lhs_ttsDetectLanguageW", "lhs_ttsBackward",
                     "CoreGetInterface", "lhs_tts_core_vtbl_t"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
