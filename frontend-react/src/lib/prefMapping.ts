export type Tone = "Concise" | "Balanced" | "Detailed";

const L2T: Record<string, Tone> = { short: "Concise", medium: "Balanced", long: "Detailed" };
const T2L: Record<Tone, string> = { Concise: "short", Balanced: "medium", Detailed: "long" };

export function lengthToTone(len: string): Tone {
  return L2T[len] ?? "Balanced";
}
export function toneToLength(t: Tone): string {
  return T2L[t];
}
