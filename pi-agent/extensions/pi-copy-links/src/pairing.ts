// This two-host policy owns SSH authority; neither request data nor inherited
// HOSTNAME/SSH/DISPLAY variables may choose a peer.
const COMPANY = "conley-company";
const LAPTOP = "conley-Y9000p";

export type PairedSshAlias = typeof COMPANY | typeof LAPTOP;

export function pairedSshAlias(actualHostname: string): PairedSshAlias | undefined {
  switch (actualHostname.toLowerCase()) {
    case COMPANY: return LAPTOP;
    case LAPTOP.toLowerCase(): return COMPANY;
    default: return undefined;
  }
}

export function isPairedSshAlias(value: unknown): value is PairedSshAlias {
  return value === COMPANY || value === LAPTOP;
}
