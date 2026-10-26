# Copyright (c) 2026 Hans Valerie
"""Exercise real image content and the processor's filesystem trust boundaries."""

import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import zlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image, ImageOps, PngImagePlugin

from tachyron.processing import (
    ProcessingError,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    process_image,
    run_sequential,
)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Build tiny PNG chunks with real CRCs for malformed-header regressions."""
    return (
        len(data).to_bytes(4, "big")
        + kind
        + data
        + zlib.crc32(kind + data).to_bytes(4, "big")
    )


def make_image(
    path: Path, *, color: str = "red", size: tuple[int, int] = (6, 4)
) -> None:
    """Generate a miniature real image without checked-in binary fixtures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, color) as image:
        image.save(path)


def test_sorted_manifest_is_relocatable_and_tracks_content(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "z.png")
    make_image(dataset / "nested/a.jpg")
    first = build_manifest(dataset)
    assert [entry.relative_path for entry in first.entries] == ["nested/a.jpg", "z.png"]
    assert first.total_bytes == sum(p.stat().st_size for p in dataset.rglob("*.*"))
    assert (
        first.entries[1].sha256
        == hashlib.sha256((dataset / "z.png").read_bytes()).hexdigest()
    )
    assert build_manifest(dataset) == first
    relocated = tmp_path / "relocated"
    make_image(relocated / "z.png")
    make_image(relocated / "nested/a.jpg")
    assert build_manifest(relocated).sha256 == first.sha256
    make_image(dataset / "z.png", color="blue")
    assert build_manifest(dataset).sha256 != first.sha256


