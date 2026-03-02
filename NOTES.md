# Other Issues Found in resolve_pipeline.py

## 1. `st_birthtime` is macOS-only (line 108)

```python
], key=lambda f: os.stat(f).st_birthtime)
```

`os.stat().st_birthtime` is only available on macOS (and Windows). On Linux it raises
`AttributeError`. Since this pipeline targets macOS + DaVinci Resolve, this is not a
blocker today, but would break if the script were ever run on Linux. A portable
alternative would be `os.path.getmtime(f)` (modification time), or wrapping in a
try/except that falls back to `st_mtime`.

## 2. Cam folder validation could be clearer

If only some CAM folders are missing (e.g. "Cam 2" doesn't exist), they get a count of
0. The validation at line 136 then reports "mismatched file counts" which is technically
correct but could be confusing — the real problem is a missing folder, not a count
mismatch. Consider checking for missing folders as a distinct error before comparing
counts.

## 3. `silence_thresh` default mismatch between docstring and argparse

The docstring at line 28 says the default `--silence_thresh` is `0.01`, but the
argparse default at line 459 is `0.001`. One of these is wrong.

## 4. `AddItemListToMediaPool` varargs vs list (FIXED)

The Resolve API signature is `AddItemListToMediaPool(item1, item2, ...)` (varargs), but
the code was passing a Python list as a single argument. Combined with spaces in the
session path (e.g. `/Volumes/LaCie 4/YT Videos 2026/...`), batch imports failed
silently. Fixed by importing audio files one at a time with a dict-format `ImportMedia`
fallback.

## 5. `place_clips_on_track` zero-duration guard (FIXED)

If `GetClipProperty("Duration")` returned something unparseable, `endFrame` was set to
0, producing an invisible zero-length clip on the timeline. Fixed by omitting
`startFrame`/`endFrame` when duration is unknown, letting Resolve use the full clip.

## 6. Track count comment is wrong (cosmetic)

Line ~394 says "3 video + 4 audio" but only 3 audio tracks are used (TrLR, Tr1, Tr2).
The 4th audio track is created but unused.
