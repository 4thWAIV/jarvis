"""Paint preferences preserve workspace geometry, drafts and live connections."""
import json
import pytest
from tests.test_agents_list_fits import async_api
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_settings import _Server
from tests.test_voice_page_queue import _open as queue_open, _queue_tab
from tests.dashboard_page import dashboard, quiet_machine

pytestmark = pytest.mark.page

METRICS = """() => Object.fromEntries([
 '#left-column','#orb-control','#orb-canvas','#right-column','.centre-tabs',
 '#conversation','.chat-playback','.chat-composer','#footer',
 '.agents-list li:first-child','.settings-panel.settings-view','.settings-nav',
 '#settings-system .status-grid','#settings-system .sysinfo-grid'
].flatMap(s=>{
 const e=document.querySelector(s); if(!e || !e.getClientRects().length) return [];
 const r=e.getBoundingClientRect(), c=getComputedStyle(e);
 const props=['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing',
 'paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginBottom',
 'borderTopWidth','borderBottomWidth','gap','gridTemplateColumns','overflowX','overflowY'];
 return [[s,{rect:['x','y','width','height'].map(k=>Math.round(r[k]*10)/10),
   type:Object.fromEntries(props.map(k=>[k,c[k]]))}]];
}))"""

CONTRAST = """e=>{
              const rgb=s=>(s.match(/[\\d.]+/g)||[]).map(Number);
              let bg=[0,0,0], ancestors=[];
              for(let n=e;n;n=n.parentElement) ancestors.unshift(n);
              for(const n of ancestors){const a=rgb(getComputedStyle(n).backgroundColor), alpha=a[3]??1;
                bg=bg.map((c,i)=>(a[i]??0)*alpha+c*(1-alpha));}
              const c=getComputedStyle(e), fg=rgb(c.color);
              const lum=cs=>cs.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
                .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
              const a=lum(fg), z=lum(bg);
              return {ratio:(Math.max(a,z)+.05)/(Math.min(a,z)+.05),background:c.backgroundColor};
            }"""

async def open_settings(page):
    await page.get_by_role('button', name='Menu', exact=True).click()
    await page.get_by_role('button', name='Settings', exact=True).click()
    await page.get_by_role('button', name='System', exact=True).click()
    await page.get_by_label('Appearance').scroll_into_view_if_needed()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(1440, 900), (1100, 700), (800, 450)])
async def test_switching_themes_keeps_geometry_draft_panel_and_connections(page_url, size):
    async with async_api.async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await b.new_page(viewport={'width':size[0], 'height':size[1]}, reduced_motion='reduce')
        await page.add_init_script(FAKE_TRANSCRIPTION)
        voice, server = _Voice(), _Server()
        sockets = []
        page.on('websocket', lambda ws:sockets.append(ws.url))
        await page.route_web_socket('**/ws/voice', voice)
        await page.route_web_socket('**/ws/sessions', lambda ws:ws.on_message(lambda _:None))
        await server.install(page)
        await page.goto(page_url)
        await page.wait_for_selector('.agents-list li')
        editor=page.get_by_role('textbox',name='Message JARVIS')
        await editor.fill('Unsent draft stays here')
        await page.evaluate('window.originalCanvas = document.querySelector("#orb-canvas")')
        await open_settings(page)
        await page.wait_for_timeout(300)
        before=await page.evaluate(METRICS)
        connected=len(sockets)
        for value, rgb in [('graphite','16, 17, 20'), ('classic','8, 13, 20')]:
            await page.get_by_label('Appearance').select_option(value)
            assert await page.evaluate('document.documentElement.dataset.theme')==value
            assert await page.evaluate('getComputedStyle(document.body).backgroundColor')==f'rgb({rgb})'
            assert await page.locator('.settings-panel').evaluate('(e)=>getComputedStyle(e).backgroundColor')==f'rgba({rgb}, 0.92)'
            assert await page.evaluate(METRICS)==before
            assert await page.evaluate('window.originalCanvas === document.querySelector("#orb-canvas")')
            assert await editor.input_value()=='Unsent draft stays here'
            assert await page.get_by_role('dialog',name='Settings').is_visible()
            assert len(sockets)==connected
            assert not server.posts, 'a theme change must not post to the backend'
        await page.get_by_label('Appearance').select_option('graphite')
        await page.get_by_role('button',name='Close',exact=True).click()
        await editor.click()
        assert await editor.evaluate('(e)=>getComputedStyle(e.parentElement).boxShadow') != 'none'
        assert await editor.evaluate('''e => { const p=document.createElement('span'); p.style.color='var(--ui-accent)'; e.parentElement.append(p); const color=getComputedStyle(p).color; p.remove(); return getComputedStyle(e.parentElement).borderColor === color; }''')
        assert await editor.input_value()=='Unsent draft stays here'
        await page.reload()
        assert await page.evaluate('document.documentElement.dataset.theme')=='graphite'
        await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('storage', ['invalid', 'denied'])
