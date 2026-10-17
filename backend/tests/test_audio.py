import io
import struct

import numpy as np
import soundfile as sf

from backend.audio import CROSS_FADE_S, PEAK, SAMPLE_RATE, cross_fade_join, limit_peak, split_into_chunks, wav_bytes


def test_short_phrases_are_packed_up_to_100_characters():
    phrases = ["ก" * 40, "ข" * 40, "ค" * 40]
    chunks = split_into_chunks(" ".join(phrases))
    assert chunks == [phrases[0] + " " + phrases[1], phrases[2]]
    assert all(len(c) <= 100 for c in chunks)


def test_space_less_run_is_cut_by_the_given_word_chunker():
    chunks = split_into_chunks("ก" * 250, lambda piece, n: [piece[:n], piece[n:]])
    assert chunks == ["ก" * 100, "ก" * 150]  # the chunker decides; the packer does not re-cut


def test_ellipsis_is_a_space_and_blank_text_has_no_chunks():
    assert split_into_chunks("ก…ข") == ["ก ข"]
    assert split_into_chunks(" … ") == []


def test_cross_fade_overlaps_by_150_ms_and_keeps_constant_level():
    fade = int(CROSS_FADE_S * SAMPLE_RATE)
    joined = cross_fade_join([np.ones(SAMPLE_RATE, np.float32), np.ones(SAMPLE_RATE, np.float32)])
    assert len(joined) == 2 * SAMPLE_RATE - fade
    assert np.allclose(joined, 1.0, atol=1e-5)  # ramps sum to 1


def test_single_wave_is_untouched():
    wave = np.arange(5, dtype=np.float32)
    assert np.array_equal(cross_fade_join([wave]), wave)


def test_loud_audio_is_limited_to_minus_1_dbfs_quiet_audio_is_not_boosted():
    assert abs(float(np.abs(limit_peak(np.array([0.0, 2.0], np.float32))).max()) - PEAK) < 1e-6
    quiet = np.array([0.1], np.float32)
    assert np.array_equal(limit_peak(quiet), quiet)


def test_wav_is_riff_pcm16_mono_24k():
    data = wav_bytes(np.sin(np.linspace(0, 100, 2400)).astype(np.float32))
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    audio_format, channels, rate, _, _, bits = struct.unpack("<HHIIHH", data[20:36])
    assert (audio_format, channels, rate, bits) == (1, 1, 24000, 16)
    assert sf.info(io.BytesIO(data)).frames == 2400
