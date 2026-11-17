/* presentation/1 composition only. The shared deck retains navigation and every answer writer. */
(function () {
  "use strict";
  const model = window.PRESENTATION;
  const doc = model.document;
  const byId = items => new Map(items.map(item => [item.id, item]));
  const slides = byId(doc.slides), assets = byId(doc.assets);
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
  function tableObject(object,parent) {
    const table=node("table");table.className="table";table.setAttribute("aria-label",object.alt);
    if(object.head){const row=node("tr");object.head.forEach(cell=>row.append(node("th",cell)));table.append(node("thead"));table.firstChild.append(row);}
    const body=node("tbody");
    // Each cell carries its column's heading: a narrow frame draws a row as a card, "Heading: cell".
    object.rows.forEach(row=>{const line=identify(node("tr"),row.id);row.cells.forEach((cell,i)=>{const c=node(i===0&&object.rowHeads?"th":"td",cell);if(object.head&&c.tagName==="TD")c.dataset.head=object.head[i];if(object.columns)identify(c,`${row.id}-${object.columns[i]}`);line.append(c);});body.append(line);});
    table.append(body);parent.append(table);
  }
  // JARVIS's own components (layout_spec "components"): words in, drawn as presentation_proof measures them.
  const block=(tag,text,className)=>{const e=node(tag,text);if(className)e.className=className;return e;};
  function callout(object,parent){
    const box=block("div",undefined,"presentation-callout");
    if(object.label)box.append(block("b",object.label));
    box.append(block("span",object.says));parent.append(box);
  }
  function figures(object,parent){
    const row=block("div",undefined,"presentation-figures");row.style.setProperty("--count",String(object.items.length));
    object.items.forEach(item=>{const figure=identify(block("div",undefined,"presentation-figure"),item.id);
      figure.append(block("b",item.value,"presentation-figure-value"),block("span",item.label));row.append(figure);});
    parent.append(row);
  }
  function steps(object,parent){
    const list=block("ol",undefined,"presentation-steps");
    object.items.forEach((item,n)=>{const step=identify(block("li",undefined,"presentation-step"),item.id);
      const marker=block("span",String(n+1),"presentation-step-marker");marker.setAttribute("aria-hidden","true");
      const text=block("div",undefined,"presentation-step-text");text.append(block("b",item.title));
      if(item.says)text.append(block("span",item.says));step.append(marker,text);list.append(step);});
    parent.append(list);
  }
  // A chart from data: presentation_data worked out every tick, label and
  // point as a share of the plot; this only places them, by percentage.
  const pct=v=>`${(v*100).toFixed(4)}%`;
  function chart(object,parent){
    const drawn=model.charts[object.id],box=block("div",undefined,"presentation-chart");
    box.setAttribute("role","figure");box.setAttribute("aria-label",object.alt);
    const legend=block("div",undefined,"presentation-chart-legend");
    drawn.series.forEach((series,s)=>{const item=block("span");const swatch=block("span",undefined,"presentation-chart-swatch");
      swatch.style.background=`var(--series-${s+1})`;item.append(swatch,document.createTextNode(series.label));legend.append(item);});
    const y=block("div",undefined,"presentation-chart-y"),ticks=block("div",undefined,"presentation-chart-ticks");
    // Every label, at no height, so the column is as wide as the widest.
    const sizer=block("div",undefined,"presentation-chart-sizer");drawn.y_ticks.forEach(t=>sizer.append(block("span",t.label)));
    y.append(sizer,ticks);
    const plot=block("div",undefined,"presentation-chart-plot");
    drawn.y_ticks.forEach(t=>{const label=block("span",t.label);label.style.bottom=pct(t.at);ticks.append(label);
      const rule=block("span",undefined,"presentation-chart-rule");rule.style.bottom=pct(t.at);plot.append(rule);});
    const n=drawn.categories?drawn.categories.length:0,count=drawn.series.length;
    drawn.series.forEach((series,s)=>{
      const along=p=>drawn.categories?(p.x+0.5)/n:p.x;
      if(drawn.kind==="line"){
        const svg=svgNode("svg",{viewBox:"0 0 100 100",preserveAspectRatio:"none","aria-hidden":"true"});svg.classList.add("presentation-chart-lines");
        svg.append(svgNode("polyline",{points:series.points.map(p=>`${along(p)*100},${(1-p.y)*100}`).join(" "),fill:"none",stroke:`var(--series-${s+1})`,"stroke-width":2,"vector-effect":"non-scaling-stroke"}));plot.append(svg);
      }
      series.points.forEach(p=>{
        const mark=identify(block("span"),p.id);mark.setAttribute("role","img");mark.setAttribute("aria-label",p.name);
        mark.style.background=`var(--series-${s+1})`;
        if(drawn.kind==="bar"){
          mark.className="presentation-chart-bar";
          mark.style.left=pct((p.x+(1-drawn.band)/2+s*drawn.band/count)/n);mark.style.width=pct(drawn.band/count/n);
          mark.style.bottom=pct(Math.min(drawn.zero,p.y));mark.style.height=pct(Math.abs(p.y-drawn.zero));
        } else {
          mark.className="presentation-chart-dot";mark.style.left=pct(along(p));mark.style.bottom=pct(p.y);
        }
        plot.append(mark);
      });
    });
    const x=block("div",undefined,"presentation-chart-x "+(drawn.categories?"categories":"ticks"));
    if(drawn.categories){x.style.setProperty("--count",String(n));drawn.categories.forEach(c=>x.append(block("span",c)));}
    else drawn.x_ticks.forEach(t=>{const label=block("span",t.label);label.style.left=pct(t.at);x.append(label);});
    box.append(legend,y,plot,x);parent.append(box);
  }
  const renderers={chart,callout,figures,steps,text:textObject,vector:vectorObject,bar:barObject,table:tableObject,image:(object,parent)=>{
    const asset=assets.get(object.asset),image=node("img");image.src=asset.path;image.alt=object.alt;image.dataset.asset=asset.id;
    image.style.setProperty("--natural-width",String(asset.width));image.style.setProperty("--natural-height",String(asset.height));parent.append(image);
  }};
  function render(id) {
    const slide=slides.get(id.replace(/^presentation-slide-/,"")); if(!slide||slide.type!=="content")return null;
    const section=node("section");section.id=id;section.className="slide content presentation-slide";section.dataset.sourceSlide=slide.id;section.setAttribute("aria-label",slide.title);
    // JARVIS's named layout (layout_spec): the title in its place, each
    // object in its slot, the boxes from the generated presentation.css.
    const canvas=node("div");canvas.className="presentation-canvas";section.append(canvas);
    const title=identify(node("h2",slide.title),slide.id+"-title");title.className="presentation-title";canvas.append(title);
    const objects=byId(slide.objects),columns=slide.objects.filter(o=>["first","second","third","fourth"].includes(o.slot)).length;
    slide.readingOrder.forEach(id=>{
      const object=objects.get(id),slot=identify(node("div"),id);
      slot.className="presentation-slot";slot.dataset.object=id;slot.dataset.kind=object.type;slot.dataset.layout=slide.layout;slot.dataset.slot=object.slot;
      if(columns)slot.dataset.columns=String(columns);
      renderers[object.type](object,slot);canvas.append(slot);
    });
    return section;
  }
  function fit(stage) {
    Object.assign(stage.style,{transform:"none",left:"0px",top:"0px",width:"100%",height:"100%"});
    // Below the spec's narrow size every content slide is one column in reading order (layout_spec narrow).
    const css=getComputedStyle(document.documentElement);
    const narrow=window.innerWidth<Number(css.getPropertyValue("--narrow-width")) || window.innerHeight<Number(css.getPropertyValue("--narrow-height"));
    document.documentElement.classList.toggle("presentation-narrow",narrow);
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
  if(doc.language)document.documentElement.lang=doc.language;
  function decorateDecision(section, decision) {
    const sourceSlide=doc.slides.find(s=>s.type==="decision"&&s.decision===decision.id);
    if(sourceSlide)section.dataset.sourceSlide=sourceSlide.id;
  }
  window.JarvisPresentation={render,fit,rectangles,inspect,decorateDecision};
  window.addEventListener("message",event=>{
    const m=event.data;
    if(event.source!==parent||!m||m.jarvis!=="package")return;
    if(m.type==="presentation.probe")parent.postMessage({jarvis:"package",type:"presentation.ready",version:"presentation/1"},"*");
    if(m.type==="presentation.inspect")inspect();
    if(m.type==="presentation.document"){
      window.__presentationLinear=m.enabled===true;
      fit(document.getElementById("stage"));
    }
  });
  parent.postMessage({jarvis:"package",type:"presentation.ready",version:"presentation/1"},"*");
})();
