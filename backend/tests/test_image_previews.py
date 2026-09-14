import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.media_links import sign_path
from app.routers import media


def test_large_image_preview_is_bounded_cached_and_original_is_preserved():
    app = FastAPI()
    app.include_router(media.media_router)
    with TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / 'large.png'
        Image.new('RGB', (4096, 3072), '#b99aff').save(source)
        original = source.read_bytes()
        with patch.object(media.settings, 'upload_dir', root), TestClient(app) as client:
            url = sign_path('/media/uploads/large.png')
            response = client.get(url + '&preview=1')
            assert response.status_code == 200
            preview = Image.open(io.BytesIO(response.content))
            assert max(preview.size) <= 512
            assert response.headers['content-type'] == 'image/webp'
            assert len(response.content) < len(original)
            assert client.get(url + '&preview=1').content == response.content
            assert client.get(url).content == original
            assert client.get('/media/uploads/large.png?preview=1').status_code == 403


def test_preview_decoding_does_not_block_other_requests():
    from app import image_previews

    app = FastAPI()
    app.include_router(media.media_router)

    @app.get('/navigation')
    async def navigation():
        return {'ready': True}

    entered, release = Event(), Event()
    original_build = image_previews._build

    def slow_decode(source):
        entered.set()
        assert release.wait(5)
        return original_build(source)

    with TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new('RGB', (32, 32)).save(root / 'slow.png')
        with patch.object(media.settings, 'upload_dir', root), patch.object(image_previews, '_build', slow_decode), TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as requests:
            preview = requests.submit(client.get, sign_path('/media/uploads/slow.png') + '&preview=1')
            try:
                assert entered.wait(2)
                response = requests.submit(client.get, '/navigation').result(timeout=1)
                assert response.json() == {'ready': True}
            finally:
                release.set()
            assert preview.result(timeout=2).status_code == 200