def test_real_batch_is_deterministic_and_preserves_inputs(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    for name in ("a.png", "a.jpg", "nested/a.png", "upper.PNG"):
        make_image(dataset / name)
    manifest = build_manifest(dataset)
    config = TransformConfig(width=3, height=2)
    first = run_sequential(manifest, tmp_path / "first", config)
    second = run_sequential(manifest, tmp_path / "second", config)
    assert first.succeeded == 4
    assert first.failed == 0
    names = [outcome.output_name for outcome in first.outcomes]
    assert len(set(names)) == 4
    for outcome in first.outcomes:
        assert outcome.output_name is not None
        output = first.output_dir / outcome.output_name
        assert (
            output.read_bytes()
            == (second.output_dir / outcome.output_name).read_bytes()
        )
        with Image.open(output) as image:
            assert image.format == "PNG"
            assert image.mode == "L"
            assert image.size == (3, 2)
            assert image.getpixel((0, 0)) == 76
    assert build_manifest(dataset) == manifest
    report = json.loads((first.output_dir / "run.json").read_text(encoding="utf-8"))
    assert report["succeeded"] == 4
    assert report["failed"] == 0
    assert report["manifest"]["sha256"] == manifest.sha256
    assert report["config"] == {"width": 3, "height": 2, "output_format": "PNG"}
    assert report["pillow_version"]
    assert (first.output_dir / "run.json").read_bytes() == (
        second.output_dir / "run.json"
    ).read_bytes()


def test_resize_uses_rgb_lanczos_before_grayscale(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (4, 3)) as source:
        for index, color in enumerate(
            [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)] * 3
        ):
            source.putpixel((index % 4, index // 4), color)
        source.save(dataset / "pattern.png")
        with (
            # Pillow's optional NumPy alias is undefined; the tested tuple is typed.
            source.resize((3, 5), Image.Resampling.LANCZOS) as resized,  # pyright: ignore[reportUnknownMemberType]
            resized.convert("L") as gray,
        ):
            expected = gray.tobytes()
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(3, 5)
    )
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(result.output_dir / name) as actual:
        assert actual.size == (3, 5)
        assert actual.tobytes() == expected


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_exif_orientation_is_applied_before_resize(
    tmp_path: Path, orientation: int
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (4, 2)) as source:
        for index, color in enumerate([(255, 0, 0)] * 4 + [(0, 0, 255)] * 4):
            source.putpixel((index % 4, index // 4), color)
        exif = Image.Exif()
        exif[274] = orientation
        source.save(dataset / "oriented.jpg", exif=exif)
    with (
        Image.open(dataset / "oriented.jpg") as source,
        ImageOps.exif_transpose(source) as oriented,
    ):
        size = oriented.size
        with oriented.convert("L") as gray:
            expected = gray.tobytes()
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(*size)
    )
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(result.output_dir / name) as actual:
        assert actual.tobytes() == expected
        assert not actual.getexif()


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_alpha_is_composited_on_white_and_metadata_discarded(
    tmp_path: Path, mode: str
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new(mode, (2, 1)) as source:
        if mode == "RGBA":
            source.putpixel((0, 0), (0, 0, 0, 0))
            source.putpixel((1, 0), (0, 0, 0, 128))
        elif mode == "LA":
            source.putpixel((0, 0), (0, 0))
            source.putpixel((1, 0), (0, 128))
        else:
            source.putpalette([0, 0, 0] * 256)
            source.putpixel((0, 0), 0)
            source.putpixel((1, 0), 1)
            source.info["transparency"] = bytes([0, 128])
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private", "discard this")
        source.save(dataset / "alpha.png", pnginfo=metadata, icc_profile=b"ignored")
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(2, 1)
    )
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(result.output_dir / name) as actual:
        assert actual.tobytes() == bytes([255, 127])
        assert not actual.info


@pytest.mark.parametrize("mode", ["1", "L", "CMYK"])
def test_other_supported_modes(tmp_path: Path, mode: str) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    name = "mode.jpg" if mode == "CMYK" else "mode.png"
    with Image.new(mode, (2, 2)) as source:
        source.save(dataset / name)
        with source.convert("RGB") as rgb, rgb.convert("L") as gray:
            expected = gray.tobytes()
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(2, 2)
    )
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(result.output_dir / name) as actual:
        assert actual.tobytes() == expected


def test_partial_failures_are_explicit_and_do_not_stop_batch(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    (dataset / "b.png").write_bytes(b"not an image")
    with Image.new("RGB", (2, 2)) as image:
        image.save(dataset / "c.png", format="GIF")
    make_image(dataset / "d.jpg", color="blue")
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig()
    )
    assert (result.succeeded, result.failed) == (2, 2)
    assert [item.error_code for item in result.outcomes] == [
        None,
        "invalid_image",
        "invalid_image",
        None,
    ]
    assert len(list(result.output_dir.glob("*.png"))) == 2
    assert not list(result.output_dir.glob(".tachyron-*"))


def test_animated_png_is_rejected(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with (
        Image.new("RGB", (2, 2), "red") as first,
        Image.new("RGB", (2, 2), "blue") as second,
    ):
        first.save(dataset / "animated.png", save_all=True, append_images=[second])
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig()
    )
    assert result.outcomes[0].error_code == "unsupported_image"
    assert result.failed == 1


def test_changed_or_missing_manifest_inputs_fail(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    make_image(dataset / "b.png")
    manifest = build_manifest(dataset)
    make_image(dataset / "a.png", color="blue")
    (dataset / "b.png").unlink()
    result = run_sequential(manifest, tmp_path / "run", TransformConfig())
    assert result.succeeded == 0
    assert [item.error_code for item in result.outcomes] == [
        "input_changed",
        "io_error",
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.png",
        "/outside.png",
        "a/../../outside",
        "a\\b.png",
        "C:/outside.png",
        "bad\ud800.png",
    ],
)
def test_forged_manifest_paths_cannot_escape(tmp_path: Path, relative: str) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    entry = replace(manifest.entries[0], relative_path=relative)
    output = tmp_path / "run"
    output.mkdir()
    result = process_image(manifest.root, entry, output, TransformConfig())
    assert result.error_code == "invalid_path"
    assert not list(output.iterdir())


@pytest.mark.parametrize("target", ["existing", "input", "input/nested"])
def test_unsafe_run_directories_are_not_modified(tmp_path: Path, target: str) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    (tmp_path / "existing").mkdir()
    sentinel = tmp_path / "existing/keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    manifest = build_manifest(dataset)
    with pytest.raises(ProcessingError):
        run_sequential(manifest, tmp_path / target, TransformConfig())
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (dataset / "nested").exists()
    assert build_manifest(dataset) == manifest


def test_output_collision_preserves_existing_file(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    first = process_image(manifest.root, manifest.entries[0], output, TransformConfig())
    assert first.output_name is not None
    path = output / first.output_name
    original = path.read_bytes()
    second = process_image(
        manifest.root, manifest.entries[0], output, TransformConfig(2, 2)
    )
    assert second.error_code == "output_collision"
    assert path.read_bytes() == original
    assert sorted(p.name for p in output.iterdir()) == [first.output_name]


@pytest.mark.parametrize("failure", ["encode", "publish"])
def test_write_failures_leave_no_partial_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    if failure == "encode":
        monkeypatch.setattr(
            Image.Image, "save", Mock(side_effect=OSError("private internal path"))
        )
    else:
        monkeypatch.setattr(
            os, "link", Mock(side_effect=OSError("private internal path"))
        )
    result = process_image(
        manifest.root, manifest.entries[0], output, TransformConfig()
    )
    assert result.error_code == "io_error"
    assert not list(output.iterdir())
    assert "private" not in str(result)


@pytest.mark.parametrize(
    "limits",
    [
        ResourceLimits(max_files=1),
        ResourceLimits(max_file_bytes=1),
        ResourceLimits(max_total_bytes=1),
        ResourceLimits(max_entries=1),
        ResourceLimits(max_depth=1),
    ],
)
def test_dataset_limits_are_enforced_before_outputs(
    tmp_path: Path, limits: ResourceLimits
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    make_image(dataset / "nested/deep/b.png")
    with pytest.raises(ProcessingError, match="limit"):
        build_manifest(dataset, limits)


@pytest.mark.parametrize(
    "limits", [ResourceLimits(max_pixels=4), ResourceLimits(max_dimension=2)]
)
def test_header_limits_are_enforced_before_decode(
    tmp_path: Path, limits: ResourceLimits, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png", size=(3, 3))
    manifest = build_manifest(dataset, limits)
    load = Mock(side_effect=AssertionError("oversize image must not be decoded"))
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", load)
    result = run_sequential(manifest, tmp_path / "run", TransformConfig())
    assert result.outcomes[0].error_code == "resource_limit"
    load.assert_not_called()


@pytest.mark.parametrize("bomb_limit", [3, 5])
def test_pillow_bomb_warnings_and_errors_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bomb_limit: int
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png", size=(3, 3))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", bomb_limit)
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig()
    )
    assert result.outcomes[0].error_code == "resource_limit"
    assert bomb_limit == Image.MAX_IMAGE_PIXELS


def test_symlink_escape_is_rejected_at_discovery_and_execution(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    make_image(tmp_path / "outside.png")
    manifest = build_manifest(dataset)
    link = dataset / "link.png"
    try:
        link.symlink_to(tmp_path / "outside.png")
    except OSError as error:
        if sys.platform != "win32":
            raise
        pytest.skip(f"OS does not permit symlink creation: {error.errno}")
    with pytest.raises(ProcessingError, match="link"):
        build_manifest(dataset)
    (dataset / "a.png").unlink()
    (dataset / "a.png").symlink_to(tmp_path / "outside.png")
    result = run_sequential(manifest, tmp_path / "run", TransformConfig())
    assert result.outcomes[0].error_code == "invalid_path"
    assert (tmp_path / "outside.png").is_file()


@pytest.mark.parametrize("width", [0, -1, 4097, True])
def test_invalid_transform_dimensions(width: int) -> None:
    with pytest.raises((ValueError, TypeError)):
        TransformConfig(width=width)


def test_empty_dataset_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProcessingError, match="empty"):
        build_manifest(tmp_path)


def test_worker_callable_and_contracts_are_picklable(tmp_path: Path) -> None:
    make_image(tmp_path / "a.png")
    assert pickle.dumps(process_image)
    assert pickle.dumps(build_manifest(tmp_path))
    assert pickle.dumps(TransformConfig())


@pytest.mark.parametrize(
    "kind", ["truncated_png", "bad_crc", "truncated_jpeg", "wide_mode"]
)
def test_malformed_and_unsupported_content_fails_without_outputs(
    tmp_path: Path, kind: str
) -> None:
    dataset = tmp_path / "input"
    path = dataset / ("image.jpg" if kind == "truncated_jpeg" else "image.png")
    make_image(path)
    data = path.read_bytes()
    if kind == "wide_mode":
        with Image.new("I;16", (2, 2)) as image:
            image.save(path)
    elif kind == "truncated_png":
        path.write_bytes(data[:-8])
    elif kind == "truncated_jpeg":
        path.write_bytes(data[:-20])
    else:
        position = data.index(b"IDAT") + 4
        path.write_bytes(
            data[:position] + bytes([data[position] ^ 1]) + data[position + 1 :]
        )
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig()
    )
    assert result.failed == 1
    assert result.outcomes[0].error_code in {"invalid_image", "unsupported_image"}
    assert [p.name for p in result.output_dir.iterdir()] == ["run.json"]


def test_rgb_color_key_transparency_uses_white_background(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (1, 1), "red") as image:
        image.save(dataset / "key.png", transparency=(255, 0, 0))
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(1, 1)
    )
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(result.output_dir / name) as actual:
        assert actual.tobytes() == bytes([255])


