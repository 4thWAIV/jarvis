"""Short-lived owner-bound image results; never filesystem or public URLs."""
from __future__ import annotations
import secrets
import time
from jarvis.package_content_contract import InspectionError, DocumentImage, ScreenImage, VisualEvidence
from jarvis.package_snapshots import owner_key

class PackageImages:
    def __init__(self):
        self._images = {}

    def keep(self, owner, evidence, data, payload, **metadata):
        now = time.monotonic()
        self._images = {k: v for k, v in self._images.items() if v[0] > now}
        if len(data) > 4_000_000:
            raise InspectionError('budget', 'This image exceeds the tool image budget.')
        while self._images and sum(len(v[2]) for v in self._images.values()) + len(data) > 16_000_000:
            self._images.pop(next(iter(self._images)))
        token = secrets.token_urlsafe(24)
        self._images[token] = (now + 60, owner_key(owner), data, payload.mediaType)
        url = f'/api/packages/{evidence.snapshot.package.package_id}/inspection/images/{token}'
        return VisualEvidence(evidence=evidence, image=payload.model_copy(update={'imageRef': url}), **metadata)

    def resolve(self, owner, token):
        result = self._images.get(token)
        if result is None or result[0] < time.monotonic() or result[1] != owner_key(owner):
            raise InspectionError('image_unavailable', 'This authenticated image result expired or belongs to another owner.')
        return result[2], result[3]

    def document(self, owner, evidence, rendered):
        info = rendered.info
        return self.keep(owner, evidence, rendered.data, DocumentImage(mediaType=info.media_type,
            width=info.width, height=info.height, page=info.page, pageCount=info.page_count, imageRef=''),
            limits=('reconstructed_from_source_bytes',))

    def screen(self, owner, evidence, shot, metadata):
        return self.keep(owner, evidence, shot.png, ScreenImage(width=shot.width, height=shot.height,
            display=metadata['display'], capturedAt=evidence.observed_at, imageRef=''), **metadata)
