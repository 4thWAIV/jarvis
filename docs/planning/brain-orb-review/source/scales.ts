import {FontLoader} from '../../../../frontend/node_modules/three/examples/jsm/loaders/FontLoader.js';
import typeface from '../../../../frontend/node_modules/three/examples/fonts/helvetiker_regular.typeface.json';
import {createAnatomicalFilaments,filamentData} from './anatomical-filaments';
import * as THREE from '../../../../frontend/node_modules/three/build/three.module.js';
import {createOrb} from './orb-renderer';
import {createLivingCore} from './core';
import {pulsePolicy, gentlePulse, setPulseTime} from './pulse-policy';

const $=(id:string)=>document.getElementById(id)!;
let power=86,tokens=74;
let mode=0,time=0,last=performance.now(),paused=false,scene:any=null,cameraRef:any=null,targets:any[]=[];
let level=5.2,inspectionYaw=0;
let reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
let visualColour=new THREE.Color('#278451'),visualDensity=.92,visualPeak=.035,visualEdge=.03;
const brainCenter=filamentData.center;
const positive=new THREE.Color('#278451'),neutral=new THREE.Color('#f5f8ff'),negativeOne=new THREE.Color('#ffda20'),negativeTwo=new THREE.Color('#ff343f'),coreBlue=new THREE.Color('#cdeeff');
function stateFor(value:number){return value>.5?'green':value>=-.5?'white':value>=-2?'yellow':'red';}
function levelColour(value:number){return ({green:positive,white:neutral,yellow:negativeOne,red:negativeTwo}[stateFor(value)]).clone();}
function lightingFor(value:number){return ({green:{density:.92,peak:.035,edge:.03},white:{density:.48,peak:.16,edge:.12},yellow:{density:.20,peak:.35,edge:.28},red:{density:.055,peak:.70,edge:.65}})[stateFor(value)];}
function radialLight(value:number,r:number){return lightingFor(value).peak;}
function updateLighting(dt:number){const q=lightingFor(level),a=reduced?1:1-Math.exp(-dt/.65);visualColour.lerp(levelColour(level),a);visualDensity+=(q.density-visualDensity)*a;visualPeak+=(q.peak-visualPeak)*a;visualEdge+=(q.edge-visualEdge)*a;}
const brainYaw=2.25,brainRoll=Math.PI/30;
function brainPosition(x:number,y:number,z:number){const dx=(x-brainCenter[0])*.64,dy=(y-brainCenter[1])*.64,dz=(z-brainCenter[2])*.64,rx=dx*Math.cos(brainYaw)+dz*Math.sin(brainYaw),rz=-dx*Math.sin(brainYaw)+dz*Math.cos(brainYaw);return [rx*Math.cos(brainRoll)-dy*Math.sin(brainRoll),rx*Math.sin(brainRoll)+dy*Math.cos(brainRoll),rz];}
function magnitude(v:number){return Math.min(Math.abs(v),99.9).toFixed(1).padStart(4,'0');}
function sign(v:number){return v<0&&magnitude(v)!=='00.0'?'-':'+';}
const font=new FontLoader().parse(typeface);
function percentage(parent:any,source:any){
 const original=source.parent.children.find((o:any)=>o.isLineSegments);
 const root=new THREE.Group();root.position.set(0,-11,8);parent.add(root);
 const textGeo=new THREE.BufferGeometry(),textMat=original.material.clone();textMat.color.setHex(0xffffff);textMat.opacity=.95;textMat.vertexColors=true;textMat.blending=THREE.NormalBlending;
 const screenGeo=new THREE.BufferGeometry();const screenMat=new THREE.MeshBasicMaterial({color:0x060d16,colorWrite:true,depthWrite:true,depthTest:true,side:THREE.DoubleSide});const screen=new THREE.Mesh(screenGeo,screenMat);screen.position.z=-2.5;screen.renderOrder=-20;root.add(screen);
 const textLines=new THREE.LineSegments(textGeo,textMat);textLines.rotation.set(0,0,0);root.add(textLines);
 const windowGeo=new THREE.BufferGeometry(),windowMat=original.material.clone();windowMat.opacity=.5;windowMat.blending=THREE.AdditiveBlending;root.add(new THREE.LineSegments(windowGeo,windowMat));
 const feedGeo=new THREE.BufferGeometry(),feedMat=original.material.clone();feedMat.vertexColors=true;feedMat.color.setHex(0xffffff);feedMat.opacity=.12;const feedPos=new Float32Array(6*24*6),feedColours=new Float32Array(feedPos.length);feedGeo.setAttribute('position',new THREE.BufferAttribute(feedPos,3));feedGeo.setAttribute('color',new THREE.BufferAttribute(feedColours,3));parent.add(new THREE.LineSegments(feedGeo,feedMat));
 const signalGeo=new THREE.BufferGeometry(),signalPos=new Float32Array(24*3);signalGeo.setAttribute('position',new THREE.BufferAttribute(signalPos,3));const signalMat=source.material.clone();signalMat.vertexColors=false;signalMat.size=.23;signalMat.opacity=.95;parent.add(new THREE.Points(signalGeo,signalMat));
 let previous='',width=14,changedAt=0,strokes:number[]=[];
 function portalPoint(x:number,y:number,z:number){
  // A shallow spherical patch: longitude bends the sides back into the orb;
  // latitude gives a small, natural narrowing toward the lower hemisphere.
  const longitude=x/24,latitude=(y-11)/32,centerLatitude=(1.8-11)/32;
  return new THREE.Vector3(24*Math.sin(longitude)*Math.cos(latitude)/Math.cos(centerLatitude),y-.012*x*x,z+24*(Math.cos(longitude)*Math.cos(latitude)-Math.cos(centerLatitude)));
 }
 function segment(a:any,b:any,z=0){strokes.push(a.x,a.y,z,b.x,b.y,z);}
 function rebuild(text:string){strokes=[];let cursor=0;
  for(const [word,size,lift] of [[sign(level),5.4,-.3],[magnitude(level),3.6,0],['%',1.8,1.8]] as [string,number,number][]){
   const shapes=font.generateShapes(word,size),contours=shapes.map(shape=>shape.extractPoints(10));let xmin=Infinity,xmax=-Infinity;
   contours.forEach(c=>c.shape.forEach(v=>{xmin=Math.min(xmin,v.x);xmax=Math.max(xmax,v.x);}));
   for(const c of contours){const loops=[c.shape,...c.holes].map(loop=>loop.map(v=>new THREE.Vector2(v.x-xmin+cursor,v.y+lift)));
    for(const loop of loops)for(let i=0;i<loop.length-1;i++){
     const a=loop[i],b=loop[i+1];
     // Front and back outlines, side-wall strands, and through-depth connections.
     for(const z of [-.65,-.3,.15,.65])segment(a,b,z);
     const steps=Math.max(1,Math.ceil(a.distanceTo(b)/.18));for(let j=0;j<=steps;j++){const v=a.clone().lerp(b,j/steps);strokes.push(v.x,v.y,-.65,v.x,v.y,.65);}
    }
    // Close-packed filament strands form the glyph interiors; no solid text mesh is rendered.
    const ys=loops[0].map(v=>v.y),low=Math.min(...ys),high=Math.max(...ys);
    for(let y=low+.06;y<high;y+=.13){const xs:number[]=[];for(const loop of loops)for(let j=0;j<loop.length;j++){const a=loop[j],b=loop[(j+1)%loop.length];if((a.y>y)!==(b.y>y))xs.push(a.x+(y-a.y)*(b.x-a.x)/(b.y-a.y));}xs.sort((a,b)=>a-b);for(let j=0;j+1<xs.length;j+=2)segment({x:xs[j],y},{x:xs[j+1],y},.65);}
   }cursor+=xmax-xmin+.4;
  }
  width=cursor-.4;for(let i=0;i<strokes.length;i+=3)strokes[i]-=width/2;
  textGeo.setAttribute('position',new THREE.Float32BufferAttribute(strokes,3));const shades:number[]=[];for(let i=0;i<strokes.length;i+=3){const light=.2+.8*(strokes[i+2]+.65)/1.3;shades.push(light,light,light);}textGeo.setAttribute('color',new THREE.Float32BufferAttribute(shades,3));
  const outline:number[]=[];const w=width/2+1.25,h=3.3,cy=1.8,r=.9;
  const aperture=new THREE.Shape();aperture.moveTo(-w+r,cy-h);aperture.lineTo(w-r,cy-h);aperture.quadraticCurveTo(w,cy-h,w,cy-h+r);aperture.lineTo(w,cy+h-r);aperture.quadraticCurveTo(w,cy+h,w-r,cy+h);aperture.lineTo(-w+r,cy+h);aperture.quadraticCurveTo(-w,cy+h,-w,cy+h-r);aperture.lineTo(-w,cy-h+r);aperture.quadraticCurveTo(-w,cy-h,-w+r,cy-h);
  screen.geometry.dispose();screen.geometry=new THREE.ShapeGeometry(aperture,16);const aperturePositions=screen.geometry.attributes.position;for(let i=0;i<aperturePositions.count;i++){const v=portalPoint(aperturePositions.getX(i),aperturePositions.getY(i),aperturePositions.getZ(i));aperturePositions.setXYZ(i,v.x,v.y,v.z);}aperturePositions.needsUpdate=true;screen.geometry.computeVertexNormals();

  for(const depth of [-.5,.35]){let last:any=null;for(let corner=0;corner<4;corner++){const cx=corner===0||corner===3?w-r:-w+r,yy=corner<2?cy+h-r:cy-h+r;for(let j=0;j<=12;j++){const angle=corner*Math.PI/2+j/12*Math.PI/2,point=[cx+r*Math.cos(angle),yy+r*Math.sin(angle),depth];if(last)outline.push(...last,...point);last=point;}}const first=[w,cy+h-r,depth];outline.push(...last,...first);}
  for(let i=0;i<outline.length;i+=3){const v=portalPoint(outline[i],outline[i+1],outline[i+2]);outline[i]=v.x;outline[i+1]=v.y;outline[i+2]=v.z;}windowGeo.setAttribute('position',new THREE.Float32BufferAttribute(outline,3));
 }
 return {update(t:number){if(cameraRef){parent.updateWorldMatrix(true,false);root.lookAt(cameraRef.position);root.updateWorldMatrix(true,false);}const text=sign(level)+magnitude(level)+'%',colour=visualColour.clone();if(text!==previous){previous=text;changedAt=t;rebuild(text);}
  textMat.color.copy(colour);signalMat.color.copy(colour);windowMat.color.copy(colour);
  const reveal=1;textGeo.setDrawRange(0,Math.floor((strokes.length/3)*reveal/2)*2);
  let offset=0;for(let branch=0;branch<6;branch++){const side=branch%2?-1:1,end=parent.worldToLocal(root.localToWorld(portalPoint(side*(width/2+1.25),branch<2?4.2:branch<4?1.8:-.6,0))),bend=new THREE.Vector3(end.x*.48,-1,5+Math.sin(t*.4+branch)*.3),curve=new THREE.CubicBezierCurve3(new THREE.Vector3(),new THREE.Vector3(side*(width/2+4),1,4),end.clone().add(new THREE.Vector3(0,1,0)),end);
   for(let j=0;j<24;j++){const a=curve.getPoint(j/24),b=curve.getPoint((j+1)/24);feedPos.set([...a.toArray(),...b.toArray()],offset);for(let endIndex=0;endIndex<2;endIndex++){const u=(j+endIndex)/24,glow=gentlePulse(t,reduced||stateFor(level)==='red')*.45,tint=colour.clone().multiplyScalar(glow);feedColours.set(tint.toArray(),offset+endIndex*3);}offset+=6;}
   for(let j=0;j<4;j++){const u=(t*.08+branch*.12+j*.25)%1;signalPos.set(curve.getPoint(u).toArray(),(branch*4+j)*3);}
  }
  feedGeo.attributes.position.needsUpdate=true;feedGeo.attributes.color.needsUpdate=true;signalGeo.attributes.position.needsUpdate=true;
 }};
}
function setLevel(value:number){level=value;$('level').value=String(value);$('level-value').textContent=sign(value)+magnitude(value)+'%';document.querySelectorAll<HTMLButtonElement>('[data-level]').forEach(b=>b.setAttribute('aria-pressed',String(stateFor(Number(b.dataset.level))===stateFor(value))));if(typeof orb!=='undefined')redrawStatic();}

