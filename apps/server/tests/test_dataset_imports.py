# Copyright (c) 2026 Hans Valerie
"""Real bounded imports, permanent receipts, catalog publication and core agreement."""

import hashlib
import io
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tachyron.api import create_app
from tachyron.imports.models import DatasetImportError, ImportLimits, Metadata
from tachyron.imports.storage import Catalog
from tachyron.jobs.coordinator import Settings
from tachyron.processing import build_manifest
from tests.job_helpers import terminal


def png() -> bytes:
    """Generate test-only deterministic lossless bytes."""
    with Image.new("RGB", (4, 3), "red") as source, io.BytesIO() as stream:
        source.save(stream, format="PNG")
        return stream.getvalue()


def payload(
    files: dict[str, bytes], key: str | None = None
) -> tuple[bytes, dict[str, str], str]:
    """Encode the exact v1 protocol without relying on multipart client defaults."""
    identifier = key or str(uuid4())
    metadata = json.dumps(
        {
            "schema_version": 1,
            "display_name": "Test images",
            "selection": {"mode": "whole-folder", "excluded_count": 0},
            "files": [
                {
                    "part": f"file-{i}",
                    "relative_path": name,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for i, (name, data) in enumerate(files.items())
            ],
        },
        separators=(",", ":"),
    ).encode()
    body = (
        b'--test\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
        + metadata
        + b"\r\n"
    )
    for i, data in enumerate(files.values()):
        body += (
            f'--test\r\nContent-Disposition: form-data; name="file-{i}"; filename="ignored"\r\n\r\n'.encode()
            + data
            + b"\r\n"
        )
    body += b"--test--\r\n"
    headers = {
        "X-Tachyron-Request": "1",
        "Idempotency-Key": identifier,
        "X-Tachyron-Metadata-SHA256": hashlib.sha256(metadata).hexdigest(),
        "Content-Type": "multipart/form-data; boundary=test",
    }
    return body, headers, identifier


def settings(root: Path, **limits: int) -> Settings:
    return Settings(
        data_dir=root / "history",
        dataset_dir=root / "managed",
        import_limits=replace(ImportLimits(), **limits),
    )


@pytest.mark.spawn
def test_import_restart_real_benchmark_and_original_manifest(tmp_path: Path) -> None:
    configuration = settings(tmp_path)
    files = {"nested/a.png": png(), "b.no-extension": png()}
    body, headers, identifier = payload(files)
    with TestClient(
        create_app(configuration),
        base_url="http://127.0.0.1",
        headers={"X-Tachyron-Request": "1"},
    ) as client:
        result = client.post("/api/dataset-imports", content=body, headers=headers)
        assert result.status_code == 201, result.text
        receipt = result.json()
        assert receipt["state"] == "READY"
        dataset = receipt["dataset"]
        original = tmp_path / "source"
        for name, data in files.items():
            path = original / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        assert dataset["manifest_sha256"] == build_manifest(original).sha256
        assert client.get("/api/datasets").json() == [dataset]
        replay = client.post(
            "/api/dataset-imports", content=b"ignored", headers=headers
        )
        assert replay.status_code == 200
        assert replay.json() == receipt
        assert (
            client.post(f"/api/dataset-imports/{identifier}/cancel").json() == receipt
        )
    with TestClient(
        create_app(configuration),
        base_url="http://127.0.0.1",
        headers={"X-Tachyron-Request": "1"},
    ) as client:
        assert client.get(f"/api/dataset-imports/{identifier}").json() == receipt
        accepted = client.post(
            "/api/jobs",
            json={
                "dataset_id": dataset["dataset_id"],
                "tiny": True,
                "repetitions": 1,
                "width": 2,
                "height": 2,
            },
        )
        assert accepted.status_code == 202, accepted.text
        job = terminal(client, accepted.json()["id"])
        assert job.state == "COMPLETED"
        evidence = client.get(f"/api/jobs/{job.id}/metrics").json()
        assert {row["workers"] for row in evidence["summaries"]} == {1, 2, 4, 8}
        assert len(client.get("/api/benchmarks").json()) == 1


@pytest.mark.parametrize("kind", ["corrupt", "truncated", "digest", "extra", "missing"])
def test_failed_receipts_are_immutable_and_not_registered(
    tmp_path: Path, kind: str
) -> None:
    body, headers, identifier = payload(
        {"a.png": b"broken" if kind == "corrupt" else png()}
    )
    if kind == "truncated":
        body = body[:-7]
    elif kind == "digest":
        body = body.replace(png(), b"x" * len(png()))
    elif kind == "extra":
        body = body.replace(
            b"--test--",
            b'--test\r\nContent-Disposition: form-data; name="file-1"\r\n\r\nx\r\n--test--',
        )
    elif kind == "missing":
        body = (
            body[
                : body.index(b'--test\r\nContent-Disposition: form-data; name="file-0"')
            ]
            + b"--test--\r\n"
        )
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code >= 400, response.text
        receipt = client.get(f"/api/dataset-imports/{identifier}").json()
        assert receipt["state"] == "FAILED"
        assert client.get("/api/datasets").json() == []
        replay = client.post("/api/dataset-imports", content=body, headers=headers)
        assert replay.json() == receipt


@pytest.mark.parametrize(
    "name",
    ["../a.png", "CON.png", "trailing. /a.png", "a\\b.png", "/a.png", "C:/a.png"],
)
def test_unsafe_names_fail_before_file_writes(tmp_path: Path, name: str) -> None:
    body, headers, identifier = payload({name: png()})
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code == 422
        assert (
            client.get(f"/api/dataset-imports/{identifier}").json()["state"] == "FAILED"
        )
    assert not (tmp_path / "a.png").exists()


def test_raw_body_limit_and_security_precede_storage(tmp_path: Path) -> None:
    body, headers, _ = payload({"a.png": png()})
    with TestClient(
        create_app(settings(tmp_path, max_body_bytes=10)), base_url="http://127.0.0.1"
    ) as client:
        assert (
            client.post(
                "/api/dataset-imports", content=body, headers=headers
            ).status_code
            == 413
        )
        assert client.post("/api/jobs", json={}).status_code == 403
        forbidden = {**headers, "Origin": "http://evil.invalid"}
        assert (
            client.post(
                "/api/dataset-imports", content=body, headers=forbidden
            ).status_code
            == 403
        )
        assert (
            client.get("/api/datasets", headers={"Host": "evil.invalid"}).status_code
            == 403
        )
        assert client.get("/api/datasets").json() == []


def test_catalog_capacity_retains_readability_and_terminal_headroom(
    tmp_path: Path,
) -> None:
    catalog = Catalog(
        tmp_path / "managed", replace(ImportLimits(), catalog_bytes=4 * 1024 * 1024)
    )
    try:
        capacity_error: DatasetImportError | None = None
        first = str(uuid4())
        catalog.create(first, "0" * 64)
        catalog.finish(first, "test_failure")
        receipt = catalog.get(first)
        for _ in range(200):
            identifier = str(uuid4())
            try:
                catalog.create(identifier, "0" * 64)
            except DatasetImportError as error:
                capacity_error = error
                break
            catalog.finish(identifier, "test_failure")
        else:
            pytest.fail("Catalog did not reach its admission bound")
        assert capacity_error is not None
        assert capacity_error.code == "catalog_capacity"
        assert catalog.get(first) == receipt
        assert catalog.datasets() == []
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            catalog.connection.execute(
                "UPDATE operations SET state='READY' WHERE id=?", (first,)
            )
        catalog.connection.rollback()
    finally:
        catalog.close()


def test_envelope_covers_production_payload_and_every_header() -> None:
    bound = 512 * 1024 * 1024 + 512 * 1024 + 1001 * (8192 + 76) + 76 + 65536
    assert 516 * 1024 * 1024 < bound <= ImportLimits().max_body_bytes


@pytest.mark.parametrize(
    "names",
    [
        ["A/a.png", "a/b.png"],
        ["a.png", "A.png"],
        ["a", "a/b.png"],
        ["é.png", "e\u0301.png"],
    ],
)
def test_ambiguous_trees_are_rejected(names: list[str]) -> None:
    raw: dict[str, object] = {
        "schema_version": 1,
        "display_name": "tree",
        "selection": {"mode": "whole-folder"},
        "files": [
            {
                "part": f"file-{i}",
                "relative_path": name,
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
            for i, name in enumerate(names)
        ],
    }
    metadata = Metadata.model_validate_json(json.dumps(raw))
    with pytest.raises(DatasetImportError, match="path_conflict"):
        metadata.check(ImportLimits())
