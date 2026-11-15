"""Measured creation checks using the browser that renders presentation/1."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from jarvis.presentation_schema import ContentSlide, VectorObject
from jarvis.presentation_source import DeckCompileError, Source

PROFILES = (("app-1280", 661.625, 602), ("app-1440", 808.8125, 702),
            ("fixture-662", 630, 453), ("fixture-809", 777, 553),
            ("takeover", 1248, 764), ("narrow", 388, 602))

# Only measured visible content counts. Text ranges catch overflow even when
# their enclosing region has the authored size. SVG path bounds come from SVG.
_MEASURE = """() => {
  const slide=document.querySelector('.slide.current'), frame=slide.getBoundingClientRect();
  const issues=[];
  const linear=document.documentElement.classList.contains('presentation-linear');
  const inside=(a,b)=>a.left>=b.left-1&&a.top>=b.top-1&&a.right<=b.right+1&&a.bottom<=b.bottom+1;
  slide.querySelectorAll('.presentation-region').forEach(region=>{
    const b=region.getBoundingClientRect(), id=region.dataset.object;
    if(!linear&&!inside(b,frame))issues.push({member:id,message:'Region lies outside the content rectangle.'});
    if(region.scrollWidth>region.clientWidth+1||(!linear&&region.scrollHeight>region.clientHeight+1))issues.push({member:id,message:`Content bounds ${region.scrollWidth}×${region.scrollHeight} exceed region ${region.clientWidth}×${region.clientHeight}.`});
    const walk=document.createTreeWalker(region,NodeFilter.SHOW_TEXT);
    let text;
    while(text=walk.nextNode()){
      if(!text.textContent.trim()||text.parentElement.closest('svg'))continue;
      const range=document.createRange();range.selectNodeContents(text);
      const style=getComputedStyle(text.parentElement);
      if(parseFloat(style.fontSize)<window.PRESENTATION.document.theme.minimumPx)issues.push({member:id,message:'Text is below the declared effective floor.'});
      for(const r of range.getClientRects())if((!linear&&!inside(r,b))||(linear&&(r.left<b.left-1||r.right>b.right+1)))issues.push({member:id,message:`Text bounds ${JSON.stringify(r.toJSON())} exceed region ${JSON.stringify(b.toJSON())}.`});
    }
  });
  if(slide.scrollWidth>slide.clientWidth+1||(!linear&&slide.scrollHeight>slide.clientHeight+1))issues.push({member:slide.dataset.sourceSlide||null,message:'Slide content overflows the measured canvas.'});
  return {issues,linear,canvas:{width:frame.width,height:frame.height}};
}"""


def check_layout(source: Source, folder: Path, evidence: Path) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    failures, records = [], []
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        try:
            page=browser.new_page()
            page_errors=[]
            page.on("pageerror",lambda error:page_errors.append(str(error)))
            helper=Path(__file__).with_name("package_story.js").read_text()
            page.route("**/api/packages/story.js",lambda route:route.fulfill(body=helper,content_type="application/javascript"))
            page.goto((folder/"index.html").as_uri())
            page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
            page.evaluate("async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));}")
            assets=page.evaluate("async () => Promise.all(window.PRESENTATION.document.assets.map(async asset => { const i=new Image();i.src=asset.path;await i.decode().catch(()=>{});return {id:asset.id,w:i.naturalWidth,h:i.naturalHeight,complete:i.complete}; }))")
            by_id={asset.id:asset for asset in source.document.assets}
            for asset in assets:
                declared=by_id[asset["id"]]
                if not asset["complete"] or (asset["w"],asset["h"])!=(declared.width,declared.height):
                    failures.append(source.diagnostic("asset","Image decoding or intrinsic dimensions differ from source.",member=declared.id))
            for slide in source.document.slides:
                if not isinstance(slide,ContentSlide):continue
                for obj in slide.objects:
                    if not isinstance(obj,VectorObject):continue
                    for shape in obj.members:
                        if shape.type!="path":continue
                        bounds=page.locator("#presentation-main-"+shape.id).evaluate("e=>{const b=e.getBBox();return {x:b.x,y:b.y,w:b.width,h:b.height};}")
                        pad=shape.strokePx/2 if shape.stroke!="none" else 0
                        v=obj.viewBox
                        if bounds["x"]-pad<v.x or bounds["y"]-pad<v.y or bounds["x"]+bounds["w"]+pad>v.x+v.width or bounds["y"]+bounds["h"]+pad>v.y+v.height:
                            failures.append(source.diagnostic("layout","Stroked path lies outside its viewBox.",member=shape.id))
            count=page.locator("#strip .jump").count()
            for profile,width,height in PROFILES:
                page.set_viewport_size({"width":round(width),"height":height})
                page.evaluate("([w,h])=>{const stage=document.querySelector('#stage');stage.style.width=w+'px';stage.style.height=h+'px';}",[width,height])
                for index in range(count):
                    page.locator(f"#go-{index+1}").evaluate("e=>e.click()")
                    result=page.evaluate(_MEASURE)
                    records.append({"profile":profile,"slide":index+1,**result})
                    failures.extend(source.diagnostic("layout",issue["message"],member=issue["member"],profile=profile) for issue in result["issues"])
                page.locator("#go-1").evaluate("e=>e.click()")
                page.screenshot(path=str(evidence/f"{profile}.png"))
            # Text enlargement uses the explicit document view, preserving the
            # object renderer and logical order instead of scaling a bitmap.
            page.evaluate("()=>{window.__presentationLinear=true;document.documentElement.style.setProperty('--body',window.PRESENTATION.document.theme.bodyPx*2+'px');document.documentElement.style.setProperty('--heading',window.PRESENTATION.document.theme.headingPx*2+'px');window.JarvisPresentation.fit(document.querySelector('#stage'));}")
            for index in range(count):
                page.locator(f"#go-{index+1}").evaluate("e=>e.click()")
                result=page.evaluate(_MEASURE)
                records.append({"profile":"text-200-percent","slide":index+1,**result})
                failures.extend(source.diagnostic("accessibility",issue["message"],member=issue["member"],profile="text-200-percent") for issue in result["issues"])
            failures.extend(source.diagnostic("production",error) for error in page_errors)
        finally:
            browser.close()
    (evidence/"measurements.json").write_text(json.dumps({"source_sha256":source.digest,"profiles":records,"failures":[d.__dict__ for d in failures]},indent=2)+"\n")
    if failures:
        raise DeckCompileError(tuple(failures))
