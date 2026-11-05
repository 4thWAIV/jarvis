export const pulsePolicy={periodSeconds:8,amplitude:.04,transitionSeconds:2,redPulses:false};
let pulseTime=0;
export function setPulseTime(seconds:number){pulseTime=seconds;}
export function gentlePulse(_seconds:number,steady=false){return steady||pulsePolicy.periodSeconds===0?1:1+pulsePolicy.amplitude*Math.sin(pulseTime*2*Math.PI/pulsePolicy.periodSeconds);}
