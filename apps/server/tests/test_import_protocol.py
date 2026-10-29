# Copyright (c) 2026 Hans Valerie
"""Small bounded fixtures for multipart, policy boundaries and image acceptance."""

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tachyron.api import create_app
from tachyron.imports import service
from tachyron.imports.models import (
    CHUNK,
    FRAME_BYTES,
    METADATA_BYTES,
    DatasetImportError,
    ImportLimits,
    Metadata,
)
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tachyron.imports.worker import Channel, Upload, send
from tachyron.processing import build_manifest
from tests.test_dataset_imports import payload, png, settings


class Acknowledgements:
    """Acknowledge parsed metadata without a process for parser boundary tests."""

    def send_bytes(self, buf: bytes) -> None:
        del buf

    def recv_bytes(self, maxlength: int) -> bytes:
        del maxlength
        return b"continue"

    def poll(self, timeout: float) -> bool:
        del timeout
        return True

    def close(self) -> None:
        pass


def test_unicode_metadata_within_policy_fits_the_helper_frame() -> None:
    document: dict[str, object] = {
        "schema_version": 1,
        "display_name": "Unicode",
        "selection": {"mode": "whole-folder"},
        "files": [
            {
                "part": f"file-{i}",
                "relative_path": "😀" * 29 + "/" + "😀" * 58 + f"{i}.png",
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
            for i in range(1000)
        ],
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= METADATA_BYTES
    metadata = Metadata.model_validate_json(encoded)
    metadata.check(ImportLimits())
    root = Path("/x/objects/00000000-0000-0000-0000-000000000000/files")
    assert all(
        len(str(root / item.relative_path).encode("utf-16-le")) // 2 <= 240
        for item in metadata.files
    )
    channel = Mock(spec=Channel)
    send(channel, {"kind": "metadata", "value": metadata.model_dump(mode="json")})
    sent: bytes = channel.send_bytes.call_args.args[0]
    assert len(sent) <= FRAME_BYTES


def test_broken_parent_pipe_aborts_helper_progress() -> None:
    channel = Mock(spec=Channel)
    channel.send_bytes.side_effect = BrokenPipeError
    with pytest.raises(BrokenPipeError):
        send(channel, {"kind": "validated", "count": 1})


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 64, CHUNK])
def test_every_framing_boundary_can_cross_chunks(
    tmp_path: Path, chunk_size: int
) -> None:
    body, headers, _ = payload({"a.png": png(), "nested/b.png": png()})
    upload = Upload(
        Acknowledgements(),
        tmp_path,
        headers["X-Tachyron-Metadata-SHA256"],
        "test",
        ImportLimits(),
    )
    try:
        for offset in range(0, len(body), chunk_size):
            upload.parser.write(body[offset : offset + chunk_size])
        assert upload.finish().sha256 == build_manifest(tmp_path).sha256
    finally:
        upload.close()


def test_full_file_count_large_metadata_and_long_headers_fit_envelope(
    tmp_path: Path,
) -> None:
    count = 1000
    image = png()
    metadata = (
        json.dumps(
            {
                "schema_version": 1,
                "display_name": "Envelope",
                "selection": {"mode": "whole-folder"},
                "files": [
                    {
                        "part": f"file-{i}",
                        "relative_path": f"{i}.png",
                        "size_bytes": len(image),
                        "sha256": hashlib.sha256(image).hexdigest(),
                    }
                    for i in range(count)
                ],
            }
        )
        .encode()
        .ljust(512 * 1024, b" ")
    )
    boundary = "x" * 70
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'.encode(),
        metadata,
        b"\r\n",
    ]
    for i in range(count):
        # A header line at the parser's actual 8192-byte maximum, excluding CRLF.
        header = f'Content-Disposition: form-data; name="file-{i}"; filename="'.encode()
        header += b"x" * (8192 - len(header) - 1) + b'"'
        parts.extend(
            (f"--{boundary}\r\n".encode(), header, b"\r\n\r\n", image, b"\r\n")
        )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    overhead = len(body) - count * len(image)
    assert overhead + 512 * 1024 * 1024 <= ImportLimits().max_body_bytes
    upload = Upload(
        Acknowledgements(),
        tmp_path,
        hashlib.sha256(metadata).hexdigest(),
        boundary,
        ImportLimits(),
    )
    try:
        for offset in range(0, len(body), CHUNK):
            upload.parser.write(body[offset : offset + CHUNK])
        assert len(upload.finish().entries) == count
    finally:
        upload.close()


