import hashlib

import numpy as np
import pytest
import soundfile as sf

from backend.errors import ApiError
from backend.voices import load_voice_bank


def _voice(folder, voice_id, seconds=2.0, text="สวัสดี"):
    sf.write(folder / f"{voice_id}.neutral.th.wav", np.zeros(int(24000 * seconds), dtype="float32"), 24000)
    (folder / f"{voice_id}.neutral.th.txt").write_text(text + "\n", encoding="utf-8")


def _manifest(folder, skip=()):
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}"
             for p in sorted(folder.iterdir()) if p.name not in skip and p.name != "manifest.sha256"]
    (folder / "manifest.sha256").write_text("\n".join(lines) + "\n")


def test_voice_with_matching_hashes_is_served_and_listed(tmp_path):
    _voice(tmp_path, "f_young_01")
    _manifest(tmp_path)
    bank = load_voice_bank(tmp_path)
    assert bank.find("f_young_01").reference_text == "สวัสดี"
    listing = bank.listing()
    assert listing["voices"] == ["f_young_01"] and listing["refused"] == []
    assert listing["sha256"] == hashlib.sha256((tmp_path / "manifest.sha256").read_bytes()).hexdigest()


def test_changed_file_refuses_voice_with_clear_404(tmp_path):
    _voice(tmp_path, "a")
    _voice(tmp_path, "b")
    _manifest(tmp_path)
    (tmp_path / "a.neutral.th.txt").write_text("tampered")
    bank = load_voice_bank(tmp_path)
    assert bank.listing()["refused"] == ["a"] and bank.listing()["voices"] == ["b"]
    with pytest.raises(ApiError) as caught:
        bank.find("a")
    assert caught.value.status == 404 and "hash mismatch" in caught.value.message


def test_voice_missing_from_manifest_or_disk_is_refused(tmp_path):
    _voice(tmp_path, "new")
    _voice(tmp_path, "gone")
    _manifest(tmp_path, skip=("new.neutral.th.wav", "new.neutral.th.txt"))
    (tmp_path / "gone.neutral.th.wav").unlink()
    assert load_voice_bank(tmp_path).listing()["refused"] == ["gone", "new"]


def test_clip_longer_than_15_seconds_is_refused(tmp_path):
    _voice(tmp_path, "long", seconds=15.5)
    _manifest(tmp_path)
    assert "long" in load_voice_bank(tmp_path).refused


def test_no_manifest_refuses_everything(tmp_path):
    _voice(tmp_path, "a")
    assert load_voice_bank(tmp_path).listing() == {"sha256": "", "voices": [], "refused": ["a"]}


def test_voice_id_parsing(tmp_path):
    _voice(tmp_path, "m_01")
    _manifest(tmp_path)
    bank = load_voice_bank(tmp_path)
    assert bank.find("default", "m_01").id == "m_01"
    for bad, status in (("../etc/passwd", 400), ("", 400), ("nobody", 404)):
        with pytest.raises(ApiError) as caught:
            bank.find(bad)
        assert caught.value.status == status
    with pytest.raises(ApiError) as caught:
        bank.find("default", "")  # DEFAULT_VOICE unset
    assert caught.value.status == 400
