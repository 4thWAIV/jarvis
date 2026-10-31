/**
 * A folder's role and availability, the same everywhere they are shown:
 * the right panel's Folders tab, the catalog and a folder's detail. Each is
 * a word with a mark beside it, so neither depends on colour.
 */

import { AVAILABILITY_WORDS, ROLE_ICONS, ROLE_WORDS, type Availability, type Role } from "./folders";

export function RoleMark({ role, short }: { role: Role; short?: boolean }) {
  return (
    <span className={`folder-role role-${role}`} title={short ? ROLE_WORDS[role] : undefined}>
      <span aria-hidden="true">{ROLE_ICONS[role]}</span>
      {short ? <span className="visually-hidden">{ROLE_WORDS[role]}</span> : ` ${ROLE_WORDS[role]}`}
    </span>
  );
}

export function AvailabilityMark({ availability }: { availability: Availability }) {
  return (
    <span className={`folder-availability availability-${availability}`}>
      <span className="folder-availability-dot" aria-hidden="true" />
      {AVAILABILITY_WORDS[availability]}
    </span>
  );
}