@pytest.mark.parametrize(
    "kind",
    [
        "metadata-hash",
        "duplicate-part",
        "duplicate-header",
        "long-header",
        "empty-file",
        "wrong-size",
    ],
)
def test_protocol_rejections_leave_terminal_receipts(tmp_path: Path, kind: str) -> None:
    body, headers, identifier = payload({"a.png": png(), "b.png": png()})
    if kind == "metadata-hash":
        headers["X-Tachyron-Metadata-SHA256"] = "0" * 64
    elif kind == "duplicate-part":
        body = body.replace(b'name="file-1"', b'name="file-0"')
    elif kind == "duplicate-header":
        body = body.replace(
            b'name="metadata"\r\n',
            b'name="metadata"\r\nContent-Disposition: form-data; name="metadata"\r\n',
        )
    elif kind == "long-header":
        body = body.replace(b'filename="ignored"', b'filename="' + b"x" * 8192 + b'"')
    elif kind == "empty-file":
        body = body.replace(png(), b"")
    else:
        body = body.replace(png(), png() + b"x")
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code >= 400
        assert str(tmp_path) not in response.text
        receipt = client.get(f"/api/dataset-imports/{identifier}").json()
        assert receipt["state"] == "FAILED"
        assert (
            client.post("/api/dataset-imports", content=body, headers=headers).json()
            == receipt
        )
        assert client.get("/api/datasets").json() == []
    assert not list((tmp_path / "managed/objects").iterdir())


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_complete_body_cap_is_inclusive(tmp_path: Path, delta: int) -> None:
    body, headers, _ = payload({"a.png": png()})
    with TestClient(
        create_app(settings(tmp_path, max_body_bytes=len(body) + delta)),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code == (413 if delta == -1 else 201)


@pytest.mark.parametrize(
    "kind", ["pixel-limit", "animated", "high-depth", "jpeg", "gif"]
)
def test_server_content_acceptance_matches_existing_decoder(
    tmp_path: Path, kind: str
) -> None:
    image_format = "JPEG" if kind == "jpeg" else "GIF" if kind == "gif" else "PNG"
    mode = "I;16" if kind == "high-depth" else "RGB"
    dimensions = (4097, 4096) if kind == "pixel-limit" else (4, 3)
    with (
        Image.new(mode, dimensions) as image,
        Image.new(mode, dimensions, 1) as second,
        io.BytesIO() as stream,
    ):
        options: dict[str, object] = (
            {"save_all": True, "append_images": [second]} if kind == "animated" else {}
        )
        image.save(stream, format=image_format, **options)
        data = stream.getvalue()
    # Extensions remain guidance: PNG/JPEG content is authoritative.
    body, headers, identifier = payload({"image.jpg": data})
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code == (201 if kind == "jpeg" else 422)
        receipt = client.get(f"/api/dataset-imports/{identifier}").json()
        if kind == "pixel-limit":
            assert receipt["error_code"] == "resource_limit"
        elif kind in ("animated", "high-depth"):
            assert receipt["error_code"] == "unsupported_image"


def test_partial_metadata_buffers_are_bounded(tmp_path: Path) -> None:
    upload = Upload(
        Acknowledgements(),
        tmp_path,
        "0" * 64,
        "test",
        replace(ImportLimits(), max_files=1),
    )
    try:
        upload.parser.write(
            b'--test\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
        )
        for _ in range(8):
            upload.parser.write(b" " * CHUNK)
        with pytest.raises(DatasetImportError, match="upload_limit_exceeded"):
            upload.parser.write(b" " * CHUNK)
        assert len(upload.metadata_bytes) <= 512 * 1024
    finally:
        upload.close()


@pytest.mark.parametrize("stalled", [False, True])
def test_validation_guard_allows_progress_beyond_two_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stalled: bool
) -> None:
    body, headers, identifier = payload({f"{i}.png": png() for i in range(3)})
    raw = body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--test", 1)[0]
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    session.metadata = Metadata.model_validate_json(raw)
    channel = Mock(spec=Channel)
    session.connection = channel
    elapsed = 0.0

    def poll(_seconds: float) -> bool:
        nonlocal elapsed
        elapsed += 61 if stalled else 45
        return not stalled

    monkeypatch.setattr(service, "monotonic", lambda: elapsed)
    channel.poll.side_effect = poll
    channel.recv_bytes.side_effect = [
        json.dumps({"kind": "validated", "count": i}).encode() for i in range(1, 4)
    ] + [b'{"kind":"complete","manifest":"unused"}']
    try:
        if stalled:
            with pytest.raises(DatasetImportError, match="validation_timeout"):
                session.exchange(b"finish", "complete")
        else:
            assert session.exchange(b"finish", "complete")["kind"] == "complete"
            assert elapsed > 120
            assert session.progress.validated_files == 3
    finally:
        catalog.finish(identifier, "test_finished")
        session.close()
        catalog.close()


