# Copyright (c) 2026 Hans Valerie
"""Local HTTP boundaries, storage capacity and owned-path cleanup regressions."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tachyron.api import create_app
from tachyron.imports.models import DatasetImportError, ImportLimits, portable_parts
from tachyron.imports.service import Session
from tachyron.imports.storage import Catalog
from tachyron.jobs.coordinator import Coordinator
from tachyron.processing import ProcessingError
from tests.test_dataset_imports import payload, png, settings


def test_import_identity_cannot_shadow_an_environment_dataset(tmp_path: Path) -> None:
    body, headers, identifier = payload({"imported.png": png()})
    dataset_id = "import-" + identifier.replace("-", "")
    source = tmp_path / "environment"
    source.mkdir()
    (source / "original.png").write_bytes(png())
    configuration = replace(settings(tmp_path), datasets={dataset_id: source})
    with TestClient(create_app(configuration), base_url="http://127.0.0.1") as client:
        before = client.get("/api/datasets").json()
        response = client.post("/api/dataset-imports", content=body, headers=headers)
        assert response.status_code == 409
        assert response.json() == {"detail": "dataset_id_conflict"}
        assert client.get("/api/datasets").json() == before
        assert client.get(f"/api/dataset-imports/{identifier}").status_code == 404


@pytest.mark.parametrize(
    ("history", "managed", "source", "message"),
    [
        ("history", "history", "source", "Managed datasets"),
        ("history", "history/managed", "source", "Managed datasets"),
        ("managed/history", "managed", "source", "Managed datasets"),
        ("history", "source/managed", "source", "Managed datasets"),
        ("history", "managed", "managed/source", "Managed datasets"),
        ("source/history", "managed", "source", "History and datasets"),
        ("history", "managed", "history/source", "History and datasets"),
        ("shared", "shared/managed", "shared/source", "Managed datasets"),
    ],
)
def test_storage_overlap_fails_before_creating_any_store(
    tmp_path: Path, history: str, managed: str, source: str, message: str
) -> None:
    configuration = replace(
        settings(tmp_path),
        data_dir=tmp_path / history,
        dataset_dir=tmp_path / managed,
        datasets={"environment": tmp_path / source},
    )
    with pytest.raises(ValueError, match=message):
        Coordinator(configuration).start()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["NUL .png", "con .jpg", "COM1 .jpeg", "nested/LPT9 .png"]
)
def test_space_padded_windows_device_names_are_rejected(name: str) -> None:
    with pytest.raises(DatasetImportError, match="invalid_path"):
        portable_parts(name)


@pytest.mark.parametrize(
    "host",
    [
        "evil.invalid",
        "[broken",
        "evil@127.0.0.1",
        "127.0.0.1/ignored",
        "127.0.0.1?ignored",
        "127.0.0.1:invalid",
        "127.0.0.1:0",
        "127.0.0.1:65536",
    ],
)
def test_invalid_host_cannot_bypass_mutation_protection(
    tmp_path: Path, host: str
) -> None:
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/jobs", json={}, headers={"Host": host})
        assert response.status_code == 403
        assert response.json() == {"detail": "invalid_host"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Tachyron-Request": "1", "Origin": "null"},
        {"X-Tachyron-Request": "1", "Origin": "http://127.0.0.1:5173.evil.invalid"},
        {"X-Tachyron-Request": "1", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_browser_mutations_reject_unsafe_requests(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post(
            f"/api/dataset-imports/{uuid4()}/cancel", headers=headers
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "request_forbidden"}
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:5173"])
def test_native_and_approved_browser_clients_remain_compatible(
    tmp_path: Path, origin: str | None
) -> None:
    headers = {"X-Tachyron-Request": "1"}
    if origin is not None:
        headers["Origin"] = origin
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        response = client.post(
            f"/api/dataset-imports/{uuid4()}/cancel", headers=headers
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "import_not_found"}


@pytest.mark.parametrize("quota", ["registrations", "storage_bytes"])
def test_capacity_rejections_preserve_existing_datasets_and_history(
    tmp_path: Path, quota: str
) -> None:
    limits = {quota: 1 if quota == "registrations" else len(png())}
    body, headers, identifier = payload({"a.png": png()})
    with TestClient(
        create_app(settings(tmp_path, **limits)), base_url="http://127.0.0.1"
    ) as client:
        assert (
            client.post(
                "/api/dataset-imports", content=body, headers=headers
            ).status_code
            == 201
        )
        saved = client.get("/api/datasets").json()
        other, other_headers, other_id = payload({"a.png": png()})
        response = client.post(
            "/api/dataset-imports", content=other, headers=other_headers
        )
        assert response.status_code == 507
        assert response.json()["detail"] == "dataset_capacity"
        assert client.get("/api/datasets").json() == saved
        assert (
            client.get(f"/api/dataset-imports/{identifier}").json()["state"] == "READY"
        )
        assert (
            client.get(f"/api/dataset-imports/{other_id}").json()["state"] == "FAILED"
        )
        assert client.get("/api/benchmarks").json() == []


def test_headroom_allows_ready_at_page_cap_and_later_full_catalog_is_readable(
    tmp_path: Path,
) -> None:
    body, headers, identifier = payload({"a.png": png()})
    root = tmp_path / "managed"
    catalog = Catalog(root, ImportLimits())
    session = Session(
        catalog, identifier, headers["X-Tachyron-Metadata-SHA256"], "test"
    )
    try:
        session.start()
        session.feed(body)
        document = session.validate()
        pages = catalog.connection.execute("PRAGMA page_count").fetchone()[0]
        catalog.connection.execute(f"PRAGMA max_page_count={pages}")
        receipt = session.publish(document)
        assert receipt.state == "READY"
        assert catalog.connection.execute("PRAGMA page_count").fetchone()[0] <= pages
    finally:
        session.close()
        catalog.close()
    reopened = Catalog(root, replace(ImportLimits(), catalog_bytes=1024 * 1024))
    try:
        with pytest.raises(DatasetImportError, match="catalog_capacity"):
            reopened.create(str(uuid4()), "0" * 64)
        assert reopened.get(identifier) == receipt
        assert reopened.datasets() == [receipt.dataset]
    finally:
        reopened.close()


def test_cleanup_never_deletes_an_unknown_or_active_namespace(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "managed", ImportLimits())
    identifier = str(uuid4())
    catalog.create(identifier, "0" * 64)
    directory = catalog.objects / identifier
    directory.mkdir()
    (directory / ".owner").write_text("unknown", encoding="ascii")
    (directory / "precious").write_text("retain", encoding="ascii")
    try:
        with pytest.raises(DatasetImportError, match="ownership_unconfirmed"):
            catalog.cleanup(identifier)
        catalog.finish(identifier, "test_failure")
        with pytest.raises(DatasetImportError, match="ownership_unconfirmed"):
            catalog.cleanup(identifier)
        assert (directory / "precious").read_text(encoding="ascii") == "retain"
    finally:
        catalog.close()


def test_symlinked_managed_root_is_rejected_before_resolution(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error.errno}")
    manager = Coordinator(replace(settings(tmp_path), dataset_dir=link / "managed"))
    with pytest.raises(ProcessingError, match="links"):
        manager.start()
    assert list(actual.iterdir()) == []


def test_corrupt_managed_manifest_never_refreshes_workload_identity(
    tmp_path: Path,
) -> None:
    body, headers, _ = payload({"a.png": png()})
    with TestClient(
        create_app(settings(tmp_path)), base_url="http://127.0.0.1"
    ) as client:
        result = client.post(
            "/api/dataset-imports", content=body, headers=headers
        ).json()
        manager: Coordinator = client.app_state["coordinator"]
        catalog = manager.catalog
        row = catalog.connection.execute("SELECT manifest FROM datasets").fetchone()[0]
        malformed = json.loads(row)
        malformed["entries"][0]["size_bytes"] = True
        # Simulate on-disk corruption bypassing the application's immutable-update trigger.
        catalog.connection.execute("DROP TRIGGER immutable_dataset")
        catalog.connection.execute(
            "UPDATE datasets SET manifest=?", (json.dumps(malformed),)
        )
        catalog.connection.commit()
        response = client.post(
            "/api/jobs",
            json={"dataset_id": result["dataset"]["dataset_id"]},
            headers={"X-Tachyron-Request": "1"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "catalog_unavailable"}
