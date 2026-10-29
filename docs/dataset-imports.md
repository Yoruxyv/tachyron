# Local dataset folder imports

Experiments can import original image files into private, application-managed
storage. Environment-registered datasets continue to use the existing startup
discovery and job contracts. Neither route changes benchmark transformations,
manifest hashing, repetitions, timing boundaries or the fixed 1/2/4/8-worker plan.

## Browser workflow

The compact **Import folder** button immediately activates the hidden native
directory picker. Its adjacent PNG/JPG/JPEG label is guidance; extensions and
browser signatures do not establish validity. No intermediate screen opens.
Current Chromium, Firefox and Safari directory-input implementations are required;
the native picker and empty-directory visibility vary by browser and platform.

After selection, a contextual review lists relative paths and byte sizes, with
50 files per page. Every selected entry starts included. Unsupported or unreadable
entries require explicit exclusion; Tachyron never silently drops them. Confirm
the included files and exclusion count before uploading. Exclusions record user
intent, not proof of the source folder's complete contents. The browser cannot
reliably establish original filesystem symlink identity; the server only accepts
bounded file bytes and creates its own regular files.

A browser worker hashes originals one file at a time. XMLHttpRequest supplies
actual transfer progress. Transfer completion is distinct from server validation
and durable registration. A READY receipt adds the immutable dataset to the
catalog and selects it immediately when benchmark configuration is unlocked.
Confirmed registrations survive older catalog responses. A new folder clears
prior review/confirmation, including selection of the same folder again.

Session storage retains the operation UUID for status recovery in the same tab.
It retains neither File objects nor partial bytes. A reload during transfer can
fail that operation; after complete receipt, server validation continues without
the browser. Check status after a lost response. An unconfirmed outcome keeps
admission in the UI closed; it is never silently retried with another identity.
If POST returns a recognized pre-admission rejection and a subsequent lookup
confirms no receipt, the UI releases that rejected identity for an explicit retry.
Network failures, malformed proxy responses and unknown outcomes retain the UUID;
a 404 observed while the POST outcome is unknown is not proof of non-admission.

## Protocol

