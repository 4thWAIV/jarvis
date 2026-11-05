import * as THREE from '../../../../frontend/node_modules/three/build/three.module.js';
import data from './filaments.json';
import {gentlePulse} from './pulse-policy';
export {data as filamentData};
/** Continuous anatomical strands, connected back to the existing living core. */
export function createAnatomicalFilaments(parent:any,transform:(x:number,y:number,z:number)=>number[],normalTransform:(x:number,y:number,z:number)=>number[]){
 const samples:any[]=[],segments:number[]=[],branches:any[]=[];
 data.paths.forEach((path,index)=>{
  const nodes:any[]=[],normals:any[]=[];
  for(let i=0;i<path.positions.length;i+=3){nodes.push(new THREE.Vector3(...transform(path.positions[i],path.positions[i+1],path.positions[i+2])));normals.push(new THREE.Vector3(...normalTransform(path.normals[i],path.normals[i+1],path.normals[i+2])).normalize());}
  const curve=new THREE.CatmullRomCurve3(nodes,false,'centripetal');
  const offset=samples.length,count=(nodes.length-1)*2;
  for(let j=0;j<=count;j++){
   const u=j/count,v=curve.getPoint(u),f=u*(normals.length-1),a=Math.floor(f),normal=normals[a].clone().lerp(normals[Math.min(a+1,normals.length-1)],f-a).normalize();
   samples.push({v,normal,rank:index/data.paths.length});
   if(j)segments.push(offset+j-1,offset+j);
  }
  // Each branch is a continuous curve from the core to one surface-path endpoint.
  {const end=nodes[0],join=end.clone().multiplyScalar(.58),bend=end.clone().multiplyScalar(.22);bend.x+=end.y*.12;branches.push({curve:new THREE.CubicBezierCurve3(new THREE.Vector3(),bend,join,end),rank:index/data.paths.length});}
 });
 const linePositions=new Float32Array(segments.length*3),lineColours=new Float32Array(segments.length*3),pointPositions=new Float32Array(samples.length*3),pointColours=new Float32Array(samples.length*3);
 samples.forEach((s,i)=>pointPositions.set(s.v.toArray(),i*3));segments.forEach((j,i)=>linePositions.set(samples[j].v.toArray(),i*3));
 function geometry(positions:Float32Array,colours:Float32Array){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(positions,3));g.setAttribute('color',new THREE.BufferAttribute(colours,3));return g;}
 const lines=geometry(linePositions,lineColours),points=geometry(pointPositions,pointColours);
 parent.add(new THREE.LineSegments(lines,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.85,depthWrite:true,blending:THREE.NormalBlending})));
 parent.add(new THREE.Points(points,new THREE.PointsMaterial({vertexColors:true,size:.095,transparent:true,opacity:.65,depthWrite:false,blending:THREE.NormalBlending})));
 const branchPositions=new Float32Array(branches.length*30*6),branchColours=new Float32Array(branchPositions.length);
 branches.forEach((b,i)=>{for(let j=0;j<30;j++)branchPositions.set([...b.curve.getPoint(j/30).toArray(),...b.curve.getPoint((j+1)/30).toArray()],(i*30+j)*6);});
 const branchGeo=geometry(branchPositions,branchColours);parent.add(new THREE.LineSegments(branchGeo,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.0128,depthWrite:false,blending:THREE.NormalBlending})));
 const tint=new THREE.Color(),white=new THREE.Color(1,1,1),view=new THREE.Vector3();
 return {pathCount:data.paths.length,sampleCount:samples.length,update(t:number,camera:any,colour:any,density:number,peak:number,edge:number,steady:boolean){
  view.copy(camera.position);parent.worldToLocal(view);view.normalize();
  tint.copy(colour).lerp(white,Math.max(0,Math.min(.88,(peak-.16)/.54*.88)));
  const luma=.2126*tint.r+.7152*tint.g+.0722*tint.b,breath=gentlePulse(t,steady);
  samples.forEach((s,i)=>{
   const u=Math.max(0,Math.min(1,(density-s.rank+.01)/.02)),active=u*u*(3-2*u),facing=s.normal.dot(view),contour=Math.pow(1-Math.abs(facing),10);
   const illumination=(.7+.3*Math.max(0,s.normal.y*.6+s.normal.z*.7));
   const light=Math.max(active*peak*illumination,contour*edge)*breath*(facing<0?.32:1);
   pointColours[i*3]=colour.r*.045+tint.r*light/luma;pointColours[i*3+1]=colour.g*.045+tint.g*light/luma;pointColours[i*3+2]=colour.b*.045+tint.b*light/luma;
  });
  segments.forEach((j,i)=>{lineColours[i*3]=pointColours[j*3];lineColours[i*3+1]=pointColours[j*3+1];lineColours[i*3+2]=pointColours[j*3+2];});
  branches.forEach((b,i)=>{const lit=b.rank<density?.18:.055;for(let j=0;j<30*2;j++)branchColours.set([colour.r*lit,colour.g*lit,colour.b*lit],(i*60+j)*3);});
  for(const g of [lines,points,branchGeo])g.attributes.color.needsUpdate=true;
 }};
}
