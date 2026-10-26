# Sequential image processing

Tachyron's processing package is usable without FastAPI, HTTP, a frontend, or
SQLite. Pillow is its only runtime dependency. This is a correctness baseline:
it does not measure benchmark timings, create workers, or compare performance.

## Reproducible example

Run these commands from the repository root in PowerShell or a POSIX shell. Use
fresh directory names when repeating the example; existing datasets/results are
deliberately preserved. The generator creates two tiny images locally:

```powershell
uv sync --locked --project apps/server
uv run --locked --project apps/server python -c "from pathlib import Path; from PIL import Image; p = Path('datasets/example'); p.mkdir(parents=True, exist_ok=False); Image.new('RGB', (12, 8), 'red').save(p / 'red.png'); Image.new('RGB', (8, 12), 'blue').save(p / 'blue.jpg'); Path('results').mkdir(exist_ok=True)"
uv run --locked --project apps/server tachyron-process datasets/example --output results/example-1 --width 4 --height 3
uv run --locked --project apps/server tachyron-process datasets/example --output results/example-2 --width 4 --height 3
```

Both invocations report two successes and zero failures. Each new output directory
contains two 4×3, mode `L` PNG files and `run.json`. Corresponding images and reports
are byte-identical with the same locked environment. `datasets/` and `results/`
are Git-ignored. The CLI also supports `python -m tachyron.cli` with the same arguments.
Use `tachyron-process --help` for the deliberately small argument surface.

The CLI emits one JSON report to stdout. Each failed input has its own stable error
code; absolute dataset paths, decoder messages, and tracebacks are excluded from
expected failure reports. Filenames are JSON-escaped. Exit codes:

| Code | Meaning                                                                         |
| ---- | ------------------------------------------------------------------------------- |
| 0    | Every manifest entry produced a complete output                                 |
| 1    | One or more inputs failed, including total image failure; see outcomes          |
| 2    | Invalid arguments/dataset/output, excessive workload, or run/report I/O failure |
| 130  | Interrupted; completed outputs remain, and no complete report is promised       |

If report publication fails, the run exits with 2 and retains completed images.
An absent `run.json` means the attempt has no complete accounting. Never interpret
the mere presence of a run directory as success. There is no resume/overwrite mode.

## Image contract

Every entry uses the same `resize-grayscale-v1` pipeline:

1. Read the bounded file snapshot and verify its size and SHA256 against the manifest.
2. Accept PNG or JPEG **by content**, regardless of extension. Verify the container
   using Pillow and fully decode it; PNGs must have one leading IHDR, with no
   duplicate or conflicting headers. Reject animation/multiple frames. Permit
   `1`, `L`, `LA`, `P`, `RGB`, `RGBA`, and `CMYK`; reject 16-bit PNGs and
   higher-bit/integer/float modes before Pillow can silently reduce precision.
3. Apply EXIF orientation before resizing, including mirrored orientations.
4. Normalize through RGBA and composite onto opaque white, including palette and
   color-key transparency. Transparency is not retained in output.
5. Resize RGB to exactly `(width, height)` using `Image.Resampling.LANCZOS`.
   Upscaling is allowed. Aspect ratio may change; there is no crop, padding, or fit mode.
