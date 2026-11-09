"""Built frontend and MCP tools against an isolated real HTTP/WebSocket server."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from tests.test_brain_page_parity import wired  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_work_packages import make_zip, write_zip

pytestmark = pytest.mark.page

HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>Package content review</title>
<style>body{font:20px system-ui;background:#17202b;color:#e8edf4;padding:32px}button{font:inherit;margin:8px}section{padding:12px}.hidden{display:none}img{width:360px}</style></head>
<body><h1>Package content review</h1><button id="costs" role="tab" aria-selected="true">Costs</button>
<button id="delivery" role="tab" aria-selected="false">Delivery</button>
<section id="costs-panel"><h2>Costs</h2><p id="estimate">The source estimate is ten dollars.</p><details id="assumptions"><summary>Assumptions</summary><p id="detail">Two hours of setup.</p></details>
<img src="diagram.svg" alt="Caption: Operations funds Budget."></section>
<section id="delivery-panel" class="hidden"><h2>Delivery</h2><p>Delivery is scheduled for Friday.</p></section>
<script>for(const id of ['costs','delivery'])document.getElementById(id).onclick=()=>{for(const tab of ['costs','delivery']){document.getElementById(tab).setAttribute('aria-selected',String(tab===id));document.getElementById(tab+'-panel').classList.toggle('hidden',tab!==id);}};</script></body></html>'''
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="160"><rect width="600" height="160" fill="white"/><text x="30" y="80" font-size="28">Budget</text><path d="M180 70 H390 M370 50 L395 70 L370 90" stroke="black" stroke-width="6" fill="none"/><text x="410" y="80" font-size="28">Operations</text></svg>'''

@pytest.fixture
def isolated_server(wired, monkeypatch, tmp_path):
    from jarvis import data_paths, work_packages
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({'index.html': HTML, 'diagram.svg': SVG,
        'brief.md': '# Review\nRead the files before answering.\n', 'data.csv': 'item,cost\nsetup,10\n',
        'unsupported.mp3': b'ID3unsupported'}, {'name': 'package-content-review', 'version': '1.0.0',
        'purpose': 'Inspect source, current tabs, and visual evidence.', 'runs': 'page', 'entry': 'index.html', 'returns': ['results/*']})), 'demo1234', clear_mark=lambda p: None)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    monkeypatch.setenv('JARVIS_PORT', str(port))
    monkeypatch.setenv('JARVIS_TOOL_URL', f'http://127.0.0.1:{port}/internal/tool')
    monkeypatch.setenv('JARVIS_TOOL_TOKEN_FILE', str(data_paths.tool_token_path()))
    data_paths.ensure_tool_token()
    config = uvicorn.Config(wired.server.app, host='127.0.0.1', port=port, lifespan='off', log_level='error')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield wired.server, package, f'http://127.0.0.1:{port}', tmp_path
    server.should_exit = True
    thread.join(5)
    sock.close()


def envelope(text):
    return json.loads(text.split('\n', 1)[1].rsplit('\n', 1)[0])


async def loaded_package_frame(page):
    element = await page.locator('#package-view iframe').element_handle()
    frame = await element.content_frame()
    await frame.wait_for_load_state('load')
    return frame

@pytest.mark.asyncio(loop_scope='session')
async def test_generic_page_state_and_visual_mcp_evidence(page_playwright, isolated_server):
    from tests import dashboard_page
    from jarvis import jarvis_mcp
    server, package, base, tmp = isolated_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    transcript = []
    async with page_playwright as playwright:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.add_init_script("window.__packageEvidence=[];addEventListener('message',e=>{if(e.data?.type==='observe.response')window.__packageEvidence.push({json:JSON.stringify(e.data),undefinedBlocks:e.data.blocks.filter(b=>Object.values(b).some(v=>v===undefined)).length});});")
        await page.goto(base)
        for _ in range(100):
            if server.ui_clients.pages(): break
            await asyncio.sleep(.05)
        assert server.ui_clients.pages(), 'The built page must register its actual voice connection.'
        async with httpx.AsyncClient(base_url=base) as http:
            opened = await http.post(f'/api/packages/{package.id}/page', headers={'Origin': base})
            assert opened.status_code == 200, opened.text
            await page.locator('#package-view iframe').wait_for()
            for _ in range(100):
                if server.package_views.mounts: break
                await asyncio.sleep(.05)
            assert server.package_views.mounts
            client = next(iter(server.package_views.mounts.values())).view.client_id
            frame = await loaded_package_frame(page)
            await frame.locator('#assumptions summary').click()
            await frame.locator('#detail').evaluate('(el)=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r);}')
            args = {'package': package.id, 'client': client}
            def mcp(tool, arguments):
                return jarvis_mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': arguments}})
            first = await asyncio.to_thread(mcp, 'observe_package_view', args)
            content = first['result']['content'][0]['text']
            first_page = envelope(content)
            assert 'text' in first_page, (first_page, await page.evaluate('window.__packageEvidence'))
            whole = first_page['text']
            while first_page['next']:
                continuation = await asyncio.to_thread(mcp, 'observe_package_view', {**args, 'continuation': first_page['next']})
                first_page = envelope(continuation['result']['content'][0]['text'])
                whole += first_page['text']
            observed = json.loads(whole)
            assert observed['selection'] == 'Two hours of setup.'
            assert 'Two hours of setup.' in observed['text']
            assert observed['location']['member_id']
            transcript.append({'request': 'Inspect the current Costs tab and selected assumption.', 'tool': 'observe_package_view', 'result': observed})
            await frame.locator('#delivery').click()
            await asyncio.sleep(.3)
            second = await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': client})
            assert second.status_code == 200, second.text
            current = second.json()
            assert 'Delivery is scheduled for Friday.' in current['content']['chunks'][0]['text']
            assert 'The source estimate is ten dollars.' not in current['content']['chunks'][0]['text']
            transcript.append({'request': 'Inspect the current tab after manual navigation.', 'tool': 'observe_package_view', 'result': current})
            overview = (await http.get(f'/api/packages/{package.id}/inspection')).json()
            member = next(m for m in overview['first_members'] if m['relative_path'] == 'diagram.svg')
            image = await asyncio.to_thread(mcp, 'look_at_package_content', {'snapshot': overview['snapshot']['snapshot_id'],
                'member': member['member_id'], 'evidence_kind': 'reconstructed_render'})
            assert not image['result']['isError'], image
            blocks = image['result']['content']
            assert any(b['type'] == 'image' for b in blocks), image
            metadata = envelope(blocks[0]['text'])
            assert metadata['kind'] == 'reconstructed_render'
            assert metadata['image']['kind'] == 'document'
            import base64
            output = Path(os.getenv('JARVIS_PACKAGE_REVIEW_DIR', str(tmp / 'review')))
            output.mkdir(parents=True, exist_ok=True)
            (output / 'diagram-render.png').write_bytes(base64.b64decode(next(b['data'] for b in blocks if b['type'] == 'image')))
            transcript.append({'request': 'Inspect the arrows as pixels rather than trusting the caption.', 'tool': 'look_at_package_content', 'result': metadata})
            await page.locator(".centre-tabs [aria-label='package-content-review']").click()
            await page.locator('#package-view iframe').wait_for(state='visible')
            await asyncio.sleep(.1)
            await page.screenshot(path=str(output / 'current-delivery-tab.png'), full_page=True)
            await page.locator(".centre-tabs [aria-label='Conversation']").click()
            await asyncio.sleep(.1)
            hidden = await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': client})
            assert hidden.status_code == 409 and hidden.json()['error']['code'] == 'view_unavailable'
            await page.locator(".centre-tabs [aria-label='package-content-review']").click()
            await asyncio.sleep(.1)
            assert (await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': client})).status_code == 200
            (output / 'mcp-transcript.json').write_text(json.dumps(transcript, ensure_ascii=False, indent=2) + '\n')
        await browser.close()

@pytest.mark.asyncio(loop_scope='session')
async def test_factory_keyboard_navigation_and_two_clients_keep_distinct_views(page_playwright, isolated_server, monkeypatch):
    from tests import dashboard_page
    from jarvis import work_packages, make_package
    server, _, base, tmp = isolated_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    archive = tmp / 'factory-deck.zip'
    example = Path(__file__).resolve().parents[1] / 'examples/packages/decision-deck'
    assert make_package._zipper().main([str(example), str(archive)]) == 0
    package, _ = work_packages.unpack(archive, 'factory123', clear_mark=lambda p: None)
    monkeypatch.setattr(server.package_views, 'narration', lambda: {'package': package.id,
        'state': 'telling', 'slide': 1, 'storyRevision': 'fixture-narration-revision', 'completedThroughBeat': None})
    async with page_playwright as playwright:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        first = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        await first.goto(base)
        async with httpx.AsyncClient(base_url=base) as http:
            for _ in range(100):
                if server.ui_clients.pages(): break
                await asyncio.sleep(.05)
            assert (await http.post(f'/api/packages/{package.id}/page', headers={'Origin': base})).status_code == 200
            frame = await loaded_package_frame(first)
            await frame.locator('#next').click()
            await frame.locator('body').press('ArrowRight')
            first_slide = await frame.locator('.slide.current').get_attribute('id')
            for _ in range(100):
                mounts = [m for m in server.package_views.mounts.values() if m.view.package.package_id == package.id and m.view.sequence > 0]
                if mounts: break
                await asyncio.sleep(.05)
            first_client = mounts[0].view.client_id
            await first.locator(".centre-tabs [aria-label='decision-deck-example']").click()
            await asyncio.sleep(.2)
            first_response = await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': first_client})
            assert first_response.status_code == 200, first_response.text
            one = first_response.json()
            assert one['active_location']['slide_id'] == first_slide
            assert one['narration']['slide_id'] == '1'
            second = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            await second.goto(base)
            await second.frame_locator('#package-view iframe').locator('.slide.current').wait_for()
            await loaded_package_frame(second)
            for _ in range(100):
                mounts = [m for m in server.package_views.mounts.values() if m.view.package.package_id == package.id and m.view.client_id != first_client and m.view.sequence > 0]
                if mounts: break
                await asyncio.sleep(.05)
            second_client = mounts[0].view.client_id
            await second.locator(".centre-tabs [aria-label='decision-deck-example']").click()
            await asyncio.sleep(.2)
            second_response = await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': second_client})
            assert second_response.status_code == 200, second_response.text
            two = second_response.json()
            assert two['active_location']['slide_id'] != first_slide
            ambiguous = await http.get(f'/api/packages/{package.id}/inspection/view')
            assert ambiguous.status_code == 409 and ambiguous.json()['error']['code'] == 'ambiguous_view'
            await first.bring_to_front()
            await first.locator(".centre-tabs [aria-label='decision-deck-example']").click()
            await asyncio.sleep(.2)
            again = (await http.get(f'/api/packages/{package.id}/inspection/view', params={'client': first_client})).json()
            assert again['active_location']['slide_id'] == first_slide
        await browser.close()
