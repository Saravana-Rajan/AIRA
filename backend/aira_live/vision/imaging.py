"""Pure image helpers. Read B runs on an enhanced copy so the two reads are genuinely independent."""
import base64
import io

from PIL import Image, ImageFilter, ImageOps

MIN_WIDTH = 1200
MIN_UPSCALE = 1.5


def enhance_for_reading(jpeg_b64: str) -> str:
    """Grayscale, auto-contrast, upscale (at least 1.5x, up to MIN_WIDTH) and sharpen a JPEG."""
    img = Image.open(io.BytesIO(base64.b64decode(jpeg_b64)))
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if img.width < MIN_WIDTH:
        scale = max(MIN_UPSCALE, MIN_WIDTH / img.width)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()
