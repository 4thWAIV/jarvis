/**
 * The voice page: the left column (the orb, status and mic), the right panel
 * (agents, diagnostics, workflows), and the centre area between them with
 * its views.
 */

import { CentreArea } from "./CentreArea";
import { Footer } from "./Footer";
import { MenuControl } from "./MenuControl";
import { MicControl } from "./MicControl";
import { RayLane } from "./RayLane";
import { RightPanel } from "./RightPanel";
import { Settings } from "./Settings";
import { OrbControl } from "./OrbControl";
import { StatusControl } from "./StatusControl";
import { startJarvis } from "./jarvis";

export function App() {
  return (
    <>
      {/* The upper left: the orb, what he is doing, and the microphone. The
          agents are a tab of the right-hand display. */}
      <div id="left-column">
        <OrbControl onReady={startJarvis} />
        <StatusControl />
        <MicControl />
      </div>

      {/* The right-hand display, mirroring the left column. */}
      <RightPanel />

      {/* The ⋮ menu, top right, and the Settings view it opens. */}
      <MenuControl />
      <Settings />

      <RayLane />
      {/* Between the two columns: the view tabs and the selected view. */}
      <CentreArea />
      <Footer />
    </>
  );
}
