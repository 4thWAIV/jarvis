"""Repeatable local renderer comparison; run after building maximum-story fixtures.

python docs/planning/high-fidelity-packages/implementation/measure_presentation.py
Measures file load, all-slide layout, cue paint and sampled Chromium JS heap.
Peak resident memory is the process-tree high-water mark reported by resource.
"""
from __future__ import annotations
import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).parent / 'maximum-story'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def measure(browser, folder):
    page = browser.new_page(viewport={'width': 809, 'height': 589})
    page.route('**/api/packages/story.js', lambda route: route.fulfill(
        body=(ROOT/'jarvis/package_story.js').read_text(), content_type='application/javascript'))
    session = page.context.new_cdp_session(page)
    session.send('Performance.enable')
    start = time.perf_counter()
    page.goto((folder/'index.html').as_uri())
    page.wait_for_function("document.querySelector('.slide.current')")
    page.evaluate('async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame)}')
    startup_ms = (time.perf_counter()-start)*1000
    story = json.loads((folder/'story.json').read_text())
    measurements = page.evaluate('''async (story)=>{
      const result={layout_ms:[],cue_ms:[]};
      for(let i=0;i<story.slides.length;i++){
        let t=performance.now();document.querySelector(`#go-${i+1}`).click();
        document.querySelector('.slide.current').getBoundingClientRect();
        result.layout_ms.push(performance.now()-t);
        const beat=story.slides[i].beats[0];t=performance.now();
        window.postMessage({jarvis:'package',type:'story.cue',beat:`measure-${i}`,...beat},'*');
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        result.cue_ms.push(performance.now()-t);
        window.postMessage({jarvis:'package',type:'story.clear'},'*');
      }
      return result;
    }''', story)
    metrics = {v['name']:v['value'] for v in session.send('Performance.getMetrics')['metrics']}
    measurements.update(startup_ms=startup_ms, slides=len(story['slides']),
                        js_heap_used_bytes=metrics['JSHeapUsedSize'],
                        js_heap_total_bytes=metrics['JSHeapTotalSize'])
    page.close()
    return measurements

def main():
    paths = {
        'yaml': OUT.parent/'review-acceptance/maximum-yaml/compiled/maximum-presentation',
        'json': OUT.parent/'review-acceptance/maximum-json/maximum-existing',
    }
    report={'platform':platform.platform(),'cpu':subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip(),
            'python':sys.version,'node':subprocess.check_output(['node','--version'],text=True).strip(),
            'base_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'renderer_hashes':{str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'jarvis/presentation_assets/presentation.js',ROOT/'examples/packages/decision-deck/deck.js',ROOT/'jarvis/package_story.js']},
            'notes':'Three sequential trials per path on the same device/browser. File-load startup excludes server startup and compilation. Layout forces geometry; cue latency includes two animation frames. JS heap is a post-run sample, not peak. Process peak below covers this comparison run, not the server.'}
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        report['browser']=browser.version
        report['paths']={kind:{'source_sha256':digest(folder/'source.yaml' if kind=='yaml' else OUT/'source/content.json'),
                               'generated_renderer_hashes':{name:digest(folder/name) for name in ('deck.js','deck.css','presentation.js','presentation.css') if (folder/name).is_file()},
                               'runs':[measure(browser,folder) for _ in range(3)]} for kind,folder in paths.items()}
        browser.close()
    report['peak_resident_bytes']={'python':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                    'child_processes':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}
    (OUT.parent/'review-acceptance/performance.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