def test_report_failure_preserves_completed_images_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    link = os.link

    def fail_report(source: Path, destination: Path) -> None:
        if destination.name == "run.json":
            raise OSError("sensitive internal error")
        link(source, destination)

    monkeypatch.setattr(os, "link", fail_report)
    with pytest.raises(ProcessingError, match="report cannot be written"):
        run_sequential(build_manifest(dataset), tmp_path / "run", TransformConfig())
    files = list((tmp_path / "run").iterdir())
    assert len(files) == 1
    with Image.open(files[0]) as image:
        image.load()
        assert image.format == "PNG"


def test_interrupted_publication_cleans_only_its_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_bytes(b"keep")
    monkeypatch.setattr(os, "link", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        process_image(manifest.root, manifest.entries[0], output, TransformConfig())
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("kind", ["digest", "duplicate", "order", "size"])
def test_inconsistent_manifest_is_rejected_before_run_creation(
    tmp_path: Path, kind: str
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    make_image(dataset / "b.png")
    manifest = build_manifest(dataset)
    if kind == "digest":
        manifest = replace(manifest, sha256="wrong")
    elif kind == "duplicate":
        manifest = replace(manifest, entries=manifest.entries * 2)
    elif kind == "order":
        manifest = replace(manifest, entries=manifest.entries[::-1])
    else:
        manifest = replace(
            manifest, entries=(replace(manifest.entries[0], size_bytes=-1),)
        )
    with pytest.raises(ProcessingError):
        run_sequential(manifest, tmp_path / "run", TransformConfig())
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_resource_limits_are_rejected(value: int) -> None:
    with pytest.raises((ValueError, TypeError)):
        ResourceLimits(max_files=value)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows directory junction semantics"
)
def test_windows_junctions_are_rejected_for_inputs_and_outputs(tmp_path: Path) -> None:
    executable = shutil.which("cmd.exe")
    assert executable is not None
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = dataset / "junction"
    result = subprocess.run(  # noqa: S603 -- Fixed mklink command and generated temporary paths.
        [executable, "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    output_link = tmp_path / "output-link"
    try:
        with pytest.raises(ProcessingError, match="link"):
            build_manifest(dataset)
        junction.rename(output_link)
        manifest = build_manifest(dataset)
        outcome = process_image(
            manifest.root, manifest.entries[0], output_link, TransformConfig()
        )
        assert outcome.error_code == "invalid_path"
        # The run API must also reject an already-existing output, including a junction.
        with pytest.raises(ProcessingError):
            run_sequential(manifest, output_link, TransformConfig())
        assert not list(outside.iterdir())
    finally:
        if junction.exists():
            junction.rmdir()
        if output_link.exists():
            output_link.rmdir()


def test_partial_temporary_write_is_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    with tempfile.NamedTemporaryFile(dir=output, delete=False) as stream:
        original_write = stream.write

        def fail_write(data: bytes) -> int:
            original_write(data[:3])
            raise OSError("disk full")

        monkeypatch.setattr(stream, "write", fail_write)
        monkeypatch.setattr(tempfile, "NamedTemporaryFile", Mock(return_value=stream))
        outcome = process_image(
            manifest.root, manifest.entries[0], output, TransformConfig()
        )
    assert outcome.error_code == "io_error"
    assert not list(output.iterdir())


def test_batch_interrupt_retains_completed_outputs_and_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    make_image(dataset / "b.png")
    manifest = build_manifest(dataset)
    link = os.link
    calls = 0

    def interrupt_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        link(source, destination)

    monkeypatch.setattr(os, "link", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        run_sequential(manifest, tmp_path / "run", TransformConfig())
    files = list((tmp_path / "run").iterdir())
    assert len(files) == 1
    with Image.open(files[0]) as image:
        image.load()
        assert image.format == "PNG"
    assert build_manifest(dataset) == manifest


def test_new_files_do_not_change_a_frozen_workload(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    make_image(dataset / "added.png")
    result = run_sequential(manifest, tmp_path / "run", TransformConfig())
    assert result.succeeded == 1
    assert len(build_manifest(dataset).entries) == 2


def test_output_pixel_budget_is_checked_before_run_creation(tmp_path: Path) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset, ResourceLimits(max_output_pixels=4))
    with pytest.raises(ProcessingError, match="output pixel limit"):
        run_sequential(manifest, tmp_path / "run", TransformConfig(3, 2))
    assert not (tmp_path / "run").exists()


def test_16_bit_rgb_png_is_rejected_before_pillow_silently_reduces_precision(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()

    header = (1).to_bytes(4, "big") * 2 + bytes([16, 2, 0, 0, 0])
    pixels = zlib.compress(bytes([0, 255, 255, 0, 0, 0, 0]))
    path = dataset / "rgb16.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", pixels)
        + png_chunk(b"IEND", b"")
    )
    with Image.open(path) as source:
        source.load()
        assert source.mode == "RGB"
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(1, 1)
    )
    assert result.outcomes[0].error_code == "unsupported_image"
    assert result.failed == 1


def test_descriptor_is_closed_if_stream_initialization_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    original_open = os.open
    descriptors: list[int] = []

    def track_open(path: Path, flags: int) -> int:
        descriptor = original_open(path, flags)
        descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "fdopen", Mock(side_effect=OSError("cannot wrap stream")))
    result = process_image(
        manifest.root, manifest.entries[0], output, TransformConfig()
    )
    assert result.error_code == "io_error"
    assert len(descriptors) == 1
    try:
        os.fstat(descriptors[0])
    except OSError:
        closed = True
    else:
        closed = False
        os.close(descriptors[0])
    assert closed


@pytest.mark.parametrize(
    "kind", ["conflicting_header", "duplicate_header", "leading_ancillary"]
)
def test_png_requires_one_leading_header(tmp_path: Path, kind: str) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    header8 = (1).to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])
    header16 = (1).to_bytes(4, "big") * 2 + bytes([16, 2, 0, 0, 0])
    if kind == "leading_ancillary":
        prefix = png_chunk(b"abCD", bytes(12))
    else:
        prefix = png_chunk(b"IHDR", header8)
    sixteen = kind != "duplicate_header"
    pixels = bytes([0, 255, 255, 0, 0, 0, 0]) if sixteen else bytes([0, 255, 0, 0])
    (dataset / "bad.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + prefix
        + png_chunk(b"IHDR", header16 if sixteen else header8)
        + png_chunk(b"IDAT", zlib.compress(pixels))
        + png_chunk(b"IEND", b"")
    )
    result = run_sequential(
        build_manifest(dataset), tmp_path / "run", TransformConfig(1, 1)
    )
    assert result.outcomes[0].error_code == "invalid_image"
    assert result.succeeded == 0
    assert not list(result.output_dir.glob("*.png"))


