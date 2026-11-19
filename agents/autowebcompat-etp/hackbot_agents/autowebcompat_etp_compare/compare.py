"""Pixel comparison of the configurations' screenshots."""

from pathlib import Path

from PIL import Image, ImageChops

from .result import ScreenshotDiff

# Differing pixels are painted this color over a faded copy of the screenshot.
HIGHLIGHT = (255, 0, 0)


def diff_screenshots(
    reference: Path, other: Path, diff_path: Path | None = None
) -> ScreenshotDiff:
    """Compare `other` to `reference` pixel by pixel.

    If they are the same size but differ and `diff_path` is given, saves an
    image there that shows `other` faded, with the differing pixels in red.
    """
    with Image.open(reference) as ref_image, Image.open(other) as other_image:
        ref = ref_image.convert("RGB")
        image = other_image.convert("RGB")
    if ref.size != image.size:
        return ScreenshotDiff(identical=False, size=image.size, reference_size=ref.size)

    # A pixel differs if any channel does.
    r, g, b = ImageChops.difference(ref, image).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(
        lambda v: 255 if v else 0
    )
    bbox = mask.getbbox()
    if bbox is None:
        return ScreenshotDiff(identical=True, size=image.size, reference_size=ref.size)

    width, height = image.size
    changed = mask.histogram()[255]
    if diff_path is not None:
        faded = Image.blend(
            image.convert("L").convert("RGB"),
            Image.new("RGB", image.size, "white"),
            0.6,
        )
        faded.paste(HIGHLIGHT, mask=mask)
        faded.save(diff_path, optimize=True)
    return ScreenshotDiff(
        identical=False,
        size=image.size,
        reference_size=ref.size,
        bbox=bbox,
        changed_fraction=changed / (width * height),
    )
