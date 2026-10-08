"""Tests for audio loading (read_audio / WaveFile)."""

import io
import os
import shutil
import struct
import tempfile
import unittest
import wave

import numpy as np

from cicwave.wavefiles import WaveFile, read_audio


def _riff(tag, channels, rate, bits, payload, extensible=False):
    """A minimal WAVE file around *payload*, for encodings ``wave`` can't write."""
    block = channels * bits // 8
    fmt = struct.pack('<HHIIHH', 0xFFFE if extensible else tag, channels,
                      rate, rate * block, block, bits)
    if extensible:
        guid = struct.pack('<H', tag) + bytes(14)
        fmt += struct.pack('<HHI', 22, bits, 0) + guid
    body = (b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt
            + b'data' + struct.pack('<I', len(payload)) + payload)
    if len(payload) % 2:
        body += b'\0'
    return b'RIFF' + struct.pack('<I', len(body)) + body


class ReadAudioTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cicwave-audio-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _write_wave(self, name, channels, width, rate, frames):
        p = self._path(name)
        with wave.open(p, 'wb') as w:
            w.setnchannels(channels)
            w.setsampwidth(width)
            w.setframerate(rate)
            w.writeframes(frames)
        return p

    def test_pcm16_mono_full_scale_and_time(self):
        x = np.array([0, 16384, -32768, 32767], dtype='<i2')
        p = self._write_wave("mono.wav", 1, 2, 8000, x.tobytes())
        df = read_audio(p)
        self.assertEqual(list(df.columns), ['time', 'audio'])
        np.testing.assert_allclose(df['audio'], x / 32768.0)
        np.testing.assert_allclose(df['time'], np.arange(4) / 8000.0)
        self.assertEqual(df.attrs['cicwave_iq']['samp_rate_hz'], 8000.0)
        self.assertEqual(df.attrs['cicwave_audio']['channels'], 1)
        self.assertEqual(df.attrs['cicwave_audio']['bits'], 16)

    def test_pcm16_stereo_left_right(self):
        x = np.array([[100, -100], [200, -200], [300, -300]], dtype='<i2')
        p = self._write_wave("st.wav", 2, 2, 44100, x.tobytes())
        df = read_audio(p)
        self.assertEqual(list(df.columns), ['time', 'left', 'right'])
        np.testing.assert_allclose(df['left'], x[:, 0] / 32768.0)
        np.testing.assert_allclose(df['right'], x[:, 1] / 32768.0)

    def test_pcm8_unsigned(self):
        p = self._write_wave("u8.wav", 1, 1, 1000, bytes([0, 128, 255]))
        df = read_audio(p)
        np.testing.assert_allclose(df['audio'], [-1.0, 0.0, 127 / 128.0])

    def test_pcm24_sign_extends(self):
        vals = [0, 1, -1, 2 ** 23 - 1, -2 ** 23]
        payload = b''.join(struct.pack('<i', v)[:3] for v in vals)
        p = self._path("s24.wav")
        with open(p, 'wb') as fh:
            fh.write(_riff(1, 1, 48000, 24, payload))
        df = read_audio(p)
        np.testing.assert_allclose(df['audio'], np.array(vals) / 2.0 ** 23)

    def test_float32_extensible_multichannel(self):
        x = np.arange(12, dtype='<f4').reshape(4, 3) / 16.0
        p = self._path("f32.wav")
        with open(p, 'wb') as fh:
            fh.write(_riff(3, 3, 96000, 32, x.tobytes(), extensible=True))
        df = read_audio(p)
        self.assertEqual(list(df.columns), ['time', 'ch0', 'ch1', 'ch2'])
        np.testing.assert_allclose(df[['ch0', 'ch1', 'ch2']].to_numpy(), x)
        self.assertEqual(df.attrs['cicwave_audio']['encoding'], 'FLOAT')

    def test_skips_unknown_chunks_and_truncated_data(self):
        x = np.array([1000, -1000, 2000], dtype='<i2')
        raw = _riff(1, 1, 8000, 16, x.tobytes())
        #- A LIST chunk before fmt, and a data size larger than the file.
        lst = b'LIST' + struct.pack('<I', 5) + b'abcde\0'
        raw = raw[:12] + lst + raw[12:]
        i = raw.index(b'data')
        raw = raw[:i + 4] + struct.pack('<I', 10 ** 6) + raw[i + 8:]
        df = read_audio(io.BytesIO(raw), '.wav')
        np.testing.assert_allclose(df['audio'], x / 32768.0)

    def test_not_a_wav(self):
        p = self._path("bad.wav")
        with open(p, 'wb') as fh:
            fh.write(b'not audio at all')
        with self.assertRaises(ValueError):
            read_audio(p)

    def test_wavefile_dispatch(self):
        x = (np.sin(np.arange(64)) * 10000).astype('<i2')
        p = self._write_wave("tone.wav", 1, 2, 16000, x.tobytes())
        wf = WaveFile(p, 'time')
        self.assertIn('audio', wf.getWaveNames())
        self.assertEqual(len(wf.df), 64)

    def test_other_formats_need_soundfile(self):
        try:
            import soundfile  # noqa: F401
        except ImportError:
            p = self._path("x.flac")
            with open(p, 'wb') as fh:
                fh.write(b'fLaC')
            with self.assertRaisesRegex(ValueError, "soundfile"):
                read_audio(p)
        else:
            self.skipTest("soundfile installed")


if __name__ == '__main__':
    unittest.main()
