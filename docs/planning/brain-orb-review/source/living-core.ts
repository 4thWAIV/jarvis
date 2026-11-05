import * as THREE from '../../../../frontend/node_modules/three/build/three.module.js';
/** A luminous moving nexus with no solid disc, fixed halo, or translucent backing sphere. */
export function createLivingCore(parent:THREE.Object3D){
 const root=new THREE.Group();parent.add(root);
 const n=300,geometry=new THREE.BufferGeometry();
 const positions=new Float32Array(n*3),colours=new Float32Array(n*3);
 geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.BufferAttribute(colours,3));
 const material=new THREE.PointsMaterial({size:.22,vertexColors:true,transparent:true,opacity:.85,depthWrite:false,blending:THREE.AdditiveBlending});
 const points=new THREE.Points(geometry,material);points.renderOrder=8;root.add(points);
 const lineGeometry=new THREE.BufferGeometry();const links=new Float32Array(n*6);lineGeometry.setAttribute('position',new THREE.BufferAttribute(links,3));
 const lineMaterial=new THREE.LineBasicMaterial({color:0xcdeeff,transparent:true,opacity:.45,depthWrite:false,blending:THREE.AdditiveBlending});
 const lines=new THREE.LineSegments(lineGeometry,lineMaterial);lines.renderOrder=8;root.add(lines);
 return {root,update(t:number){
  for(let i=0;i<n;i++){
   const phi=i*2.39996+t*(.6+.18*Math.sin(i)),u=(i+.5)/n;
   const z=2*u-1,rad=Math.sqrt(1-z*z),r=.65+1.9*(.5+.5*Math.sin(i*1.7+t*.9));
   positions[i*3]=r*rad*Math.cos(phi)+.24*Math.sin(t*2.1+i*.13);
   positions[i*3+1]=r*rad*Math.sin(phi)+.26*Math.cos(t*1.7+i*.17);
   positions[i*3+2]=r*z+.35*Math.sin(t*1.5+i*.23);
   const bright=.45+.55*Math.pow(.5+.5*Math.sin(t*3-i*.3),2);
   colours[i*3]=bright*.8;colours[i*3+1]=bright*.94;colours[i*3+2]=bright;
  }
  let count=0;
  // Nearest local links reform as the particles move, rather than rotating a fixed mesh.
  for(let i=0;i<n;i+=2){let best=-1,dist=.64;
   for(let j=i+1;j<Math.min(n,i+22);j++){const d=(positions[i*3]-positions[j*3])**2+(positions[i*3+1]-positions[j*3+1])**2+(positions[i*3+2]-positions[j*3+2])**2;if(d<dist){dist=d;best=j;}}
   if(best>=0){for(let k=0;k<3;k++){links[count*6+k]=positions[i*3+k];links[count*6+3+k]=positions[best*3+k];}count++;}
  }
  geometry.getAttribute('position').needsUpdate=true;geometry.getAttribute('color').needsUpdate=true;
  lineGeometry.getAttribute('position').needsUpdate=true;lineGeometry.setDrawRange(0,count*2);
 },dispose(){root.removeFromParent();geometry.dispose();material.dispose();lineGeometry.dispose();lineMaterial.dispose();}};
}