@pytest.mark.parametrize("target", ["image", "report"])
def test_cleanup_warning_preserves_published_output_accounting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    target: str,
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    unlink = Path.unlink
    calls = 0

    def deny_one_cleanup(path: Path) -> None:
        nonlocal calls
        if path.parent == output and path.name.startswith(".tachyron-"):
            calls += 1
            if calls == (1 if target == "image" else 2):
                raise PermissionError("private OS error")
        unlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny_one_cleanup)
        result = run_sequential(manifest, output, TransformConfig(2, 2))
    assert (result.succeeded, result.failed) == (1, 0)
    name = result.outcomes[0].output_name
    assert name is not None
    with Image.open(output / name) as image:
        image.load()
        assert image.size == (2, 2)
    report = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert report["outcomes"][0]["output_name"] == name
    temporary = list(output.glob(".tachyron-*"))
    assert len(temporary) == 1
    assert temporary[0].name in caplog.text
    assert "private OS error" not in caplog.text
    assert build_manifest(dataset) == manifest


def test_cleanup_failure_preserves_original_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dataset = tmp_path / "input"
    make_image(dataset / "a.png")
    manifest = build_manifest(dataset)
    output = tmp_path / "run"
    output.mkdir()
    first = process_image(
        manifest.root, manifest.entries[0], output, TransformConfig(2, 2)
    )
    assert first.output_name is not None
    original = (output / first.output_name).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            Path, "unlink", Mock(side_effect=PermissionError("private OS error"))
        )
        second = process_image(
            manifest.root, manifest.entries[0], output, TransformConfig(1, 1)
        )
    assert second.error_code == "output_collision"
    assert (output / first.output_name).read_bytes() == original
    assert "temporary file" in caplog.text
