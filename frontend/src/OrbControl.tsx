/**
 * The orb as one control on the page: it fills the box the page gives it.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { activeBrain, brains } from "./brains";
import { createOrb, type Orb } from "./orb";
import { PROVIDERS } from "./providers";

/** The main orb, for the rays (RayLane.tsx), which make it transmit. */
export const mainOrb: { current: Orb | null } = { current: null };

export function OrbControl({ onReady }: { onReady: (orb: Orb) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    // Transparent, so a ray drawn behind it shows through, as if it came from
    // inside the orb. The page behind is the same near-black.
    const orb = createOrb(canvasRef.current!, { transparent: true, core: true, ownRenderer: true });
    mainOrb.current = orb;
    onReady(orb);
    return () => {
      mainOrb.current = null;
      orb.destroy();
    };
  }, [onReady]);

  return (
    <div id="orb-control">
      <canvas id="orb-canvas" ref={canvasRef} />
      <BrainLogo />
    </div>
  );
}

/** The active brain's logo, tight in the orb's lower left corner, so the
 *  orb shows which brain is running. From the shared brain list (brains.ts,
 *  read again on every reconnect and whenever a brain becomes ready) and the
 *  same providers table the usage card uses; nothing shows for a provider
 *  with no logo, or when no brain is known to be running. */
function BrainLogo() {
  const live = useSyncExternalStore(brains.subscribe, brains.get);
  const provider = activeBrain(live.value);
  const [missing, setMissing] = useState(false);

  const info = provider ? PROVIDERS[provider] : undefined;
  if (!info?.logo || missing) return null;
  return (
    <img id="brain-logo" src={info.logo} alt={info.name} title={`${info.name} brain`}
         data-provider={provider} onError={() => setMissing(true)} />
  );
}
