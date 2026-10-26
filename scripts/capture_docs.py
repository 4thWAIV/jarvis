#!/usr/bin/env python3
"""Capture the real React UI with fictional data and no live backend.

Run from the checkout: .venv/bin/python scripts/capture_docs.py
Uses the existing browser-test fixtures, Playwright and frontend dependencies.
Builds into a temporary directory; writes only docs/images/workspace-*.png.
No physical microphone, model, external service or live user data is used.
"""
import asyncio
import functools
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright
from tests.test_voice_page_voice_health import record
from tests.test_voice_page_workflows import _workflow
from tests.test_voice_page_diagnostics import _week
from tests.test_voice_page_settings import _status
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION


def fixtures():
    now = time.time()
    summary, history = record(now)
    base = history['samples'][0]
    history['samples'] = [dict(base, at=now-900+i,
        backlogSec=2.1 if 285 <= i <= 300 else 0.12+(i%9)*0.015,
        rtf=1.2 if 285 <= i <= 300 else 0.18+(i%7)*0.01,
        loadAvg=16 if 285 <= i <= 300 else 4,
        notices=12 if 285 <= i <= 300 else 1,
        noticesBySource={'Input':12 if 285 <= i <= 300 else 1},
        finals=1 if i%30==0 else 0) for i in range(900)]
    summary.update(last2min=history['samples'][-120:], lastDropAt=now-600)
    history['events'][0].update(at=now-600, cause='Example: high machine load',
                               topProcesses=[{'name':'example-build', 'cpu':85.0}])
    history['events'][1]['at'] = now-599
    workflow = _workflow()
    workflow.update(name='Workspace release', project='example-project', phase='review',
                    phases_done=2, phases_total=4, tasks_done=7, tasks_total=10,
                    started_at=now-2400, last_event_at=now-30,
                    last_summary='Interface review is ready for your decision.')
    workflow['items'] = [dict(slug=slug,kind='phase',phase=None,title=title,state=state,
                              done=None,total=None) for slug,title,state in [
        ('design','Design','done'),('implementation','Implementation','done'),
        ('review','Review','running'),('release','Release','pending')]]
    workflow['open'] = [dict(kind='decision',key='review',summary='Ready for a trial release?',
                            detail={'options':['Run the trial','Continue review'],
                                    'recommendation':'Run the trial'},opened_at=now-120)]
    workflow['open_decisions']=['review']
    week=_week(8.0,42.0)
    week.update(week_start=now-3.5*86400,resets_at=now+3.5*86400,observed_at=now)
    week['points']=[{'at':now-3.5*86400+i*43200,'used':i*6} for i in range(8)]
    conversation=[]
    for i,(kind,text) in enumerate([
        ('typed','What needs my attention on the workspace release?'),
        ('jarvis','The interface is ready for review. The workflow has one decision waiting.'),
        ('typed','Show me the progress and keep the release paused until I decide.'),
        ('jarvis','Design and implementation are complete. Review is active, and release is still pending.')]):
        conversation.append(dict(id=i+1,at=now-300+i*30,kind=kind,text=text,
            agent_session_id=None,agent_name=None,message_id=None,session_message_id=None,
            capture_id=None,detail={},message=None))
    return summary,history,workflow,week,conversation


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


async def capture(url):
    summary,history,workflow,week,conversation=fixtures()
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=BROWSER_ARGS)
        try:
            page=await browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            # Chromium's synthetic microphone and the test recognition socket.
            await page.add_init_script(FAKE_TRANSCRIPTION)
            async def api(route):
                path=urlsplit(route.request.url).path
                if path=='/api/settings/status': body=_status()
                elif path=='/api/settings/preferences': body={'user_name':'Example','honorific':'sir'}
                elif path=='/api/brains':
                    body={'brains':[dict(name='anthropic',title='Claude',added=True,active=True,
                                         default=True,model='sonnet')]}
                elif path=='/api/usage/week': body=week
                elif path=='/api/agents':
                    body={'agents':[dict(session_id=f's{i}',name=name,active=True,state=state,
                                         needs=1 if state=='needs_you' else 0,provider=provider)
                        for i,(name,state,provider) in enumerate([
                            ('Interface','working','anthropic'),('Review','needs_you','openai'),
                            ('Diagnostics','working','anthropic'),('Documentation','idle','openai')])]}
                elif path=='/api/conversation': body={'items':conversation,'earlier':None}
                elif path=='/api/voice/diagnostics': body=summary
                elif path=='/api/voice/diagnostics/samples': body=history
                elif path=='/api/workflows': body={'workflows':[workflow]}
                elif path.startswith('/api/workflows/'):
                    body={'events':[]} if path.endswith('/events') else {'workflow':workflow}
                elif path=='/api/captures': body={'captures':[]}
                else: body={}
                await route.fulfill(json=body)
            await page.route('**/api/**',api)
            async def voice(ws):
                ws.on_message(lambda _:None)
                ws.send(json.dumps({'type':'status','state':'idle'}))
            await page.route_web_socket('**/ws/voice',voice)
            await page.route_web_socket('**/ws/sessions',lambda ws:ws.on_message(lambda _:None))
            await page.goto(url,wait_until='networkidle')
            await page.wait_for_selector('.conversation-history .history-turn')
            await page.wait_for_selector('.agents-list li')
            await page.evaluate('document.fonts.ready')
            await page.wait_for_timeout(1200)
            output=ROOT/'docs/images'
            await page.screenshot(path=str(output/'workspace-conversation.png'))
            await page.click('.right-tabs [role=tab][title=Workflows]')
            await page.click('.workflow-row')
            await page.wait_for_selector('#workflow-detail .workflow-detail-name')
            await page.wait_for_timeout(700)
            await page.screenshot(path=str(output/'workspace-workflow.png'))
            await page.click('.right-tabs [role=tab][title=Diagnostics]')
            await page.click('.voice-summary-open')
            await page.wait_for_selector('#voice-diagnostics')
            await page.wait_for_timeout(700)
            await page.screenshot(path=str(output/'workspace-diagnostics.png'))
            if errors: raise RuntimeError('\n'.join(errors))
            print('Captured Conversation, Workflow and Diagnostics from the current frontend.')
        finally:
            await browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='jarvis-docs-') as tmp:
        subprocess.run(['npx','vite','build','--outDir',tmp,'--emptyOutDir','--logLevel','error'],
                       cwd=ROOT/'frontend',check=True)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),
            functools.partial(QuietHandler,directory=tmp))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try: asyncio.run(capture(f'http://127.0.0.1:{server.server_address[1]}/'))
        finally: server.shutdown();server.server_close()

if __name__=='__main__': main()
