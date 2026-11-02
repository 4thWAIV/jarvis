/**
 * The orb as one control on the page: it fills the box the page gives it.
 * It is also a target the brain can point at (the whole orb, its canvas or
 * the brain's logo), hung from the left rail. The canvas is pointed at
 * whole, since its 3D insides have no projection adapter: it fills this box,
 * so the box draws it, and the logo lying on it is inside it, not beside it.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { activeBrain, brains } from "./brains";
import { useT } from "./locale";
import { createOrb, type Orb } from "./orb";
import { mainOrb } from "./orbSource";
import { PROVIDERS } from "./providers";
import { useUiTarget } from "./uiRegistry";

type PartRef = (el: Element | null) => void;

export function OrbControl({ onReady }: { onReady: (orb: Orb) => void }) {
  const t = useT();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "orb" }, label: t("targets.orb"),
                           socket: { rail: "rail.left", side: "left" } });

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
    <div id="orb-control" ref={(el) => { ui.whole(el); ui.part("canvas")(el); }}>
      <canvas id="orb-canvas" ref={canvasRef} />
      <BrainLogo partRef={ui.part("logo")} />
    </div>
  );
}

/** The active brain's logo, tight in the orb's lower left corner, so the
 *  orb shows which brain is running. From the shared brain list (brains.ts,
 *  read again on every reconnect and whenever a brain becomes ready) and the
 *  same providers table the usage card uses; nothing shows for a provider
 *  with no logo, or when no brain is known to be running. */
function BrainLogo({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(brains.subscribe, brains.get);
  const provider = activeBrain(live.value);
  const [missing, setMissing] = useState(false);

  const info = provider ? PROVIDERS[provider] : undefined;
  if (!info?.logo || missing) return null;
  return (
    <img id="brain-logo" ref={partRef} src={info.logo} alt={info.name} title={t("left.orb.brainLogo", { provider: info.name })}
         data-provider={provider} onError={() => setMissing(true)} />
  );
}