@pytest.mark.parametrize("counts", [[0], [2], [1, 1], [1, 2, 3, 4]])
def test_only_sequential_validation_acknowledgements_are_accepted(
    tmp_path: Path, counts: list[int]
) -> None:
    body, headers, identifier = payload({f"{i}.png": png() for i in range(3)})
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    session.metadata = Metadata.model_validate_json(
        body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--test", 1)[0]
    )
    channel = Mock(spec=Channel)
    channel.poll.return_value = True
    channel.recv_bytes.side_effect = [
        json.dumps({"kind": "validated", "count": count}).encode() for count in counts
    ]
    session.connection = channel
    try:
        with pytest.raises(DatasetImportError, match="worker_protocol"):
            session.exchange(b"finish", "complete")
        assert session.progress.validated_files == (
            counts[-2] if len(counts) > 1 else None
        )
        assert catalog.get(identifier).state == "RECEIVING"
    finally:
        catalog.finish(identifier, "test_finished")
        session.close()
        catalog.close()


def test_metadata_capacity_failure_never_authorizes_helper_writes(
    tmp_path: Path,
) -> None:
    body, headers, identifier = payload({"a.png": png()})
    metadata = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--test", 1)[0])
    catalog = Catalog(tmp_path / "managed", ImportLimits(storage_bytes=1))
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    channel = Mock(spec=Channel)
    channel.poll.return_value = True
    channel.recv_bytes.return_value = json.dumps(
        {"kind": "metadata", "value": metadata}
    ).encode()
    session.connection = channel
    try:
        with pytest.raises(DatasetImportError, match="dataset_capacity"):
            session.exchange(b"data:body", "received")
        channel.send_bytes.assert_called_once_with(b"data:body")
        assert session.metadata is None
        assert session.progress.total_files is None
    finally:
        catalog.finish(identifier, "test_finished")
        session.close()
        catalog.close()


def test_validation_heartbeats_do_not_extend_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    session = Session(catalog, "heartbeat", "0" * 64, "test")
    channel = Mock(spec=Channel)
    elapsed = 0.0

    def poll(_seconds: float) -> bool:
        nonlocal elapsed
        elapsed += 21
        return True

    monkeypatch.setattr(service, "monotonic", lambda: elapsed)
    channel.poll.side_effect = poll
    channel.recv_bytes.return_value = b'{"kind":"validating","index":0}'
    session.connection = channel
    try:
        with pytest.raises(DatasetImportError, match="validation_timeout"):
            session.exchange(b"finish", "complete")
        assert channel.recv_bytes.call_count == 3
        assert session.progress.validated_files is None
    finally:
        catalog.finish(session.identifier, "test_finished")
        session.close()
        catalog.close()


def test_changed_bytes_fail_before_acknowledging_image_validation(
    tmp_path: Path,
) -> None:
    body, headers, _ = payload({"a.png": png()})
    channel = Mock(spec=Channel, wraps=Acknowledgements())
    upload = Upload(
        channel, tmp_path, headers["X-Tachyron-Metadata-SHA256"], "test", ImportLimits()
    )
    try:
        upload.parser.write(body)
        (tmp_path / "a.png").write_bytes(b"x" * len(png()))
        with pytest.raises(DatasetImportError, match="input_changed") as failure:
            upload.finish()
        assert failure.value.index == 0
        messages = [
            json.loads(call.args[0]) for call in channel.send_bytes.call_args_list
        ]
        assert messages[-1] == {"kind": "validating", "index": 0}
        assert not any(message["kind"] == "validated" for message in messages)
    finally:
        upload.close()
