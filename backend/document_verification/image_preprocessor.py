"""Image Preprocessing Strategy for OCR Document Verification in Suraksha Setu.

Provides:
- Automatic EXIF orientation correction
- Resolution checking and high-fidelity Lanczos upscaling for small text/dates
- Adaptive contrast and sharpness enhancement
- Grayscale conversion
- Multi-candidate image generation for controlled fallback OCR without excessive compression
"""

import io
import logging
from enum import Enum
from typing import Dict, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# Minimum target dimension for clear OCR text and date detection
MIN_OCR_DIMENSION_PX = 1200
MAX_OCR_DIMENSION_PX = 3000


class PreprocessingVariant(str, Enum):
    """Available controlled image preprocessing variants for OCR."""

    ORIGINAL = "ORIGINAL"
    ENHANCED = "ENHANCED"
    CONTRAST_BOOSTED = "CONTRAST_BOOSTED"
    UPSCALED = "UPSCALED"
    GRAYSCALE_SHARPENED = "GRAYSCALE_SHARPENED"


class ImagePreprocessor:
    """Handles image normalization, orientation correction, and enhancement for OCR."""

    @staticmethod
    def is_image_format(content_type: str, filename: str) -> bool:
        """Check if upload is an image format supported by PIL."""
        ct = (content_type or "").lower()
        fn = (filename or "").lower()
        if any(ct.startswith(p) for p in ("image/jpeg", "image/jpg", "image/png", "image/webp")):
            return True
        return any(fn.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))

    @classmethod
    def load_and_orient(cls, file_bytes: bytes) -> Optional[Image.Image]:
        """Load image bytes and apply EXIF orientation correction.

        Returns None if file_bytes is not a decodable image (e.g. PDF).
        """
        try:
            img = Image.open(io.BytesIO(file_bytes))
            # Automatically rotate based on EXIF metadata (crucial for phone uploads)
            img = ImageOps.exif_transpose(img)

            # Convert RGBA/Palette/CMYK to standard RGB
            if img.mode in ("RGBA", "LA", "P"):
                # Preserve white background for transparent regions
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    background.paste(img, mask=img.split()[3])
                else:
                    background.paste(img.convert("RGBA"))
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            return img
        except Exception as exc:
            logger.debug("ImagePreprocessor could not decode image with PIL: %s", exc)
            return None

    @classmethod
    def upscale_if_small(
        cls, img: Image.Image, min_dim: int = MIN_OCR_DIMENSION_PX
    ) -> Tuple[Image.Image, float]:
        """Upscale image if smaller than min_dim to preserve small date and MRZ characters.

        Returns:
            Tuple of (upscaled_image, scale_factor)
        """
        w, h = img.size
        min_current = min(w, h)
        if min_current < min_dim:
            scale = min_dim / float(min_current)
            new_w = int(w * scale)
            new_h = int(h * scale)
            # Use high-quality Lanczos resampling
            upscaled = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            logger.info(
                "ImagePreprocessor: upscaled image from (%d, %d) to (%d, %d) (scale=%.2f)",
                w,
                h,
                new_w,
                new_h,
                scale,
            )
            return upscaled, scale
        return img, 1.0

    @classmethod
    def enhance_for_ocr(cls, img: Image.Image) -> Image.Image:
        """Apply balanced contrast, brightness, and sharpness enhancement for OCR readability."""
        # 1. Mild unsharp mask to clarify small typography (dates, serial numbers)
        sharpened = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=130, threshold=3))

        # 2. Enhance contrast moderately (prevents washed-out text)
        contrast = ImageEnhance.Contrast(sharpened)
        enhanced = contrast.enhance(1.25)

        # 3. Enhance sharpness
        sharpness = ImageEnhance.Sharpness(enhanced)
        enhanced = sharpness.enhance(1.2)

        return enhanced

    @classmethod
    def generate_variants(cls, file_bytes: bytes) -> Dict[PreprocessingVariant, bytes]:
        """Generate controlled preprocessing variants for primary and fallback OCR passes.

        Variants:
        1. ORIGINAL: Untouched original bytes
        2. ENHANCED: Oriented, upscaled, balanced contrast/sharpness
        3. CONTRAST_BOOSTED: Higher contrast for faint/washed-out text
        4. GRAYSCALE_SHARPENED: Grayscale with edge sharpening
        """
        variants: Dict[PreprocessingVariant, bytes] = {
            PreprocessingVariant.ORIGINAL: file_bytes
        }

        img = cls.load_and_orient(file_bytes)
        if img is None:
            # Not a PIL-supported image (e.g. PDF); return original
            return variants

        # Variant 1: Enhanced (Oriented + Upscaled + Enhanced)
        upscaled, _ = cls.upscale_if_small(img)
        enhanced = cls.enhance_for_ocr(upscaled)
        buf_enhanced = io.BytesIO()
        enhanced.save(buf_enhanced, format="JPEG", quality=95)
        variants[PreprocessingVariant.ENHANCED] = buf_enhanced.getvalue()

        # Variant 2: Contrast Boosted (For low-contrast or noisy scans)
        contrast_booster = ImageEnhance.Contrast(upscaled)
        contrast_boosted = contrast_booster.enhance(1.6)
        buf_contrast = io.BytesIO()
        contrast_boosted.save(buf_contrast, format="JPEG", quality=95)
        variants[PreprocessingVariant.CONTRAST_BOOSTED] = buf_contrast.getvalue()

        # Variant 3: Grayscale Sharpened (For documents with colored security backgrounds)
        gray = ImageOps.grayscale(upscaled)
        gray_enhanced = ImageOps.autocontrast(gray, cutoff=2)
        buf_gray = io.BytesIO()
        gray_enhanced.save(buf_gray, format="JPEG", quality=95)
        variants[PreprocessingVariant.GRAYSCALE_SHARPENED] = buf_gray.getvalue()

        return variants
