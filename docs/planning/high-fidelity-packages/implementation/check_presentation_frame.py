"""Additional actual-app viewport/inspection evidence through the shared checker.

Run from the build worktree with its Python environment. No live package delivery.
"""
from __future__ import annotations
import asyncio
import json
import shutil
import tempfile
import time
import hashlib
import yaml
import zipfile
from pathlib import Path
from playwright.async_api import async_playwright
from jarvis import agent_keys, decision_check as check, decision_package, make_package, package_signature

OUT = Path(__file__).parent / 'review-acceptance/frame'
SESSION = '01a164df-34ae-7760-bec0-88c187b9cf41'

async def inspect_app(server, made, package_id, result):
    story=json.loads((server.package_folder(package_id)/'story.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=check.BROWSER_ARGS)
        try:
            for touch in (False,True):
                d=check.Deck(browser,server,made.deck,story,package_id,OUT,result)
                try:
                    assert await d.open((1440,900),(809,589),has_touch=touch)
                    view=d.page.locator('#package-view')
                    geometry=await view.locator('.package-border').bounding_box()
                    expected=48 if touch else 36
                    assert abs(geometry['height']-expected)<.1,(touch,geometry)
                    sizes=await view.locator('.package-icon').evaluate_all('els=>els.map(e=>[e.clientWidth,e.clientHeight])')
                    assert all(w==(44 if touch else 32) and h==(44 if touch else 32) for w,h in sizes),sizes
                    if touch:
                        await view.screenshot(path=str(OUT/'coarse-pointer.png'))
                        result.passed.append('Coarse pointer: 48px border and 44px icon targets.')
                        before=d.answers()
                        await view.locator('.package-done').click()
                        await view.wait_for(state='detached')
                        assert d.answers()==before=={}
                        result.passed.append('Done signs off the unanswered street decision without manufacturing an answer.')
                        continue
                    before=d.answers()
                    await view.locator('.takeover-enter').click()
                    await d.page.locator('#takeover-bar').wait_for()
                    await view.screenshot(path=str(OUT/'takeover.png'))
                    for width in (600,500,420):
                        await check._fit_frame(d.page,d.iframe,(width-32,602))
                        await asyncio.sleep(.15)
                        await view.screenshot(path=str(OUT/f'frame-{width}.png'))
                        bounds=await view.locator('.package-border').evaluate('e=>({width:e.clientWidth,scroll:e.scrollWidth})')
                        assert bounds['scroll']<=bounds['width'],bounds
                    assert await d.inner.evaluate("document.documentElement.classList.contains('presentation-linear')")
                    await view.locator('[data-package-menu=more]').click()
                    await view.locator('[data-presentation-action=inspect]').click()
                    overlay=d.inner.locator('.presentation-inspector')
                    await overlay.wait_for()
                    client=next(frame['clientId'] for _,frame in d.frames if frame.get('type')=='ui_welcome')
                    async def inspect_by_brain():
                        ok,said=await d.tool('ui_navigate',command='package.inspect',args={},client=client)
                        assert ok and 'status: completed' in said,said
                    for _ in range(2):await inspect_by_brain()
                    assert await overlay.count()==1
                    assert await d.inner.locator('#stage').evaluate('e=>e.inert')
                    labels=await overlay.locator('button').all_text_contents()
                    expected_names=await d.inner.evaluate("""()=>{
                      const model=window.PRESENTATION;
                      const slide=model.document.slides.find(s=>s.id===document.querySelector('.presentation-slide.current').dataset.sourceSlide);
                      const objects=new Map(slide.objects.map(o=>[o.id,o]));
                      const names=new Map(model.targets.map(t=>[t.member,t.accessible_name]));
                      const ids=[slide.id];
                      for(const id of slide.readingOrder){const o=objects.get(id);ids.push(id);for(const p of o.paragraphs||[]){ids.push(p.id,...p.runs.map(r=>r.id));}for(const m of o.members||o.bars||[])ids.push(m.id);}
                      return ids.map(id=>names.get(id));
                    }""")
                    assert labels[1:]==expected_names
                    await overlay.locator('button').nth(1).click()
                    assert 'Source:' in await overlay.locator('[role=status]').inner_text()
                    current=await d.current_slide()
                    for key in ('PageDown','PageUp','ArrowRight','ArrowLeft','Home','End'):
                        await d.page.keyboard.press(key)
                        assert await d.current_slide()==current,key
                        assert await overlay.is_visible()
                    await d.page.keyboard.press('Escape')
                    await overlay.wait_for(state='detached')
                    await d.page.wait_for_function("document.activeElement?.dataset.packageMenu==='more'")
                    assert d.answers()==before
                    result.passed.append('420px frame: reversed source objects follow readingOrder in Details; modal navigation keys keep the slide fixed; Escape restores focus and leaves answers unchanged.')
                    # Reopen through the complete brain command path after Escape.
                    for _ in range(2):await inspect_by_brain()
                    await overlay.wait_for()
                    assert await overlay.count()==1
                    assert await d.inner.locator('#stage').evaluate('e=>e.inert')
                    for key in ('PageDown','PageUp','ArrowRight','ArrowLeft','Home','End'):
                        await d.page.keyboard.press(key)
                        assert await d.current_slide()==current,key
                    await overlay.locator('button').first.click()
                    await overlay.wait_for(state='detached')
                    assert not await d.inner.locator('#stage').evaluate('e=>e.inert')
                    await d.page.wait_for_function("document.activeElement?.dataset.packageMenu==='more'")
                    assert d.answers()==before
                    await d.inner.locator('#go-1').focus()
                    await d.page.keyboard.press('PageDown')
                    assert await d.current_slide()!=current
                    await d.inner.locator('#go-1').evaluate('e=>e.click()')
                    result.passed.append('Repeated brain ui_navigate package.inspect requests keep one inspector; Escape and Return close it, reopening remains isolated, focus returns, and background navigation resumes only after close.')
                    await d.page.set_viewport_size({'width':1440,'height':900})
                    await view.locator('[data-package-menu=more]').click()
                    await view.locator('[data-presentation-action=document]').click()
                    await d.inner.evaluate("()=>{const d=window.PRESENTATION.document;document.documentElement.style.setProperty('--body',d.theme.bodyPx*2+'px');document.documentElement.style.setProperty('--heading',d.theme.headingPx*2+'px');}")
                    await view.screenshot(path=str(OUT/'text-200-percent.png'))
                    assert await d.inner.evaluate("document.querySelector('.presentation-slide.current').scrollWidth<=document.querySelector('.presentation-slide.current').clientWidth")
                    target=await d.inner.evaluate("()=>document.querySelector('.presentation-slide.current h2[data-member]').id")
                    message={'jarvis':'package','type':'story.cue','beat':'range-check','targets':['#'+target]}
                    await d.page.evaluate("m=>document.querySelector('#package-view iframe').contentWindow.postMessage(m,'*')",message)
                    await d.inner.locator('.jarvis-story-ring').first.wait_for()
                    for width in (420,1440):
                        await check._fit_frame(d.page,d.iframe,(width-32,602))
                        await asyncio.sleep(.1)
                        actual=await d.inner.evaluate("id=>{const e=document.getElementById(id);return {rects:window.JarvisPresentation.rectangles(e.dataset.member).length,rings:[...document.querySelectorAll('.jarvis-story-ring')].filter(e=>e.style.display!=='none').length}}",target)
                        assert actual['rects']==actual['rings'],actual
                    await d.page.evaluate("()=>document.querySelector('#package-view iframe').contentWindow.postMessage({jarvis:'package',type:'story.clear'},'*')")
                    await d.inner.locator('.jarvis-story-ring').first.wait_for(state='detached')
                    result.passed.append('200% text: document view stays readable; all text-range rings follow reflow and clear.')
                    since=time.monotonic()
                    await view.locator('[data-story-action=tell]').click()
                    assert await d.story_frame(since,lambda f:f.get('state')=='telling',check.STORY_SEC)
                    await view.locator('.workflow-close').click()
                    await view.wait_for(state='detached')
                    assert await d.story_frame(since,lambda f:f.get('state')=='stopped',check.STOP_WITHIN_SEC)
                    await asyncio.sleep(.2)
                    heard=len(d.cued_audio(since))
                    await asyncio.sleep(1)
                    assert len(d.cued_audio(since))==heard and d.answers()==before
                    result.passed.append('Close withdraws narration through the real HTTP route and leaves the unanswered decision unchanged.')
                finally:await d.close()
        finally:await browser.close()

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    source=OUT/'fixture-source'
    shutil.copytree(Path('examples/presentations/street'),source,dirs_exist_ok=True)
    model=yaml.safe_load((source/'presentation.yaml').read_text())
    for slide in model['slides']:
        if slide['type']=='content':slide['objects'].reverse()
    (source/'presentation.yaml').write_text(yaml.safe_dump(model,sort_keys=False))
    made=make_package.make_presentation(source/'presentation.yaml',Path(tempfile.mkdtemp(prefix='presentation-frame-build-')))
    copies={}
    with zipfile.ZipFile(made.archive) as archive:
        for name in ('presentation.js','presentation.css'):
            member=next(n for n in archive.namelist() if n.endswith('/'+name) or n==name)
            copies[name]={'source':hashlib.sha256((Path('jarvis/presentation_assets')/name).read_bytes()).hexdigest(),'compiled':hashlib.sha256(archive.read(member)).hexdigest()}
    assert all(h['source']==h['compiled'] for h in copies.values()),copies
    signer=agent_keys.signer_for(SESSION)
    server=check.Isolated(Path(tempfile.mkdtemp(prefix='presentation-frame-')), 'en', check.built_page(),before=lambda data:check.seed(data,signer[1],SESSION))
    result=check.Result()
    try:
        signed=OUT/'checked.zip';package_signature.sign(made.archive,signed,signer[0],signer[1],SESSION)
        delivered=decision_package.deliver(signed,server.base,decision_package.says_of(made.deck['title']),signer=signer)
        asyncio.run(check._within_the_wall_clock(inspect_app(server,made,delivered.package['id'],result),result))
    except Exception as error:result.failures.append(f'{type(error).__name__}: {error}')
    finally:
        server.stop()
        if result.ok:shutil.rmtree(server.folder)
    (OUT/'result.json').write_text(json.dumps({'ok':result.ok,'passed':result.passed,'failures':result.failures,'server_log':str(server.log),'source_sha256':hashlib.sha256((source/'presentation.yaml').read_bytes()).hexdigest(),'renderer_hashes':copies,'story_helper_sha256':hashlib.sha256(Path('jarvis/package_story.js').read_bytes()).hexdigest()},indent=2)+'\n')
    print(json.dumps({'ok':result.ok,'failures':result.failures}))

if __name__=='__main__':main()
