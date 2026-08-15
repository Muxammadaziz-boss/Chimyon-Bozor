import os
import re
from django.core.exceptions import ValidationError

ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.ico']
MAX_IMAGE_SIZE_MB = 5  # 5 Megabytes

# Canonical Uzbekistan Phone Regex: +998 followed by exactly 9 digits
PHONE_RE = re.compile(r'^\+998\d{9}$')


def normalize_uz_phone(raw_phone) -> str | None:
    """
    Normalizes any valid Uzbekistan phone number into canonical '+998XXXXXXXXX' format.
    Accepts:
      - '901234567' -> '+998901234567'
      - '998901234567' -> '+998901234567'
      - '+998901234567' -> '+998901234567'
      - '+998 90 123 45 67' -> '+998901234567'
      - '998 90 123 45 67' -> '+998901234567'
      - '90 123 45 67' -> '+998901234567'
    Rejects:
      - Any strings with letters (e.g. '+998901234567a', 'abc')
      - Non-Uzbekistan country codes (e.g. '+997901234567', '+123456789')
      - Numbers with wrong number of digits (e.g. '+99890123456', '+9989012345678')
    Returns:
      Canonical phone string '+998XXXXXXXXX' if valid, or None if invalid.
    """
    if not raw_phone:
        return None

    raw = str(raw_phone).strip()
    if not re.match(r'^\+?[0-9\s\-()]+$', raw):
        return None

    digits = re.sub(r'\D', '', raw)

    if raw.startswith('+'):
        # With '+' prefix, must be +998 followed by 9 digits = 12 digits
        if digits.startswith('998') and len(digits) == 12:
            canonical = '+' + digits
            return canonical if PHONE_RE.match(canonical) else None
        return None
    else:
        # Without '+' prefix
        if digits.startswith('998') and len(digits) == 12:
            canonical = '+' + digits
            return canonical if PHONE_RE.match(canonical) else None
        elif len(digits) == 9:
            canonical = '+998' + digits
            return canonical if PHONE_RE.match(canonical) else None
        return None


def validate_image_file(file):
    """
    Validates uploaded image file size, extension and format.
    Prevents executable or dangerous file uploads.
    """
    if not file:
        return

    # 1. Size check
    if hasattr(file, 'size') and file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise ValidationError(f"Rasm hajmi {MAX_IMAGE_SIZE_MB}MB dan oshmasligi kerak.")

    # 2. Extension check
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(f"Noto'g'ri fayl formati ({ext}). Faqat JPG, JPEG, PNG, WEBP formatlariga ruxsat berilgan.")

    # 3. Image integrity check using Pillow
    try:
        from PIL import Image
        file.seek(0)
        img = Image.open(file)
        img.verify()
        file.seek(0)
    except Exception:
        raise ValidationError("Yuklangan fayl yaroqli rasm emas yoki buzilgan.")

