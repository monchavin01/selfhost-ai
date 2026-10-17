"""F5-TTS-THAI inference, mirroring the known-good ThaiDub reference (f5.py) but batching chunks on the GPU.
Only imported on the GPU host; torch and f5_tts_th are never imported at module level of anything else."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .f5_jobs import ChunkJob
from .voices import Voice

log = logging.getLogger("backend.f5")
SAMPLE_RATE = 24000
MAX_DURATION_FRAMES = 4096  # CFM.sample's own default clamp
MODEL_SHAPE = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, text_mask_padding=False,
                   conv_layers=4, pe_attn_head=1)  # the "v1" model of f5_tts_th.TTS.load_f5tts


class _PerItemSeed:
    """Stands in for `torch` inside f5_tts_th.cfm during one sample() call. CFM.sample seeds every batch item with the
    SAME seed (or none), so an item's noise would depend on its neighbours. Here item i draws its noise right after
    manual_seed(seeds[i]) - exactly what the single-line reference does - so a line sounds the same in any batch."""

    def __init__(self, torch: Any, seeds: Sequence[int]) -> None:
        self._torch, self._seeds = torch, iter(seeds)

    def randn(self, *args: Any, **kwargs: Any) -> Any:  # sample() draws once per item, in batch order
        self._torch.manual_seed(next(self._seeds))
        return self._torch.randn(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._torch, name)


class F5Engine:
    def __init__(self, model_dir: Path, vocoder_dir: Path, nfe_step: int = 10, device: str | None = None) -> None:
        import torch
        from f5_tts_th import utils_infer as ui
        from f5_tts_th.normalize import normalize_text
        from f5_tts_th.utils import convert_char_to_pinyin

        self.torch, self.ui = torch, ui
        self.device, self.nfe_step = device or ("cuda" if torch.cuda.is_available() else "cpu"), nfe_step
        self._normalize, self._pinyin = normalize_text, convert_char_to_pinyin
        # the package picks its device at import, so pass it everywhere
        model = ui.load_model(MODEL_SHAPE, str(model_dir / "model_1000000.pt"), mel_spec_type="vocos",
                              vocab_file=str(model_dir / "vocab.txt"), device=self.device)
        self.model = model.float()  # load_model forces fp16
        self.vocoder = ui.load_vocoder("vocos", is_local=True, local_path=str(vocoder_dir), device=self.device)
        self._cfm_module = sys.modules[type(self.model).__module__]
        self._references: dict[str, tuple[Any, int, list[str]]] = {}  # voice id -> (mel, frames, reference tokens)

    def split_long_run(self, piece: str, max_chars: int) -> list[str]:
        return self.ui.chunk_text(piece, max_chars)

    def _reference(self, voice: Voice) -> tuple[Any, int, list[str]]:
        """The clip cut and levelled as the package does (edge trim, mono, rms, 24 kHz) and turned into the mel the
        model conditions on, once per voice."""
        if voice.id not in self._references:
            import soundfile as sf
            import torchaudio

            torch, ui = self.torch, self.ui
            cut, _ = ui.preprocess_ref_audio_text(str(voice.clip), "x", show_info=lambda *a: None)  # "x": no recogniser
            data, rate = sf.read(cut, dtype="float32", always_2d=True)
            if cut != str(voice.clip):
                os.unlink(cut)  # the package leaves its temp file behind
            audio = torch.from_numpy(data.T.copy()).mean(0, keepdim=True)
            rms = float(torch.sqrt(torch.mean(audio ** 2)))
            if 0 < rms < ui.target_rms:
                audio = audio * ui.target_rms / rms
            if rate != SAMPLE_RATE:
                audio = torchaudio.transforms.Resample(rate, SAMPLE_RATE)(audio)
            audio = audio.to(self.device)
            with torch.inference_mode():
                mel = self.model.mel_spec(audio).permute(0, 2, 1)
            tokens = self._pinyin([voice.reference_text])[0]
            while tokens and tokens[0] == " ":
                tokens = tokens[1:]
            self._references[voice.id] = (mel, audio.shape[-1] // ui.hop_length, tokens)
        return self._references[voice.id]

    def _generated_frames(self, text: str, speed: float) -> tuple[list[str], int]:
        generated = self._normalize(text)
        frames = int(len(self.ui.th_to_g2p(generated)) * 5)  # the package's own length estimate: natural speaking rate
        frames = frames * 2 if frames < 50 else frames
        return list(generated), int(frames / speed)  # fewer frames for the same text: the model speaks it faster

    def synthesize_batch(self, jobs: Sequence[ChunkJob]) -> list[np.ndarray]:
        """One model.sample() call for all jobs; returns one float32 24 kHz wave per job, in order."""
        torch = self.torch
        refs = [self._reference(job.voice) for job in jobs]
        gens = [self._generated_frames(job.text, job.speed) for job in jobs]
        texts = [ref_tokens + [" ", " "] + generated for (_, _, ref_tokens), (generated, _) in zip(refs, gens)]
        ref_frames = [frames for _, frames, _ in refs]
        durations = [ref + frames for ref, (_, frames) in zip(ref_frames, gens)]
        cond = torch.zeros(len(jobs), max(ref_frames), refs[0][0].shape[-1], device=self.device)
        for row, (mel, frames, _) in zip(cond, refs):
            row[:frames] = mel[0, :frames]
        began = time.perf_counter()
        swapped, self._cfm_module.torch = self._cfm_module.torch, _PerItemSeed(torch, [j.seed for j in jobs])
        try:
            with torch.inference_mode():
                out, _ = self.model.sample(
                    cond=cond, text=texts, duration=torch.tensor(durations, device=self.device),
                    lens=torch.tensor(ref_frames, device=self.device), steps=self.nfe_step, cfg_strength=2.0,
                    sway_sampling_coef=-1.0)
                sampled = time.perf_counter()
                waves = []
                for i, text in enumerate(texts):
                    # sample() lifts each length to at least text/prompt length + 1 and clamps it
                    end = min(max(max(len(text), ref_frames[i]) + 1, durations[i]), MAX_DURATION_FRAMES)
                    mel = out[i:i + 1, ref_frames[i]:end, :].to(torch.float32).permute(0, 2, 1)
                    waves.append(self.vocoder.decode(mel).squeeze().cpu().numpy().astype(np.float32))
        finally:
            self._cfm_module.torch = swapped
        log.info("f5 batch=%d sample_s=%.2f vocoder_s=%.2f gen_frames=%s", len(jobs), sampled - began,
                 time.perf_counter() - sampled, [d - r for d, r in zip(durations, ref_frames)])
        return waves
