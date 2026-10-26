# Copyright (c) 2026 Hans Valerie
"""One picklable Pillow operation with exclusive, complete output publication."""

import hashlib
import io
import logging
import os
import tempfile
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from tachyron.processing.dataset import dataset_root, read_input, reject_link
from tachyron.processing.models import (
    DEFAULT_LIMITS,
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
)

logger = logging.getLogger(__name__)


def output_directory(path: Path, root: Path) -> Path:
    """Require a real output directory outside the dataset."""
    reject_link(path.lstat())
    canonical = path.resolve(strict=True)
    if not canonical.is_dir() or canonical.is_relative_to(root):
        raise ProcessingError("invalid_path", "Outputs must be outside the dataset")
    return canonical


def publish_bytes(directory: Path, name: str, data: bytes) -> None:
    """Publish complete bytes atomically without replacing an existing pathname.

    A temporary file and final hard link share a directory/filesystem. Failure
    removes only this call's temporary file; existing destination files survive.
    Cleanup denial emits a warning without changing publication accounting or
    hiding the original failure.

    Args:
        directory: Validated, owned run directory on a hard-link capable filesystem.
        name: Internally generated basename.
        data: Complete encoded content.

    Raises:
        OSError: Write or exclusive publication failed.
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".tachyron-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
        os.link(temporary, directory / name)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                logger.warning(
                    "Unable to remove owned run temporary file %s", temporary.name
                )


def _png_bit_depth(data: bytes) -> int:
    """Require a unique leading IHDR before trusting the PNG's encoded depth."""
    if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
        raise ProcessingError("invalid_image", "PNG must begin with one valid IHDR")
    offset = 33
    # Pillow verifies CRCs; this bounded walk rejects header ambiguity it permits.
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data) or kind == b"IHDR":
            raise ProcessingError(
                "invalid_image", "PNG chunk boundary or header is invalid"
            )
        if kind == b"IEND":
            return data[24]
        offset = end
    raise ProcessingError("invalid_image", "PNG chunk stream is incomplete")


def _decode_rgb(
    data: bytes, limits: ResourceLimits
) -> tuple[Image.Image, tuple[int, int]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as source:
                width, height = source.size
                if (
                    max(width, height) > limits.max_dimension
                    or width * height > limits.max_pixels
                ):
                    raise ProcessingError(
                        "resource_limit", "Image dimension or pixel limit exceeded"
                    )
                high_bit_png = source.format == "PNG" and _png_bit_depth(data) > 8
                if (
                    high_bit_png
                    or getattr(source, "n_frames", 1) != 1
                    or source.mode
                    not in {
                        "1",
                        "L",
                        "LA",
                        "P",
                        "RGB",
                        "RGBA",
                        "CMYK",
                    }
                ):
                    raise ProcessingError(
                        "unsupported_image", "Image frame count or mode is unsupported"
                    )
                source.verify()
            with Image.open(io.BytesIO(data), formats=("PNG", "JPEG")) as source:
                source.load()
                with (
                    ImageOps.exif_transpose(source) as oriented,
                    oriented.convert("RGBA") as rgba,
                ):
                    rgb = Image.new("RGB", oriented.size, "white")
                    try:
                        with rgba.getchannel("A") as alpha:
                            rgb.paste(rgba, mask=alpha)
                    except (OSError, ValueError):
                        rgb.close()
                        raise
                    return rgb, (width, height)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ProcessingError(
            "resource_limit", "Pillow decompression limit exceeded"
        ) from error
    except ProcessingError:
        raise
    except (OSError, SyntaxError, ValueError, Warning) as error:
        raise ProcessingError("invalid_image", "Image cannot be decoded") from error


def process_image(
    root: Path,
    entry: ManifestEntry,
    output_dir: Path,
    config: TransformConfig,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ImageOutcome:
    """Process one manifest entry without HTTP state or side effects at import.

    Args:
        root: Allowed dataset root.
        entry: Relative path and expected compressed content identity.
        output_dir: Existing per-run directory outside the dataset.
        config: Validated exact output size and lossless encoding.
        limits: Bounded read and decode policy.

    Returns:
        One successful PNG mapping, or an explicit sanitized failure code.
        Expected per-file failures never stop other batch entries.
    """
    try:
        limits.check_output(config)
        canonical = dataset_root(root)
        directory = output_directory(output_dir, canonical)
        data = read_input(canonical, entry.relative_path, limits)
        if (
            len(data) != entry.size_bytes
            or hashlib.sha256(data).hexdigest() != entry.sha256
        ):
            raise ProcessingError(
                "input_changed", "Input no longer matches the manifest"
            )
        rgb, original_size = _decode_rgb(data, limits)
        with (
            rgb,
            # Pillow 13 leaves its optional NumPy alias undefined; this uses a typed tuple.
            rgb.resize(  # pyright: ignore[reportUnknownMemberType]
                (config.width, config.height), Image.Resampling.LANCZOS
            ) as resized,
            resized.convert("L") as gray,
        ):
            gray.info.clear()
            buffer = io.BytesIO()
            gray.save(
                buffer, format=config.output_format, optimize=False, compress_level=6
            )
        name = hashlib.sha256(entry.relative_path.encode("utf-8")).hexdigest() + ".png"
        publish_bytes(directory, name, buffer.getvalue())
        return ImageOutcome(entry.relative_path, name, input_size=original_size)
    except ProcessingError as error:
        return ImageOutcome(entry.relative_path, error_code=error.code)
    except FileExistsError:
        return ImageOutcome(entry.relative_path, error_code="output_collision")
    except OSError:
        return ImageOutcome(entry.relative_path, error_code="io_error")
