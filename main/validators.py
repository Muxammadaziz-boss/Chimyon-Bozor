import os
from django.core.exceptions import ValidationError

ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.ico']
MAX_IMAGE_SIZE_MB = 5  # 5 Megabytes


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
