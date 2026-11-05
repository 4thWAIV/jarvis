import * as THREE from '../../../../frontend/node_modules/three/build/three.module.js';
export const brightnessSteps={green:.50,white:.65,yellow:.72,red:1};
export const stateNames={green:'Green',white:'Yellow-white',yellow:'Yellow',red:'Red'};
export const lightTarget=(brightness:number)=>.82*Math.pow(brightness,2.2);
/** Preserve hue where possible, adding a pale hot center before any channel clips. */
export function colourAtLuminance(base:any,target:number){
 const tint=base.clone(),white=new THREE.Color(1,1,1);
 const luma=(c:any)=>.2126*c.r+.7152*c.g+.0722*c.b;
 let low=0,high=1;
 for(let i=0;i<16;i++){
  const mix=(low+high)/2;c.copy(base).lerp(white,mix);
  if(target*Math.max(c.r,c.g,c.b)>luma(c))low=mix;else high=mix;
 }
 tint.lerp(white,Math.min(1,high+.04));return tint.multiplyScalar(target/luma(tint));
}
const c=new THREE.Color();
