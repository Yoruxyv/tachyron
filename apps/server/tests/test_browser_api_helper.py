# Copyright (c) 2026 Hans Valerie
"""Characterize the frontend browser helper's fixtures and ownership boundaries."""

import importlib
import io
import time
from functools import partial
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import Mock

import pytest
import uvicorn
from fastapi import FastAPI
from PIL import Image

from tachyron.api import create_app
from tachyron.jobs.coordinator import Settings
from tests.job_helpers import local_client

helper = importlib.import_module("apps.web.scripts.serve-test-api")


class BrowserHelper(Protocol):
    def serve(
        self, port: int, *, with_test_dataset: bool, stdio_shutdown: bool
    ) -> None: ...


# The CLI filename contains hyphens; this protocol types its dynamic import boundary.
serve = cast("BrowserHelper", helper).serve


@pytest.fixture
def configurations(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    captured: list[Settings] = []

    def capture(settings: Settings) -> FastAPI:
        captured.append(settings)
        return create_app(settings)

    monkeypatch.setattr(helper, "create_app", capture)
    return captured


@pytest.mark.parametrize("with_dataset", [False, True])
def test_workspace_lives_until_api_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    configurations: list[Settings],
    with_dataset: bool,
) -> None:
    def server(config: uvicorn.Config) -> Mock:
        assert config.host == "127.0.0.1"
        assert config.port == 8123
        app = config.app
        assert isinstance(app, FastAPI)

        def run() -> None:
            settings = configurations[0]
            assert settings.data_dir.name == "history"
            assert settings.data_dir.parent.is_dir()
            assert list(settings.datasets) == (["browser-test"] if with_dataset else [])
            if with_dataset:
                dataset = settings.datasets["browser-test"]
                assert sorted(path.name for path in dataset.iterdir()) == [
                    f"{index}.png" for index in range(8)
                ]
                for index in range(8):
                    with Image.open(dataset / f"{index}.png") as image:
                        assert image.mode == "RGB"
                        assert image.size == (16, 12)
                        assert image.getpixel((0, 0)) == (index * 25, 60, 90)
            with local_client(app) as client:
                assert client.get("/health").json() == {"status": "ok"}
                assert client.get("/api/benchmarks").json() == []
                assert settings.data_dir.is_dir()
            assert settings.data_dir.parent.is_dir()

        return Mock(run=run)

    monkeypatch.setattr(uvicorn, "Server", server)
    serve(8123, with_test_dataset=with_dataset, stdio_shutdown=False)
    assert not configurations[0].data_dir.parent.exists()


@pytest.mark.parametrize("failure_at", ["startup", "run"])
def test_workspace_is_removed_after_initialization_or_server_failure(
    monkeypatch: pytest.MonkeyPatch,
    configurations: list[Settings],
    failure_at: str,
) -> None:
    failure = RuntimeError("injected server failure")
    if failure_at == "startup":
        monkeypatch.setattr(uvicorn, "Server", Mock(side_effect=failure))
    else:
        monkeypatch.setattr(
            uvicorn, "Server", Mock(return_value=Mock(run=Mock(side_effect=failure)))
        )
    with pytest.raises(RuntimeError, match="injected server failure"):
        serve(8123, with_test_dataset=True, stdio_shutdown=False)
    assert not configurations[0].data_dir.parent.exists()


def test_partial_dataset_is_removed_after_image_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configurations: list[Settings],
) -> None:
    temporary_directory = helper.tempfile.TemporaryDirectory
    monkeypatch.setattr(
        helper.tempfile,
        "TemporaryDirectory",
        partial(temporary_directory, dir=tmp_path),
    )
    monkeypatch.setattr(
        Image.Image, "save", Mock(side_effect=OSError("image write failed"))
    )
    with pytest.raises(OSError, match="image write failed"):
        serve(8123, with_test_dataset=True, stdio_shutdown=False)
    assert configurations == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("owner_input", ["", "xy", None])
def test_owner_stdin_requests_cooperative_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    configurations: list[Settings],
    owner_input: str | None,
) -> None:
    stdin = None if owner_input is None else io.StringIO(owner_input)
    server = Mock(should_exit=False)

    def run() -> None:
        deadline = time.monotonic() + 2
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server.should_exit
        assert configurations[0].data_dir.parent.is_dir()

    server.run = run
    monkeypatch.setattr(uvicorn, "Server", Mock(return_value=server))
    monkeypatch.setattr(helper.sys, "stdin", stdin)
    serve(8123, with_test_dataset=False, stdio_shutdown=True)
    if stdin is not None:
        assert stdin.tell() == min(1, len(owner_input or ""))
    assert not configurations[0].data_dir.parent.exists()
