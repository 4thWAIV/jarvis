/* presentation/1 composition only. The shared deck retains navigation and every answer writer. */
(function () {
  "use strict";
  const model = window.PRESENTATION;
  const doc = model.document;
  const byId = items => new Map(items.map(item => [item.id, item]));
  const slides = byId(doc.slides), layouts = byId(doc.layouts), assets = byId(doc.assets);
  const targets = new Map(model.targets.map(target => [target.member, target]));
  const references = byId(doc.references);
  references.set(doc.id,{description:"Authored presentation source",locator:"source.yaml",revision:model.source_sha256});
  const node = (tag, text) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; return e; };
  const svgNode = (tag, attrs) => { const e=document.createElementNS("http://www.w3.org/2000/svg",tag); Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v))); return e; };
  function identify(e, id) { const target=targets.get(id); e.id=target.dom_id; e.dataset.member=id; e.tabIndex=-1; return e; }
  function textObject(object, parent) {
    let list=null, listRole=null;
    object.paragraphs.forEach(paragraph => {
      const isList=["bullet","numbered"].includes(paragraph.role);
      if (isList && listRole!==paragraph.role) { list=node(paragraph.role==="bullet"?"ul":"ol"); parent.append(list); }
      if (!isList) list=null;
      listRole=paragraph.role;
      const element=identify(node(isList?"li":paragraph.role==="heading"?"h2":"p"),paragraph.id);
      paragraph.runs.forEach(run=>element.append(identify(node(run.emphasis==="strong"?"strong":run.emphasis==="italic"?"em":"span",run.text),run.id)));
      (list||parent).append(element);
    });
  }
  function vectorObject(object,parent) {
    const v=object.viewBox;
    const svg=svgNode("svg",{viewBox:[v.x,v.y,v.width,v.height].join(" "),role:"img","aria-label":object.alt,preserveAspectRatio:"xMidYMid meet"});
    svg.style.setProperty("--vector-aspect",String(v.width/v.height));
    object.members.forEach(member=>{
      let geometry;
      if(member.type==="rect")geometry={...member.rect,rx:member.radius};
      else if(member.type==="ellipse")geometry={cx:member.cx,cy:member.cy,rx:member.rx,ry:member.ry};
      else geometry={d:member.d};
      const shape=identify(svgNode(member.type,{...geometry,fill:member.fill,stroke:member.stroke,"stroke-width":member.strokePx,"stroke-linejoin":"round","stroke-linecap":"round"}),member.id);
      shape.append(svgNode("title",{})); shape.firstChild.textContent=member.alt;
      svg.append(shape);
    });
    parent.append(svg);
  }
  function barObject(object,parent) {
    parent.classList.add("presentation-bars"); parent.setAttribute("role","list"); parent.setAttribute("aria-label",object.alt);
    object.bars.forEach(bar=>{
      const row=identify(node("div"),bar.id); row.className="presentation-bar-line"; row.setAttribute("role","listitem");
      const label=node("span",bar.label); label.append(node("span",`${bar.value} ${object.units}`)); label.lastChild.className="presentation-bar-value";
      const track=node("div"),fill=node("span");track.className="presentation-bar-track";fill.className="presentation-bar-fill";
      fill.style.width=(bar.value/object.domain[1]*100)+"%";track.setAttribute("aria-hidden","true");track.append(fill);row.append(label,track);parent.append(row);
    });
    const axis=node("div");axis.className="presentation-axis";axis.append(node("span",`0 ${object.units}`),node("span",`${object.domain[1]} ${object.units}`));parent.append(axis);
  }
  const renderers={text:textObject,vector:vectorObject,bar:barObject,image:(object,parent)=>{
    const asset=assets.get(object.asset),image=node("img");image.src=asset.path;image.alt=object.alt;image.dataset.asset=asset.id;parent.append(image);
  }};
  function render(id) {
    const slide=slides.get(id.replace(/^presentation-slide-/,"")); if(!slide||slide.type!=="content")return null;
    const section=node("section");section.id=id;section.className="slide content presentation-slide";section.dataset.sourceSlide=slide.id;section.setAttribute("aria-label",slide.title);
    const regions=byId(layouts.get(slide.layout).regions),objects=byId(slide.objects);
    slide.readingOrder.forEach(id=>{
      const object=objects.get(id),r=regions.get(object.region).rect,region=identify(node("div"),id);
      region.className="presentation-region";region.dataset.object=id;
      Object.assign(region.style,{left:r.x+"%",top:r.y+"%",width:r.width+"%",height:r.height+"%",padding:doc.theme.gapPx+"px"});
      renderers[object.type](object,region);section.append(region);
    });
    return section;
  }
  function fit(stage) {
    Object.assign(stage.style,{transform:"none",left:"0px",top:"0px",width:"100%",height:"100%"});
    document.documentElement.classList.toggle("presentation-linear",window.innerWidth<500 || window.__presentationLinear===true);
  }
  // One live geometry implementation for inspection and narrated focus.
  // Text nodes avoid a block's single bounding box and duplicate inline boxes.
  function rectangles(id) {
    const target=targets.get(id);
    const element=document.getElementById(target?.dom_id);
    if(!element || getComputedStyle(element).visibility!=="visible")return [];
    let rects=[];
    if(["text","paragraph","run"].includes(target.kind)) {
      const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
      let text;
      while(text=walker.nextNode()) {
        if(!text.textContent.trim())continue;
        const range=document.createRange();range.selectNodeContents(text);
        rects.push(...range.getClientRects());
      }
    } else rects=[...element.getClientRects()];
    return rects.filter(r=>r.width>0&&r.height>0).map(r=>r.toJSON());
  }
  // The semantic traversal is separate from ordinary Tab navigation.
  let inspector=null;
  function inspect() {
    // UI and brain requests share this boundary: one active inspector owns
    // the stage's inert state until it closes, including repeated requests.
    if(inspector?.isConnected)return;
    const opener=document.activeElement;
    const stage=document.getElementById("stage"), wasInert=stage.inert;
    const overlay=node("section");overlay.className="presentation-inspector";overlay.setAttribute("role","dialog");overlay.setAttribute("aria-modal","true");overlay.setAttribute("aria-label","Presentation source and objects");
    overlay.append(node("h2",doc.title),node("p",doc.purpose),node("p",`Source revision: ${model.source_sha256}`));
    const current=document.querySelector(".slide.current");
    const members=model.targets.filter(t=>t.slide===current?.dataset.sourceSlide);
    const close=node("button","Return to presentation");close.onclick=()=>{if(inspector!==overlay)return;overlay.remove();inspector=null;stage.inert=wasInert;opener?.focus();parent.postMessage({jarvis:"package",type:"presentation.inspection.closed"},"*");};overlay.append(close);
    members.forEach(target=>{const button=node("button",target.accessible_name);button.onclick=()=>{const reference=references.get(target.reference);const frozen=model.frozen_references?.[target.reference];description.textContent=`${target.accessible_name}. ${reference.description} Source: ${reference.locator}. Revision: ${reference.revision}. Member: ${target.source_member||target.member}.${frozen?` Frozen evidence: ${frozen.path}. SHA-256: ${frozen.sha256}.`:""}`;};overlay.append(button);});
    const description=node("p");description.setAttribute("role","status");overlay.append(description);
    overlay.onkeydown=e=>{if(e.key==="Escape")close.click();if(e.key==="Tab"){const buttons=[...overlay.querySelectorAll("button")];if(e.shiftKey&&document.activeElement===buttons[0]){e.preventDefault();buttons.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===buttons.at(-1)){e.preventDefault();buttons[0].focus();}}};
    inspector=overlay;stage.inert=true;document.body.append(overlay);close.focus();
  }
  const style=document.documentElement.style;
  Object.entries({"--ground":doc.theme.background,"--text":doc.theme.foreground,"--accent":doc.theme.accent,"--muted":doc.theme.muted,"--body":doc.theme.bodyPx+"px","--heading":doc.theme.headingPx+"px"}).forEach(([k,v])=>style.setProperty(k,v));
  document.documentElement.lang=doc.language;
  function decorateDecision(section, decision) {
    const sourceSlide=doc.slides.find(s=>s.type==="decision"&&s.decision===decision.id);
    if(sourceSlide)section.dataset.sourceSlide=sourceSlide.id;
  }
  window.JarvisPresentation={render,fit,rectangles,inspect,decorateDecision};
  window.addEventListener("message",event=>{
    const m=event.data;
    if(event.source!==parent||!m||m.jarvis!=="package")return;
    if(m.type==="presentation.probe")parent.postMessage({jarvis:"package",type:"presentation.ready",version:"presentation/1"},"*");
    if(m.type==="presentation.select"&&Number.isInteger(m.slide)){
      const selected=model.story.slides[m.slide-1];
      const action=selected?.beats?.[0]?.actions?.find(a=>a.click);
      if(action)document.querySelector(action.click)?.click();
    }
    if(m.type==="presentation.inspect")inspect();
    if(m.type==="presentation.document"){
      window.__presentationLinear=m.enabled===true;
      fit(document.getElementById("stage"));
    }
  });
  parent.postMessage({jarvis:"package",type:"presentation.ready",version:"presentation/1"},"*");
})();