6. Convert to 8-bit grayscale using Pillow's ITU-R 601-2 luma conversion
   (`0.299 R + 0.587 G + 0.114 B`, with Pillow's integer rounding).
7. Encode mode `L` PNG with `optimize=False`, compression level 6, and no metadata.

EXIF, ICC profiles, gamma, DPI, text, and other input metadata are discarded.
ICC color management and linear-light resampling are not performed: the pipeline
uses Pillow's normal encoded color conversions, including CMYK to RGB. This is a
fixed processing contract, not an archival or color-proofing workflow.

The [Pillow image reference](https://pillow.readthedocs.io/en/stable/reference/Image.html)
and [EXIF transpose reference](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose)
describe these operations. Output content is deterministic for identical input,
configuration, and software environment. Cross-version/platform codec changes may
affect bytes or pixels; use the locked Pillow version and compare decoded content
when investigating parity. Do not assume JPEG input bytes should equal PNG output.

## Manifest and output ownership

Discovery recursively includes **every regular file**, including files with no
recognized extension. Non-images receive explicit per-file failures rather than
being silently omitted. Empty datasets and unsafe/unreadable/excessive trees fail
discovery before outputs exist. Directory links, file symlinks, Windows junctions,
reparse points, and non-regular files are rejected.

Entries are sorted by relative POSIX path, with compressed byte size and SHA256.
The manifest SHA256 covers the ordered entries, not the machine-specific root.
Relocating an unchanged tree therefore retains its identity. The root is resolved
from the caller's explicit directory selection; relative entry paths cannot leave
that canonical root. Traversal, absolute paths, Windows drive/stream paths, and
backslash paths and non-UTF-8 names in forged entries are rejected on every OS.

Image dimensions are recorded in successful outcomes (before orientation).
Discovery hashes compressed bytes without decoding images. During execution,
changed, removed, or unreadable entries fail individually. Newly added files do
not join an already-created manifest: rediscover explicitly to change the workload.

The caller owns dataset selection. The batch creates exactly one **new** run
directory outside the dataset, with a pre-existing parent; POSIX permissions are
0700 subject to platform semantics. It never deletes inputs, reuses existing run
directories, or overwrites destination files. Windows ACLs come from the filesystem.
Output names are full SHA256 hashes of relative input paths plus `.png`; the report
maps each input to its output. Same stems, nested paths, differing extensions, and
case variants cannot accidentally replace each other. A destination collision
fails explicitly, even if the existing file has the expected name.

Each encoded image/report is written to an exclusive temporary file, closed, then
published through an atomic, non-replacing hard link. Only that call's temporary
file is removed on failure/interruption. If the OS denies temporary-file cleanup,
a warning names the owned leftover file. Completed publications retain their output
mapping and success count; cleanup warnings do not change CLI exit codes. Original
write, collision, and interruption failures still propagate. If stdout reporting
fails, the CLI returns 2; interruption during reporting returns 130. Completed
images and any published `run.json` are retained.

This requires hard-link support, as provided
by NTFS and normal Linux filesystems. Unsupported filesystems fail visibly; there
is no unsafe rename/overwrite fallback. These guarantees cover ordinary operation
and exceptions, not power-loss durability (`fsync` is not promised).

`run.json` contains a schema/pipeline identifier, Pillow version, ordered manifest,
configuration, limits, all outcomes, and success/failure totals. It excludes absolute
paths and timings. API/UI state cannot affect processing or its accounting.

## Resource and trust boundaries

Default `ResourceLimits` are safety caps, not performance budgets:

| Resource                                      | Default cap  |
| --------------------------------------------- | ------------ |
| Candidate files                               | 1,000        |
| Individual compressed file                    | 32 MiB       |
| Aggregate compressed dataset                  | 512 MiB      |
| Image pixels before decoding                  | 16,777,216   |
| Individual input dimension                    | 16,384       |
| Output width and height                       | Each 1–4,096 |
| Aggregate requested grayscale output pixels   | 268,435,456  |
| All discovered entries, including directories | 10,000       |
| Directory depth below dataset root            | 32           |

The output pixel cap bounds the requested raw grayscale workload to 256 MiB,
before PNG overhead. Limits are checked before relevant reads/decodes/output
creation; the sequential runner processes one image at a time. Full compressed snapshots and
several bounded image buffers are held in memory, so this is not a streaming
decoder or an exact RSS quota. Python callers may explicitly supply another
validated `ResourceLimits`; the CLI uses defaults, and reports record the limits.

Pillow decompression-bomb warnings/errors also fail the input. Other decoder
warnings fail closed. The processor does not disable Pillow's limits, enable
truncated-image loading, or modify global decoder settings. Warnings are scoped to
the decode call; this synchronous implementation is not a thread-isolation boundary.

Use local directories controlled by the invoking user. Path-component checks,
no-follow/non-blocking open flags where available, descriptor identity checks,
bounded reads, and content hashes detect unsafe links and observed input edits.
There is no portable guarantee against a hostile process concurrently renaming
ancestor directories or changing filesystem permissions. A future service must
register and isolate its datasets/output roots; this local CLI is not an upload
sandbox. No network access or subprocess execution occurs in processing.

## Python usage and module ownership

```python
from pathlib import Path

from tachyron.processing import TransformConfig, build_manifest, run_sequential

manifest = build_manifest(Path("datasets/example"))
result = run_sequential(manifest, Path("results/python-run"), TransformConfig(4, 3))
assert result.succeeded + result.failed == len(manifest.entries)
```

- `processing/models.py`: immutable configuration, safety limits, manifests, outcomes.
- `processing/dataset.py`: bounded discovery, hashing, path and input revalidation.
- `processing/processor.py`: top-level picklable `process_image` and exclusive writes.
- `processing/__init__.py`: shared run preparation/publication and sequential execution.
- `cli.py`: argument parsing, JSON presentation, exit codes, interruption reporting.

No processing module imports FastAPI or persistence. Process-pool ownership is
implemented separately in [parallel workers](workers.md), using the same image
callable and shared run boundaries. Worker comparisons, timing, telemetry,
experiment statistics, persistence, HTTP jobs, and dashboard workflows remain
deferred. The existing liveness route is unchanged.
