"""Shared capture preparation; approval and delivery remain in transmission."""
from __future__ import annotations
from jarvis import transmission

class CapturePreparation:
    def __init__(self, record, show, heard):
        self.record, self.show, self.heard = record, show, heard

    def set(self, capture, text, to, to_session, *, package_context=None):
        readdressed = bool(to_session) and to_session != capture.get('to_session')
        changed = capture['text'] != text
        context_changed = package_context is not None and capture.get('package_context') != package_context
        if changed or readdressed or context_changed:
            transmission.changed(capture)
        capture['text'] = text
        if package_context is not None:
            capture['package_context'] = package_context
        if to_session:
            capture['to'], capture['to_session'] = to, to_session
        if readdressed:
            capture.pop('for', None)
        heard = self.heard()
        if heard is not None and heard not in capture['heard']:
            capture['heard'].append(heard)
        if changed or readdressed or context_changed:
            self.record(capture, to_session)
        self.show(capture, 'capturing')
