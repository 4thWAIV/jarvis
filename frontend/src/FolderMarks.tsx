/**
 * A folder's role and availability, the same everywhere they are shown:
 * the right panel's Folders tab, the catalog and a folder's detail. Each is
 * a word with a mark beside it, so neither depends on colour.
 */

import { availabilityWords, ROLE_ICONS, roleWords, type Availability, type Role } from "./folders";
import { useLanguage } from "./locale";

export function RoleMark({ role, short }: { role: Role; short?: boolean }) {
  useLanguage();
  const words = roleWords(role);
  return (
    <span className={`folder-role role-${role}`} title={short ? words : undefined}>
      <span aria-hidden="true">{ROLE_ICONS[role]}</span>
      {short ? <span className="visually-hidden">{words}</span> : ` ${words}`}
    </span>
  );
}

/** `ref` is a registered target's part (the availability itself). */
export function AvailabilityMark({ availability, ref }: {
  availability: Availability; ref?: (el: Element | null) => void }) {
  useLanguage();
  return (
    <span className={`folder-availability availability-${availability}`} ref={ref}>
      <span className="folder-availability-dot" aria-hidden="true" />
      {availabilityWords(availability)}
    </span>
  );
}
