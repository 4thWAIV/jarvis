import {createLivingCore as createOriginalCore} from './living-core';
import {gentlePulse} from './pulse-policy';
/** Reuse the existing core geometry and motion with shared, low-amplitude lighting. */
export function createLivingCore(parent:any){
 const core=createOriginalCore(parent);
 return {...core,update(t:number,steady=false){
  core.update(t);
  core.root.traverse((object:any)=>{
   if(!object.isPoints)return;
   const colours=object.geometry.attributes.color,breath=gentlePulse(t,steady);
   for(let i=0;i<colours.count;i++){
    const brightness=(.35+.15*Math.sin(i*.3)**2)*breath;
    colours.setXYZ(i,brightness*.8,brightness*.94,brightness);
   }
   colours.needsUpdate=true;
  });
 }};
}
