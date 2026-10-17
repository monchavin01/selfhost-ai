from types import SimpleNamespace as NS

from backend.whisper_engine import build_response

WORDS = [NS(start=0.0, end=0.4, word=" สวัสดี", probability=0.9), NS(start=0.4, end=0.9, word="ครับ", probability=0.9)]
SEGMENT = NS(id=0, seek=0, start=0.0, end=0.9, text=" สวัสดีครับ", tokens=[1, 2], avg_logprob=-0.1,
             compression_ratio=1.0, no_speech_prob=0.01, words=WORDS)
INFO = NS(language="th", duration=1.234567)


def test_json_has_only_text():
    assert build_response([SEGMENT], INFO, "json", with_words=False) == {"text": "สวัสดีครับ"}


def test_verbose_json_has_segments_and_no_words_unless_asked():
    result = build_response([SEGMENT], INFO, "verbose_json", with_words=False)
    assert result["language"] == "th" and result["duration"] == 1.235
    assert result["segments"][0]["text"] == "สวัสดีครับ" and result["segments"][0]["start"] == 0.0
    assert "words" not in result


def test_verbose_json_with_word_granularity_lists_words_at_top_level():
    result = build_response([SEGMENT], INFO, "verbose_json", with_words=True)
    assert result["words"] == [{"start": 0.0, "end": 0.4, "word": " สวัสดี"}, {"start": 0.4, "end": 0.9, "word": "ครับ"}]
