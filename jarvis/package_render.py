"""Constrained reconstructed HTML rendering in a cancellable subprocess.

No JARVIS origin, parent bridge, credentials, external network, downloads,
service workers, input or arbitrary control clicks are available.
"""
from __future__ import annotations
import asyncio
import base64
import json
import sys
from urllib.parse import unquote, urlsplit
from jarvis import bundle_archive
from jarvis.bundle_contract import RenderedPage, RenderedPageInfo
from jarvis.package_content_contract import InspectionError

async def render(inspection, owner, snapshot, location, options):
    members = inspection.snapshots.members(owner, snapshot)
    selected = next(m for m in members if m.member_id == location.member_id)
    if location.slide_id and not any(m.relative_path == 'deck-data.js' for m in members):
        raise InspectionError('runtime_state_unavailable', 'A reconstructed custom page only shows its initial loaded state.')
    resources, total = {}, 0
    for member in members:
        if member.relative_path.startswith('results/'):
            continue
        if total + member.size > 64 * 1024 * 1024:
            raise InspectionError('budget', 'The reconstructed page exceeds its resource byte budget.')
        data = inspection.snapshots.source(owner, snapshot, member.member_id).read()
        resources[member.relative_path] = base64.b64encode(data).decode('ascii')
        total += len(data)
    payload = json.dumps({'resources': resources, 'entry': selected.relative_path,
        'slide': location.slide_id, 'max_edge': options.max_edge}).encode()
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'jarvis.package_render', '--worker',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(process.communicate(payload), 12)
        if process.returncode:
            raise InspectionError('renderer_unavailable', 'The isolated HTML renderer could not finish.')
        raw = json.loads(out)
        if raw.get('error'):
            raise InspectionError('renderer_unavailable', raw['error'])
        png = base64.b64decode(raw['png'], validate=True)
        if len(png) > 4_000_000:
            raise InspectionError('budget', 'The reconstructed image exceeds the tool image budget.')
        return RenderedPage(RenderedPageInfo(member_id=location.member_id, page=1, page_count=1, width=raw['width'], height=raw['height'], media_type='image/png'), png)
    except asyncio.TimeoutError:
        raise InspectionError('renderer_timeout', 'The isolated renderer exceeded its deadline.', retryable=True) from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()

async def worker(payload):
    from playwright.async_api import async_playwright
    import mimetypes
    resources = payload['resources']
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            edge = min(1600, max(256, payload['max_edge']))
            context = await browser.new_context(viewport={'width': edge, 'height': min(1000, edge)},
                service_workers='block', accept_downloads=False, permissions=[])
            async def route(request):
                url = urlsplit(request.request.url)
                path = unquote(url.path).lstrip('/')
                if url.scheme != 'https' or url.netloc != 'package.invalid' or request.request.method != 'GET' or path not in resources:
                    await request.abort(); return
                try:
                    bundle_archive.normalised(path)
                except bundle_archive.ArchiveError:
                    await request.abort(); return
                await request.fulfill(body=base64.b64decode(resources[path]),
                    content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream',
                    headers={'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'"})
            await context.route('**/*', route)
            async def refuse_socket(socket):
                await socket.close()
            await context.route_web_socket('**/*', refuse_socket)
            await context.add_init_script("Object.defineProperty(window,'RTCPeerConnection',{value:undefined,configurable:false});Object.defineProperty(window,'WebTransport',{value:undefined,configurable:false});")
            page = await context.new_page()
            page.on('popup', lambda popup: asyncio.create_task(popup.close()))
            page.on('download', lambda download: asyncio.create_task(download.cancel()))
            await page.goto('https://package.invalid/' + payload['entry'], wait_until='domcontentloaded', timeout=5000)
            if payload.get('slide'):
                # Declarative factory slide classes only; no package controls are clicked.
                found = await page.evaluate('''id => {const target=document.getElementById(id); if(!target || !target.classList.contains('slide')) return false;
                  document.querySelectorAll('.slide').forEach(el=>el.classList.toggle('current',el===target)); return true;}''', payload['slide'])
                if not found:
                    raise ValueError('The declarative factory slide is unavailable.')
            await page.wait_for_timeout(150)
            png = await page.screenshot(type='png', timeout=5000)
            from jarvis.screen import _png_size
            width, height = _png_size(png)
            return {'png': base64.b64encode(png).decode(), 'width': width, 'height': height}
        finally:
            await browser.close()

if __name__ == '__main__':
    try:
        result = asyncio.run(worker(json.loads(sys.stdin.buffer.read(96 * 1024 * 1024))))
    except Exception:
        result = {'error': 'The isolated HTML rendering capability is unavailable.'}
    sys.stdout.write(json.dumps(result))
