"""HTTP adapter for the shared package inspection service."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote
from fastapi import Body, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from jarvis import package_html, packages_api, web_auth
from jarvis.bundle_contract import PageLocation, RenderOptions
from jarvis.package_content_contract import InspectionError, PackageLocation, PackageRef


def failure(error):
    return JSONResponse({"error": error.failure.model_dump(mode="json")}, status_code=409)


def mount(app, inspection, views, owner, *, prepare_followup=None):
    async def snapshot(package_id, revision, path):
        try:
            # Resolve a registered revision; no caller path can choose a cache directory.
            with inspection.snapshots.db() as conn:
                from jarvis.package_snapshots import owner_key
                row = conn.execute("SELECT snapshot_id FROM package_content_snapshots WHERE owner=? AND package_id=? AND content_revision=? ORDER BY created_at DESC LIMIT 1",
                    (owner_key(owner()), package_id, revision)).fetchone()
            if row is None:
                raise InspectionError("snapshot_not_found", "That mounted source revision is unavailable.")
            snap = inspection.snapshots.get(owner(), row[0])
            if path.startswith("results/"):
                current = await asyncio.to_thread(inspection.snapshots.refresh_results, owner(), snap)
                return RedirectResponse(f"/api/packages/{quote(package_id, safe='')}/files/@results/{current.results_revision}/{quote(path, safe='/')}", status_code=307)
            member = next((m for m in inspection.snapshots.members(owner(), snap)
                if m.relative_path == path), None)
            if member is None:
                raise InspectionError("member_not_found", "That source member is unavailable in this snapshot.")
            data = await asyncio.to_thread(inspection.snapshots.source(owner(), snap, member.member_id).read)
            old = f"/api/packages/{quote(package_id, safe='')}/files/"
            new = old + f"@snapshots/{revision}/"
            limits = ()
            if member.detected_format == "html":
                data, limits = package_html.serve_html(data, old, new)
            elif path.lower().endswith(".css"):
                from jarvis.bundle_readers import _paging
                encoding = _paging.encoding_of(data)
                data = package_html.rewrite_css(data.decode(encoding), old, new).encode(encoding)
            return Response(data, media_type=packages_api.media_type(path),
                headers={"Cache-Control": "no-store", **({"X-Jarvis-Observation": ",".join(limits)} if limits else {})})
        except InspectionError as e:
            return failure(e)

    async def results(package_id, revision, path):
        try:
            from jarvis.package_snapshots import owner_key
            with inspection.snapshots.db() as conn:
                row = conn.execute("SELECT snapshot_id FROM package_content_snapshots WHERE owner=? AND package_id=? AND results_revision=? ORDER BY created_at DESC LIMIT 1", (owner_key(owner()), package_id, revision)).fetchone()
            if not row or not path.startswith("results/"):
                raise InspectionError("snapshot_not_found", "That results revision is unavailable.")
            snap = inspection.snapshots.get(owner(), row[0])
            member = next((m for m in inspection.snapshots.members(owner(), snap) if m.relative_path == path), None)
            if member is None:
                raise InspectionError("member_not_found", "That results member is unavailable.")
            data = inspection.snapshots.source(owner(), snap, member.member_id).read()
            return Response(data, media_type=packages_api.media_type(path), headers={"Cache-Control": "no-store"})
        except InspectionError as error:
            return failure(error)

    async def overview(package_id, inbox_id: str | None = None, expected_revision: str | None = None):
        try:
            got = await asyncio.wait_for(inspection.inspect(owner=owner(),
                package=PackageRef(package_id=package_id, inbox_id=inbox_id), expected_revision=expected_revision), 15)
            return got.model_dump(mode="json")
        except InspectionError as e:
            return failure(e)
        except asyncio.TimeoutError:
            return failure(InspectionError("indexing", "Snapshot preparation exceeded the request deadline; retry the exact package.", retryable=True))

    async def members(package_id, snapshot_id: str, continuation: str | None = None):
        try:
            snap = checked_snapshot(package_id, snapshot_id)
            return (await inspection.members(owner=owner(), snapshot=snap, continuation=continuation)).model_dump(mode="json")
        except InspectionError as e:
            return failure(e)

    def checked_snapshot(package_id, sid):
        snap = inspection.snapshots.get(owner(), sid)
        if snap.package.package_id != package_id:
            raise InspectionError("revision_mismatch", "That snapshot belongs to another package.")
        return snap

    async def text(package_id, snapshot_id: str, member_id: str, part: str = "source", continuation: str | None = None, target: str | None = None):
        try:
            try:
                location = PackageLocation.model_validate_json(target) if target else PackageLocation(member_id=member_id, part=part)
            except ValueError:
                raise InspectionError("bad_location", "The requested location does not match the package location schema.") from None
            if location.member_id != member_id:
                raise InspectionError("location_mismatch", "The query member differs from the requested location.")
            got = await asyncio.wait_for(inspection.read(owner=owner(), snapshot=checked_snapshot(package_id, snapshot_id),
                location=location, continuation=continuation), 15)
            return got.model_dump(mode="json")
        except InspectionError as e:
            return failure(e)
        except asyncio.TimeoutError:
            return failure(InspectionError("indexing", "Reading exceeded the request deadline.", retryable=True))

    async def search(package_id, snapshot_id: str, query: str, continuation: str | None = None):
        try:
            got = await inspection.search(owner=owner(), snapshot=checked_snapshot(package_id, snapshot_id),
                                           query=query, continuation=continuation)
            return got.model_dump(mode="json")
        except InspectionError as e:
            return failure(e)

    async def page(package_id, page: int, snapshot_id: str, member_id: str):
        try:
            visual = await asyncio.wait_for(inspection.render(owner=owner(),
                snapshot=checked_snapshot(package_id, snapshot_id),
                location=PackageLocation(member_id=member_id, location=PageLocation(page=page)), options=RenderOptions()), 15)
            return visual.model_dump(mode="json")
        except InspectionError as e:
            return failure(e)

    async def image(package_id, token):
        try:
            data, media_type = inspection.images.resolve(owner(), token, package_id)
            return Response(data, media_type=media_type, headers={"Cache-Control": "no-store"})
        except InspectionError as error:
            return failure(error)

    async def capture(package_id, request: Request, body: dict = Body(default_factory=dict)):
        if not packages_api.his_click(request):
            return JSONResponse({"error": packages_api.NOT_HIS_ACT}, status_code=403)
        try:
            mount = views.choose(owner(), PackageRef(package_id=package_id), body.get("client"))
            visual = await views.capture(owner=owner(), view=mount.view, display=body.get("display", 1))
            return visual.model_dump(mode="json")
        except InspectionError as error:
            return failure(error)

    async def current(package_id, client: str | None = None):
        try:
            got = await views.observe(owner=owner(), package=PackageRef(package_id=package_id), client_id=client)
            return got.model_dump(mode="json")
        except InspectionError as e:
            return failure(e)

    async def observe(package_id, request: Request, body: dict = Body(default_factory=dict)):
        if not packages_api.his_click(request):
            return JSONResponse({"error": packages_api.NOT_HIS_ACT}, status_code=403)
        return await current(package_id, body.get("client"))

    async def followup(package_id, request: Request, body: dict = Body(default_factory=dict)):
        if not packages_api.his_click(request):
            return JSONResponse({"error": packages_api.NOT_HIS_ACT}, status_code=403)
        if prepare_followup is None:
            return failure(InspectionError("intent_unavailable", "Follow-up preparation is unavailable."))
        try:
            return await prepare_followup(package_id, body, request)
        except InspectionError as e:
            return failure(e)

    async def helper():
        from jarvis import data_paths
        return Response((data_paths.PACKAGE_DIR / "package_observer.js").read_bytes(), media_type="text/javascript", headers={"Cache-Control": "no-store"})

    # These routes precede the original file catch-all.
    app.add_api_route(package_html.HELPER_PATH, helper, methods=["GET"])
    app.add_api_route("/api/packages/{package_id}/files/@results/{revision}/{path:path}", results, methods=["GET"])
    app.add_api_route("/api/packages/{package_id}/files/@snapshots/{revision}/{path:path}", snapshot, methods=["GET"])
    base = "/api/packages/{package_id}/inspection"
    for path, handler, methods in [("", overview, ["GET"]), ("/members", members, ["GET"]),
        ("/text", text, ["GET"]), ("/search", search, ["GET"]), ("/pages/{page}", page, ["GET"]),
        ("/images/{token}", image, ["GET"]), ("/view/capture", capture, ["POST"]), ("/view", current, ["GET"]), ("/view/observe", observe, ["POST"]), ("/followup", followup, ["POST"])]:
        app.add_api_route(base + path, handler, methods=methods)