async def test_theme_bootstrap_and_storage_failure_are_safe(page_url, storage):
    async with async_api.async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=BROWSER_ARGS)
        page=await b.new_page()
        await page.add_init_script(FAKE_TRANSCRIPTION)
        script=("localStorage.setItem('jarvis.theme','old-theme')" if storage=='invalid' else
                "Object.defineProperty(window,'localStorage',{get(){throw new DOMException('denied','SecurityError')}})")
        await page.add_init_script(script)
        await page.route_web_socket('**/ws/voice',_Voice())
        await page.route_web_socket('**/ws/sessions',lambda ws:ws.on_message(lambda _:None))
        await _Server().install(page)
        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(page_url)
        assert await page.evaluate('document.documentElement.dataset.theme')=='classic'
        await open_settings(page)
        await page.get_by_label('Appearance').select_option('graphite')
        assert await page.evaluate('document.documentElement.dataset.theme')=='graphite'
        assert not errors
        await b.close()


@pytest.mark.asyncio
async def test_queue_owner_identity_and_text_contrast_in_both_themes(page_url):
    async with async_api.async_playwright() as p:
        b,page,world,routes,voice=await queue_open(p,page_url,viewport=(1100,700),reduced=True)
        await _queue_tab(page)
        for name in ('classic','graphite'):
            await page.evaluate("v=>window.dispatchEvent(new StorageEvent('storage',{key:'jarvis.theme',newValue:v}))",name)
            cue=page.locator('.cue-your-turn').first
            style=await cue.evaluate(CONTRAST)
            assert style['ratio']>=4.5,style
            assert style['background']=='rgba(14, 165, 233, 0.85)'
        assert not [c for c in routes.calls if c[0]!='GET']
        await b.close()


@pytest.mark.asyncio
async def test_dashboard_uses_saved_theme_and_cross_tab_changes():
    async with dashboard(quiet_machine()) as page:
        await page.evaluate("localStorage.setItem('jarvis.theme','graphite')")
        await page.reload()
        await page.wait_for_selector('.brand-mark')
        assert await page.evaluate('document.documentElement.dataset.theme')=='graphite'
        assert await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ground').trim()")=='#101114'
        before=await page.locator('.shell-head').bounding_box()
        await page.evaluate("window.dispatchEvent(new StorageEvent('storage',{key:'jarvis.theme',newValue:'classic'}))")
        assert await page.locator('.shell-head').bounding_box()==before
        assert await page.evaluate('document.documentElement.dataset.theme')=='classic'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['failed', 'blocked', 'cancelled'])
async def test_workflow_state_badges_remain_readable_in_both_themes(page_url, state):
    from tests.test_voice_page_workflows import _Server as WorkflowServer
    async with async_api.async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=BROWSER_ARGS)
        page=await b.new_page()
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket('**/ws/voice',_Voice())
        await page.route_web_socket('**/ws/sessions',lambda ws:None)
        server=WorkflowServer(); server.state=state
        await page.route('**/api/workflows**',server)
        await page.goto(page_url)
        await page.click('.right-tabs [role=tab][title=Workflows]')
        await page.locator('.workflow-row').click()
        badge=page.locator('.workflow-detail-state')
        for name in ('classic','graphite'):
            await page.evaluate("v=>window.dispatchEvent(new StorageEvent('storage',{key:'jarvis.theme',newValue:v}))",name)
            style=await badge.evaluate(CONTRAST)
            assert style['ratio']>=4.5,style
        await b.close()
