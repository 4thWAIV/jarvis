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
      paragraph.runs.forEach(run=>{
        const shown=identify(node(run.emphasis==="strong"?"strong":run.emphasis==="italic"?"em":run.emphasis==="his-words"?"mark":"span",run.text),run.id);
        if(run.emphasis==="his-words")shown.className="his-words";
        element.append(shown);
      });
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
    // A table naming its columns lights each cell, `<row id>-<column id>`.
    object.rows.forEach(row=>{const line=identify(node("tr"),row.id);row.cells.forEach((cell,i)=>{const c=node(i===0&&object.rowHeads?"th":"td",cell);if(object.columns)identify(c,`${row.id}-${object.columns[i]}`);line.append(c);});body.append(line);});
    table.append(body);parent.append(table);
  }
  // JARVIS's own components (layout_spec "components"): words in, drawn in the spec's sizes.
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
  // A diagram: diagram_layout placed every node, label, group and
  // connector point as a share of the slot; this draws them there. Groups
  // under the connectors (one SVG, drawn again in px whenever its box
  // changes), nodes and words over them; every one a target.
  const placeAt=(e,box)=>{e.style.left=pct(box[0]);e.style.top=pct(box[1]);e.style.width=pct(box[2]);e.style.height=pct(box[3]);return e;};
  const SHAPES={diamond:()=>svgNode("polygon",{points:"50,0 100,50 50,100 0,50"}),
    cylinder:()=>svgNode("path",{d:"M0,12 A50,12 0 0 1 100,12 L100,88 A50,12 0 0 1 0,88 Z M0,12 A50,12 0 0 0 100,12"}),
    document:()=>svgNode("path",{d:"M0,0 H100 V88 C75,76 50,100 25,94 C12,91 5,88 0,90 Z"})};
  function diagram(object,parent){
    const drawn=model.diagrams[object.id],box=block("div",undefined,"presentation-diagram");
    box.dataset.kind=drawn.kind;box.dataset.orientation=drawn.orientation;
    box.setAttribute("role","figure");box.setAttribute("aria-label",object.alt);
    drawn.groups.forEach(g=>{
      box.append(placeAt(identify(block("div",undefined,"presentation-diagram-group"),g.id),g.box));
      const words=placeAt(block("div",g.label,"presentation-diagram-group-label"),g.label_box);words.dataset.labelOf=g.id;box.append(words);});
    const svg=svgNode("svg",{"aria-hidden":"true"});svg.classList.add("presentation-diagram-links");
    const head=`presentation-arrow-${object.id}`,defs=svgNode("defs",{});
    const marker=svgNode("marker",{id:head,viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:layoutArrow,markerHeight:layoutArrow,orient:"auto-start-reverse",markerUnits:"strokeWidth"});
    const tip=svgNode("path",{d:"M0,0 L10,5 L0,10 Z"});tip.classList.add("presentation-diagram-arrowhead");marker.append(tip);defs.append(marker);svg.append(defs);
    const paths=[];
    if(drawn.axis){const axis=svgNode("line",{});axis.classList.add("presentation-diagram-axis");svg.append(axis);paths.push([axis,[drawn.axis.from,drawn.axis.to]]);}
    drawn.links.forEach(link=>{
      const g=identify(svgNode("g",{}),link.id);g.classList.add("presentation-diagram-connector");g.dataset.style=link.style;
      g.setAttribute("role","img");g.setAttribute("aria-label",model.targets.find(t=>t.member===link.id).accessible_name);
      const path=svgNode("path",{});
      if(link.style==="arrow"||link.style==="dashed"||link.style==="both")path.setAttribute("marker-end",`url(#${head})`);
      if(link.style==="both")path.setAttribute("marker-start",`url(#${head})`);
      g.append(path);svg.append(g);paths.push([path,link.points]);});
    box.append(svg);
    if(drawn.axis)drawn.axis.markers.forEach(p=>{const dot=block("span",undefined,"presentation-diagram-marker");dot.style.left=pct(p[0]);dot.style.top=pct(p[1]);dot.setAttribute("aria-hidden","true");box.append(dot);});
    drawn.nodes.forEach(n=>{
      const shown=placeAt(identify(block("div",undefined,"presentation-diagram-node"),n.id),n.box);
      shown.dataset.shape=n.shape;shown.dataset.tone=n.tone;shown.setAttribute("role","img");
      shown.setAttribute("aria-label",model.targets.find(t=>t.member===n.id).accessible_name);
      if(SHAPES[n.shape]){const outline=svgNode("svg",{viewBox:"0 0 100 100",preserveAspectRatio:"none","aria-hidden":"true"});
        outline.classList.add("presentation-diagram-shape");outline.append(SHAPES[n.shape]());shown.append(outline);}
      shown.append(block("div",n.label,"presentation-diagram-text"));box.append(shown);
      if(n.when_box){const when=placeAt(block("div",n.when,"presentation-diagram-when"),n.when_box);when.dataset.labelOf=n.id;box.append(when);}
    });
    drawn.links.forEach(link=>{if(!link.label_box)return;
      const words=placeAt(block("div",link.label,"presentation-diagram-label"),link.label_box);words.dataset.labelOf=link.id;box.append(words);});
    // The connectors in px: the slot's shares times the diagram's own size now.
    function draw(){const w=box.clientWidth,h=box.clientHeight;if(!w||!h)return;
      paths.forEach(([e,points])=>{const xy=points.map(p=>[p[0]*w,p[1]*h]);
        if(e.tagName==="line"){e.setAttribute("x1",xy[0][0]);e.setAttribute("y1",xy[0][1]);e.setAttribute("x2",xy[1][0]);e.setAttribute("y2",xy[1][1]);}
        else e.setAttribute("d","M"+xy.map(p=>p[0].toFixed(2)+","+p[1].toFixed(2)).join(" L"));});}
    if(window.ResizeObserver)new ResizeObserver(draw).observe(box);
    requestAnimationFrame(draw);parent.append(box);
  }
  const layoutArrow=model.diagram_arrow||5;
  const renderers={chart,callout,figures,steps,diagram,text:textObject,vector:vectorObject,bar:barObject,table:tableObject,image:(object,parent)=>{
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
    // A connector or a group is lit with its words.
    if(["connector","group","node"].includes(target.kind))
      document.querySelectorAll(`[data-label-of="${CSS.escape(id)}"]`).forEach(e=>rects.push(...e.getClientRects()));
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
