"""Small cached display copies; generation always reads the original upload."""
import asyncio
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

# Dedicated workers keep image decoding out of both the event loop and the
# request thread pool. A canvas with many images cannot starve normal API calls.
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="image-preview")


def _build(source: Path) -> Path:
    stat = source.stat()
    version = hashlib.sha256(f"{source.name}:{stat.st_size}:{stat.st_mtime_ns}:512-v1".encode()).hexdigest()[:24]
    directory = source.parent / '.previews'
    target = directory / f'{version}.webp'
    if target.is_file():
        return target
    directory.mkdir(exist_ok=True)
    temporary = directory / f'{version}-{uuid.uuid4().hex}.part'
    try:
        with Image.open(source) as original:
            # JPEG can downsample during decode; thumbnail also bounds PNG/BMP.
            original.draft('RGB', (512, 512))
            original.thumbnail((512, 512), Image.Resampling.LANCZOS)
            frame = ImageOps.exif_transpose(original)
            frame = frame.convert('RGBA' if 'A' in frame.getbands() or 'transparency' in frame.info else 'RGB')
            frame.save(temporary, 'WEBP', quality=80, method=3)
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


async def image_preview(source: Path) -> Path:
    return await asyncio.get_running_loop().run_in_executor(_workers, _build, source)