function brainOrb(parent:any,source:any,phase:number){
 const reading=percentage(parent,source);
 const core=createLivingCore(parent),sourceLines=source.parent.children.find((o:any)=>o.isLineSegments);
 const shellGeo=source.geometry.clone();shellGeo.setAttribute('position',new THREE.BufferAttribute(new Float32Array(source.geometry.attributes.position.array.length),3));const shell=new THREE.Points(shellGeo,source.material.clone());shell.material.size*=.44;shell.material.opacity=.3;parent.add(shell);
 const envelopeGeo=sourceLines.geometry.clone();envelopeGeo.setAttribute('position',new THREE.BufferAttribute(new Float32Array(sourceLines.geometry.attributes.position.array.length),3));const envelope=new THREE.LineSegments(envelopeGeo,sourceLines.material.clone());envelope.material.opacity=.065;envelope.material.vertexColors=true;envelopeGeo.setAttribute('color',new THREE.BufferAttribute(new Float32Array(envelopeGeo.attributes.position.array.length),3));parent.add(envelope);
 const normalTransform=(x:number,y:number,z:number)=>{const rx=x*Math.cos(brainYaw)+z*Math.sin(brainYaw);return [rx*Math.cos(brainRoll)-y*Math.sin(brainRoll),rx*Math.sin(brainRoll)+y*Math.cos(brainRoll),-x*Math.sin(brainYaw)+z*Math.cos(brainYaw)];};
 const anatomy=createAnatomicalFilaments(parent,brainPosition,normalTransform);
 return {update(t:number){
  core.update(t,reduced||stateFor(level)==='red');const stateColour=visualColour.clone();core.root.traverse((o:any)=>{if(o.isPoints){const a=o.geometry.attributes.color;for(let i=0;i<a.count;i++){const intensity=a.getZ(i);a.setXYZ(i,intensity*stateColour.r*.1,intensity*stateColour.g*.1,intensity*stateColour.b*.1);}a.needsUpdate=true;}else if(o.isLineSegments)o.material.color.copy(stateColour).multiplyScalar(.1);});reading.update(t);envelope.material.color.setHex(0xffffff);
  const routeAroundWindow=(x:number,y:number,z:number)=>{if(z>4&&Math.abs(x)<10&&y>-15&&y<-5){const dx=10-Math.abs(x),dy=Math.min(y+15,-5-y);if(dx<dy)x=(x<0?-1:1)*10;else y=y<-10?-15:-5;}return [x,y,z];};
  for(const [original,target,scale] of [[source.geometry,shellGeo,.75],[sourceLines.geometry,envelopeGeo,.92]] as any[]){const a=original.attributes.position.array,b=target.attributes.position.array;for(let i=0;i<a.length;i+=3){const v=routeAroundWindow(a[i]*scale,a[i+1]*scale,a[i+2]*scale);b[i]=v[0];b[i+1]=v[1];b[i+2]=v[2];}if(target===envelopeGeo){for(let i=0;i<b.length;i+=6){if(Math.max(b[i+2],b[i+5])<=4)continue;const dx=b[i+3]-b[i],dy=b[i+4]-b[i+1];let low=0,high=1,hit=true;const ps=[-dx,dx,-dy,dy],qs=[b[i]+9.7,9.7-b[i],b[i+1]+14.7,-5.3-b[i+1]];for(let j=0;j<4;j++){if(Math.abs(ps[j])<1e-8){if(qs[j]<0)hit=false;}else{const u=qs[j]/ps[j];if(ps[j]<0)low=Math.max(low,u);else high=Math.min(high,u);}}if(hit&&low<high){b[i+3]=b[i];b[i+4]=b[i+1];b[i+5]=b[i+2];}}}if(target===shellGeo){const sourceColours=source.geometry.attributes.color,localColours=shellGeo.attributes.color;for(let i=0;i<localColours.count;i++){const k=i*3,r=Math.hypot(b[k],b[k+1]),u=Math.max(0,Math.min(1,(r-7)/10)),blend=u*u*(3-2*u),strength=Math.max(sourceColours.getX(i),sourceColours.getY(i),sourceColours.getZ(i));localColours.setXYZ(i,strength*stateColour.r*.1*(1-blend)+sourceColours.getX(i)*blend,strength*stateColour.g*.1*(1-blend)+sourceColours.getY(i)*blend,strength*stateColour.b*.1*(1-blend)+sourceColours.getZ(i)*blend);}localColours.needsUpdate=true;}if(target===envelopeGeo){const c=target.attributes.color;for(let i=0;i<c.count;i++){const k=i*3,u=Math.max(0,Math.min(1,(Math.hypot(b[k],b[k+1])-7)/10)),blend=u*u*(3-2*u),tint=stateColour.clone().multiplyScalar(.1).lerp(sourceLines.material.color,blend);c.setXYZ(i,tint.r,tint.g,tint.b);}c.needsUpdate=true;}target.attributes.position.needsUpdate=true;target.setDrawRange(original.drawRange.start,original.drawRange.count);} 

  anatomy.update(t,cameraRef,visualColour,visualDensity,visualPeak,visualEdge,reduced||stateFor(level)==='red');
 }};
}
function change(n:number){mode=n;document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.mode)===n)));$('caption').textContent=n===0?'JARVIS alone. The same central orb remains the anchor at every scale.':n===1?'One nearby brain orb represents the brains running JARVIS. Its own particles and filaments form the brain.':'Two nearby brain orbs expose Claude and Codex separately. Both use the same orb material.';if(typeof orb!=='undefined')redrawStatic();}
(window as any).toolOrbMorph=()=>0;
(window as any).toolOrbHook=(_c:any,_a:any,_ca:any,points:any,_t:any,core:any,halo:any,camera:any)=>{
 cameraRef=camera;
 if(!scene){const root=new THREE.Group(),nucleus=new THREE.Group();points.parent.add(root,nucleus);scene={root,nucleus,core:createLivingCore(nucleus),brains:[]};for(let i=0;i<2;i++){const holder=new THREE.Group();root.add(holder);const orb=brainOrb(holder,points,i*2);scene.brains.push({holder,orb});}}
 core.visible=false;halo.visible=false;scene.nucleus.position.copy(points.position);scene.core.update(time,reduced);
 const w=$('stage').clientWidth,h=$('stage').clientHeight;camera.clearViewOffset();camera.aspect=w/h;camera.position.set(0,0,210);camera.lookAt(0,0,0);camera.updateProjectionMatrix();camera.updateMatrixWorld();targets=[];
 scene.brains.forEach((b:any,i:number)=>{const visible=mode>0&&(i===0||mode===2);b.holder.visible=visible;if(!visible)return;const a=.35+time*.065+i*Math.PI*.9,r=i===0?62:74;b.holder.position.set(Math.cos(a)*r,Math.sin(a)*r*.46,Math.sin(a)*r*.42);b.holder.rotation.y=inspectionYaw;b.orb.update(time);b.holder.updateMatrixWorld(true);const world=b.holder.getWorldPosition(new THREE.Vector3()),v=world.clone().project(camera),units=2*(210-world.z)*Math.tan(THREE.MathUtils.degToRad(camera.fov/2))/h;const x=(v.x+1)*w/2,y=(1-v.y)*h/2;targets.push({i,x,y,r:Math.max(22,9/units)});});
};
const orb=createOrb($('cosmos') as HTMLCanvasElement,{particles:2400,core:true,transparent:true,ownRenderer:true});orb.setState('thinking');
document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(b=>b.onclick=()=>change(Number(b.dataset.mode)));
function applyMotion(){orb.setPaused(paused||reduced);$('pause').textContent=paused?'Play motion':'Pause motion';} $('pause').onclick=()=>{paused=!paused;applyMotion();};
function redrawStatic(){if(reduced){updateLighting(1);orb.setPaused(false);requestAnimationFrame(()=>requestAnimationFrame(()=>orb.setPaused(true)));}}
$('reduce-motion').onchange=(e:any)=>{reduced=e.target.checked;applyMotion();redrawStatic();};($('reduce-motion') as HTMLInputElement).checked=reduced;matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e=>{reduced=e.matches;($('reduce-motion') as HTMLInputElement).checked=reduced;applyMotion();redrawStatic();});
function tick(now:number){if(!paused)setPulseTime(now/1000);const wave=gentlePulse(now/1000,reduced);const indicator=$('pulse-level');if(indicator)indicator.style.filter=`brightness(${wave})`;const elapsed=Math.max(0,(now-last)/1000);if(!paused&&!reduced)time+=Math.min(.06,elapsed);updateLighting(elapsed);last=now;requestAnimationFrame(tick);}requestAnimationFrame(tick);change(1);
$('level').oninput=(e:any)=>setLevel(Number(e.target.value));document.querySelectorAll<HTMLButtonElement>('[data-level]').forEach(b=>b.onclick=()=>setLevel(Number(b.dataset.level)));setLevel(level);redrawStatic();
(window as any).brainStudy={change,snapshot:()=>({mode,targets:targets.length,level,state:stateFor(level),number:sign(level)+magnitude(level)+'%',anchor:brainPosition(...brainCenter),scale:.64,density:visualDensity,peak:visualPeak,reduced,pulsePolicy,rollDegrees:6,pathCount:filamentData.paths.length,sourceVertexCount:filamentData.sourceVertices,colour:levelColour(level).getHexString()}),levelColour:(v:number)=>levelColour(v).getHexString(),setLevel,radialLight,stateFor,seek:(v:number)=>time=v,portalFacing:()=>{const result:any[]=[];scene?.root.traverse((o:any)=>{if(o.geometry?.type==='ShapeGeometry'){const root=o.parent,normal=new THREE.Vector3(0,0,1).applyQuaternion(root.getWorldQuaternion(new THREE.Quaternion())),direction=cameraRef.position.clone().sub(root.getWorldPosition(new THREE.Vector3())).normalize();result.push(normal.dot(direction));}});return result;}};

document.querySelectorAll<HTMLButtonElement>('[data-period]').forEach(b=>b.onclick=()=>{pulsePolicy.periodSeconds=Number(b.dataset.period);document.querySelectorAll('[data-period]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('pulse-description').textContent=pulsePolicy.periodSeconds?`One gentle cycle every ${pulsePolicy.periodSeconds} seconds. Red remains steady.`:'Steady illumination. No brightness pulse.';});

document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(b=>b.onclick=()=>{inspectionYaw=Number(b.dataset.view);document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));redrawStatic();});
