"""Voice bank: `<id>.neutral.th.wav` + `.txt`, trusted only if both files match voices/manifest.sha256."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ApiError

MANIFEST = "manifest.sha256"
CLIP_SUFFIX, TEXT_SUFFIX = ".neutral.th.wav", ".neutral.th.txt"
MAX_REFERENCE_S = 15.0  # the library cuts a longer clip but keeps the whole transcript, which then leaks into the speech
VOICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class Voice:
    id: str
    clip: Path
    reference_text: str


@dataclass
class VoiceBank:
    voices: dict[str, Voice] = field(default_factory=dict)
    refused: dict[str, str] = field(default_factory=dict)  # voice id -> why
    manifest_sha256: str = ""

    def listing(self) -> dict:
        return {"sha256": self.manifest_sha256, "voices": sorted(self.voices), "refused": sorted(self.refused)}

    def find(self, voice_id: str, default_voice: str = "") -> Voice:
        if voice_id == "default":
            voice_id = default_voice
            if not voice_id:
                raise ApiError(400, "voice 'default' requested but DEFAULT_VOICE is not set")
        if not VOICE_ID.match(voice_id):
            raise ApiError(400, f"invalid voice id {voice_id!r}")
        if voice_id in self.refused:
            raise ApiError(404, f"voice {voice_id!r} refused: {self.refused[voice_id]}")
        if voice_id not in self.voices:
            raise ApiError(404, f"unknown voice {voice_id!r}")
        return self.voices[voice_id]


def parse_manifest(text: str) -> dict[str, str]:
    """`sha256sum` lines: `<hex>  <filename>` (a `*` before the name marks binary mode)."""
    entries = {}
    for line in text.splitlines():
        if line.strip():
            digest, _, name = line.partition(" ")
            entries[name.strip().lstrip("*")] = digest.lower()
    return entries


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _problem(voices_dir: Path, name: str, expected: dict[str, str]) -> str | None:
    if name not in expected:
        return f"{name} not in {MANIFEST}"
    path = voices_dir / name
    if not path.is_file():
        return f"{name} missing"
    if _sha256(path) != expected[name]:
        return f"{name} hash mismatch"
    return None


def load_voice_bank(voices_dir: Path) -> VoiceBank:
    import soundfile as sf

    manifest_file = voices_dir / MANIFEST
    bank = VoiceBank()
    expected: dict[str, str] = {}
    if manifest_file.is_file():
        bank.manifest_sha256 = _sha256(manifest_file)
        expected = parse_manifest(manifest_file.read_text(encoding="utf-8"))
    names = {p.name for p in voices_dir.glob("*")} | set(expected)
    ids = {n[: -len(s)] for n in names for s in (CLIP_SUFFIX, TEXT_SUFFIX) if n.endswith(s)}
    for voice_id in sorted(ids):
        clip, text = voices_dir / (voice_id + CLIP_SUFFIX), voices_dir / (voice_id + TEXT_SUFFIX)
        reason = (_problem(voices_dir, clip.name, expected) or _problem(voices_dir, text.name, expected))
        if reason is None:
            seconds = sf.info(str(clip)).duration
            reference_text = text.read_text(encoding="utf-8").strip()
            if seconds > MAX_REFERENCE_S:
                reason = f"clip is {seconds:.1f} s, at most {MAX_REFERENCE_S:.0f} s allowed"
            elif not reference_text:
                reason = f"{text.name} is empty"
        if reason:
            bank.refused[voice_id] = reason
        else:
            bank.voices[voice_id] = Voice(voice_id, clip, reference_text)
    return bank
