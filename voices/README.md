# voices/

The F5 voice bank. Only this README and `manifest.sha256` are in git: the clips are cut from real dubbed audio,
so they stay on your machine and are copied to the rented box by `make vast-voices` (`make vast-up` does it too).

```
voices/
  <voice_id>.neutral.th.wav   24 kHz, mono, 16-bit, at most 15 s
  <voice_id>.neutral.th.txt   the exact words spoken in the clip
  manifest.sha256             sha256 of every wav + txt (make voices)
```

`voice_id` is the file name before the first `.` (`m_young_01.neutral.th.wav` is `m_young_01`). On the box the
bank lives in `/data/voices`; the server waits for it at start and refuses any voice whose files do not match the
manifest.
