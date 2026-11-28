from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from vue_rag.adapters.ingestion import ExtractionError, LocalInputExtractor
from vue_rag.application.ingestion_contracts import IngestionRequest
from vue_rag.streamlit_ui.controller import primary_upload_name, stage_uploads


@dataclass(frozen=True)
class Upload:
    name: str
    content: bytes = b"content"

    def getvalue(self) -> bytes:
        return self.content


def _request(input_path: str = "guide.md") -> IngestionRequest:
    return IngestionRequest(
        kind="markdown",
        title="Guide",
        source_type_id="source",
        status_id="status",
        external_id="guide",
        input_path=input_path,
    )


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def test_markdown_extracts_local_images_and_lists_remote_urls(tmp_path: Path) -> None:
    (tmp_path / "image 1.png").write_bytes(_png_bytes())
    (tmp_path / "guide.md").write_text(
        "\n".join(
            (
                "![local](nested/image%201.png)",
                "![duplicate](image%201.png)",
                "![missing](missing.png)",
                "[archive](https://example.com/file.zip)",
                "![remote](https://cdn.example.com/image.png)",
                "[duplicate URL](https://example.com/file.zip)",
            )
        )
    )

    extracted = LocalInputExtractor(tmp_path).extract(_request())

    assert tuple(asset.name for asset in extracted.assets) == ("image 1.png",)
    assert extracted.downloadable_urls == (
        "https://example.com/file.zip",
        "https://cdn.example.com/image.png",
    )


def test_markdown_lists_common_http_url_forms(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text(
        "\n".join(
            (
                '[title](https://example.com/title "Download")',
                "<https://example.com/autolink>",
                "[parentheses](https://example.com/a_(b).zip)",
                "[query punctuation](https://example.com/search?q=!)",
                "[terminal period](https://example.com/a.)",
                "[reference]: https://example.com/reference",
            )
        )
    )

    extracted = LocalInputExtractor(tmp_path).extract(_request())

    assert extracted.downloadable_urls == (
        "https://example.com/title",
        "https://example.com/autolink",
        "https://example.com/a_(b).zip",
        "https://example.com/search?q=!",
        "https://example.com/a.",
        "https://example.com/reference",
    )


def test_markdown_rejects_excessively_long_url(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text("http://" * 1_000)

    with pytest.raises(ExtractionError, match="URL preview exceeds limit"):
        LocalInputExtractor(tmp_path).extract(_request())


def test_markdown_rejects_too_many_urls(tmp_path: Path) -> None:
    urls = "\n".join(f"https://example.com/file-{position}" for position in range(257))
    (tmp_path / "guide.md").write_text(urls)

    with pytest.raises(ExtractionError, match="URL preview exceeds limit"):
        LocalInputExtractor(tmp_path).extract(_request())


def test_markdown_primary_file_is_independent_of_upload_order() -> None:
    uploads = (Upload("image.png"), Upload("guide.md"))

    assert primary_upload_name("markdown", uploads) == "guide.md"


@pytest.mark.parametrize(
    "uploads",
    (
        (Upload("image.png"),),
        (Upload("first.md"), Upload("second.md")),
    ),
)
def test_markdown_requires_exactly_one_primary_file(
    uploads: tuple[Upload, ...],
) -> None:
    with pytest.raises(ValueError, match="exactly one primary file"):
        primary_upload_name("markdown", uploads)


def test_markdown_rejects_referenced_non_image(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text("![not image](note.txt)")
    (tmp_path / "note.txt").write_text("not an image")

    with pytest.raises(ExtractionError, match="image content"):
        LocalInputExtractor(tmp_path).extract(_request())


def test_stage_uploads_accepts_unicode_spaces_and_parentheses(tmp_path: Path) -> None:
    name = "Онбординг для ученика (Pro Max).md"

    staged = stage_uploads(tmp_path, (Upload(name),))

    assert (staged / name).read_bytes() == b"content"


@pytest.mark.parametrize(
    "name",
    (
        "../x.md",
        "a/b.md",
        "a\\b.md",
        "control\x00.md",
        ".hidden",
        "-option.md",
        "a..b.md",
        "a" * 129,
    ),
)
def test_stage_uploads_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="unique safe basenames"):
        stage_uploads(tmp_path, (Upload(name),))


def test_stage_uploads_rejects_duplicate_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unique safe basenames"):
        stage_uploads(tmp_path, (Upload("same.md"), Upload("same.md")))
