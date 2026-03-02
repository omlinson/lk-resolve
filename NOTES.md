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