All mutating requests require `X-Tachyron-Request: 1`; see the
[local request boundary](api.md#local-request-boundary). Clients never supply a
server path. All counts below refer to original compressed file bytes.

| Route                                             | Result                                                                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `GET /api/dataset-imports/policy`                 | Schema v1, current operational limits, `metadata_bytes: 524288`, `formats: ["PNG", "JPEG"]`                        |
| `POST /api/dataset-imports`                       | New complete import: `201` and READY receipt; replay: `200` and the existing receipt; `Location` identifies status |
| `GET /api/dataset-imports/{operation_id}`         | `200` durable receipt, with live progress when available; unknown UUID: `404 import_not_found`                     |
| `POST /api/dataset-imports/{operation_id}/cancel` | Active: `202` with durable cancellation intent; terminal: `200` unchanged receipt                                  |

POST requires a lowercase canonical UUID in `Idempotency-Key`. This UUID is also
the operation ID. `X-Tachyron-Metadata-SHA256` is the lowercase SHA256 of the
**exact UTF-8 metadata bytes**, including whitespace. The MIME boundary must be
1–70 printable ASCII characters. Content encoding must be absent or `identity`.

The single multipart body contains `metadata` first, then exactly `file-0` through
`file-(n-1)` in metadata order, and a complete closing boundary. Metadata is a text
field with no filename. File-part filenames are ignored. Only Content-Disposition
and optional Content-Type headers are accepted, with no duplicate headers.

```json
{
  "schema_version": 1,
  "display_name": "Camera sample",
  "selection": { "mode": "explicit-subset", "excluded_count": 1 },
  "files": [
    {
      "part": "file-0",
      "relative_path": "day-1/image.png",
      "size_bytes": 1234,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
```

The example digest and size are illustrative, not an uploadable image. With no
exclusions use `whole-folder` and zero `excluded_count`. Unknown fields, duplicate
JSON members, coercion, unsafe paths, inconsistent selection, missing/extra parts,
digest/length mismatches and truncated MIME framing are rejected.

An operation receipt has this shape (progress is illustrative):

```json
{
  "schema_version": 1,
  "operation_id": "12345678-1234-4234-8234-123456789abc",
  "state": "VALIDATING",
  "created_at": "2026-10-28T00:00:00+00:00",
  "ended_at": null,
  "cancel_requested": false,
  "progress": { "total_files": 1, "received_files": 1, "validated_files": 0 },
  "dataset": null,
  "error_code": null,
  "file_index": null
}
```

READY includes the same dataset object returned by `GET /api/datasets`:
`dataset_id`, `manifest_sha256`, `input_count`, `total_bytes`, `error_code` and
additive `display_name`, `source`, `selection_mode`, `excluded_count` fields.
Imported IDs are `import-` plus the operation UUID without hyphens. Identical
content imported under a new key creates a distinct registration; names may
repeat and never replace a dataset. Environment IDs retain their existing rules;
startup rejects a collision with a managed ID.
Admission also rejects a new import whose derived ID already belongs to an
environment dataset (`409 dataset_id_conflict`), without replacing either workload.

Replay binds the key to the original metadata digest and returns that operation's
current receipt without consuming another body. A different digest yields
`409 idempotency_conflict`. Terminal READY/FAILED/CANCELLED receipts never change.
Explicit retry after failure or cancellation uses a **new UUID and key**. Failed
pre-admission requests may have no receipt. The UUID must be saved before POST;
GET resolves a lost response even when commit succeeded before the socket failed.

Errors expose stable codes and, when applicable, a zero-based `file_index`, never
paths, filenames, SQL, decoder messages or request contents. Typical rejections
are `413 upload_limit_exceeded`, `422 invalid_path/path_conflict/invalid_image/
unsupported_image/resource_limit`, `408 upload_interrupted/upload_timeout`,
`409 workload_busy/dataset_id_conflict`, `429 import_busy`, `507 dataset_capacity/catalog_capacity/
insufficient_storage`, and `503 catalog_unavailable/worker_unavailable`. Details
of an admitted failure remain available in its immutable receipt.

## Limits and image acceptance

| Resource                                  | Initial limit                                                       |
| ----------------------------------------- | ------------------------------------------------------------------- |
| Included files                            | 1,000                                                               |
| One original file                         | 32 MiB                                                              |
| Sum of original file bytes                | 512 MiB                                                             |
| Complete raw multipart body               | 521 MiB                                                             |
| Metadata                                  | 512 KiB                                                             |
| Headers per part                          | 8, with 8 KiB aggregate field/value bytes and an 8 KiB maximum line |
| Managed registrations                     | 32, separately from environment registrations                       |
| Accounted managed file bytes              | 4 GiB, including unfinished reservations                            |
| Catalog                                   | 64 MiB SQLite page budget                                           |
| Receive inactivity / total receive budget | 30 seconds / 10 minutes                                             |
| Helper progress watchdog                  | 60 seconds per image or protocol exchange                           |

The raw body limit includes metadata, headers, boundaries and framing, not just
images. A conservative envelope is
`512 MiB + 512 KiB + 1001 × (8192 + 76) + 76 + 65536 = 545737080 bytes`,
below 521 MiB (546308096 bytes). Tests also parse 1,000 real small files with
maximum-length header lines, metadata and boundaries. Both declared and observed
body sizes are checked; a small or absent Content-Length cannot bypass streaming
accounting. Pipe frames carry at most 64 KiB of file transfer at a time; metadata
buffering is bounded independently. The ASGI server also owns its network buffers.
Helper JSON frames use UTF-8 without ASCII escaping, so accepted Unicode paths
cannot expand beyond the frame limit solely through escape encoding.

The receive budget is an operational bound, not a transfer-speed guarantee. The
validation watchdog resets only after another image validates. It therefore
allows progress across 1,000 legitimate images without an arbitrary 120-second
dataset-wide cutoff. The browser uses a separate upload deadline derived from
receive budget plus per-image watchdogs, rather than the ten-second JSON timeout.
Watchdogs can reject a valid file on a sufficiently slow or overloaded host.

Pillow uses the existing PNG/JPEG decoder, verifies and fully loads each image,
and applies the existing single-frame/mode, decompression-bomb, 16,777,216-pixel
and 16,384-dimension checks. Original bytes are preserved. No re-encoding,
thumbnail, EXIF rewrite or new processing pipeline is introduced. Resource caps
are neither performance guarantees nor OS memory/CPU/physical-disk quotas.

## Durable storage and publication

`TACHYRON_DATASET_DIR` defaults to `datasets/imported`, independently of
`TACHYRON_DATA_DIR` (history and benchmark outputs). Embedded Settings with no
explicit dataset directory uses a sibling `<history-name>-datasets` directory.
Managed storage, history and all environment dataset roots must not overlap.

```text
datasets/imported/
  catalog.sqlite3
  objects/<operation-uuid>/
    .owner
    files/<validated-relative-path>
```

There is no public/static file route. Every import gets a new private directory;
MIME filenames never choose storage destinations. The directory remains at the
same path across finalization, avoiding rename/publication ambiguity. File writes
are flushed and fsynced before validation completes. Directory fsync is available
on POSIX; Windows relies on flushed file handles and SQLite FULL synchronization.
This is normal process-crash recovery, not a universal power-loss guarantee.
Back up the complete managed directory while stopped. Removing only the catalog
does not rediscover or silently adopt leftover files.

The separate SQLite catalog has `user_version = 1`:

- `operations(id, digest, state, document, nonce)`: permanent UUID/digest binding,
  fixed 8 KiB receipt and unguessable private ownership token. A trigger forbids
  terminal updates; a partial unique index permits one active operation.
- `datasets(id, operation, info, manifest)`: unique immutable registration and
  root-independent manifest snapshot, linked to exactly one operation.
- `work(operation, reserved, pid, born, headroom)`: unfinished byte reservation,
  helper PID/creation time and a preallocated 2 MiB terminal-transition reserve.

One serialized, exclusively locked connection owns the catalog. Future schema
versions require migrations; unsupported versions fail startup. Admission requires
room for a permanent receipt, 2 MiB reserve and 1 MiB margin before accepting bytes.
READY frees the reserve and inserts the bounded manifest, dataset and READY receipt
in one transaction. FAILED/CANCELLED need no larger receipt row. A full catalog
rejects new operations with `507 catalog_capacity`; existing datasets, receipts and
the independent history remain readable. Receipts do not expire. Eventual catalog
exhaustion requires an operator/product decision; v1 has no destructive pruning.
Free-space checks are best effort. External disk use can still cause I/O failure.

SQLite is the sole publication authority. Dataset listing, status and job admission
read the committed registration directly. There is no separate managed registry
cache to publish. A READY commit therefore survives a failed HTTP response or
cleanup. Job admission stores the immutable manifest hash and existing execution
revalidates input bytes; later filesystem tampering fails rather than refreshing
the workload's identity. Imported content is immutable through the application;
the local OS account remains trusted to preserve its private storage.

## Lifecycle, locks and recovery

Transitions are `RECEIVING -> VALIDATING -> READY`, or from either active state to
FAILED/CANCELLED. Cancellation records intent under the same session mutex used
for publication. If it wins, READY is forbidden. If READY wins, cancellation
returns that receipt unchanged. The cancellation response confirms intent, not
instant resource release. Aborting the browser socket before its cancel request
arrives can instead leave an immutable FAILED/upload_interrupted receipt.

Coordinator admission uses one condition lock for jobs and imports. It checks
queued and active jobs, creates the receipt and attaches a cancellable session
**before** helper startup. Imports exclude new benchmark jobs; accepted queued jobs
exclude imports. The helper, not the API, holds the existing account-wide
experiment lock through private writes, validation and release. Cooperating CLI
experiments and APIs with another history directory are also excluded. Browser
review stays lightweight; new hashing/import confirmation is disabled during a
known active benchmark. Unrelated processes and API polling can still affect load.

Import cancellation uses the same coordinator condition as admission, so receipt
creation and session attachment cannot expose an uncancellable startup gap.
Lock order is coordinator condition then session mutex then catalog for import
cancellation; admission uses condition then catalog, and publication uses session
mutex then catalog. Catalog methods never
acquire either parent lock. No admission/catalog/session lock is held while waiting
for pipe I/O, helper termination or thread joins. Cleanup obtains the account lock
after catalog reads finish. The helper never accesses SQLite or coordinator locks.

After its complete-manifest message, the helper performs no further file writes
and waits for release. The parent confirms helper exit before cleanup or releasing
admission. An unconfirmed exit closes further admission and retains ownership;
shutdown can retry reaping without opening another worker. Idle receive observes
cancellation, including shutdown, without waiting for the inactivity timeout.

Before complete body EOF, disconnect fails and cleans owned staging. After EOF,
validation/publication continue without a disconnect listener. ASGI task/server
cancellation is a separate interruption, and cannot abandon a mutating thread.
Recovery resolves any remaining nonterminal receipt after the prior owner exits.

The durable PID and creation time address a parent dying during native validation.
A child-written bounded marker corroborates operation, nonce, PID and creation
time before recovery can kill a live spawn helper. A recycled PID is never killed.
A child that never received `go` exits on pipe EOF; missing/unproven identity fails
closed rather than targeting another process. Live request cleanup uses its own
Process object. This is best-effort supervision, not a Windows Job Object, Linux
cgroup, codec sandbox, hard resource isolation or protection against hostile code
running as the same OS account.
Recovery observes process exit without consuming another owner's POSIX wait status.
A zombie has already stopped executing and released its file locks; its original
parent or the OS reaper retains responsibility for collecting the exit status.
A broken parent pipe also stops the helper at its next progress exchange; native
decode already in progress still requires supervised termination or completion.

Restart reaps verified helpers, freezes unfinished operations and removes only
proven failed/cancelled namespaces under the account lock. READY data is never
deleted. Unknown directories, wrong markers, links and reparse points are retained
and can require operator repair. Cleanup failure retains reservations/work for
restart instead of claiming that storage was reclaimed. Recursive removal first
checks every descendant for links; filesystem tampering by the trusted account
during that check is outside the security boundary.

There is no resumability, replacement, deletion, archive extraction, cloud storage,
multi-host worker scheduling or generic upload framework in v1.
