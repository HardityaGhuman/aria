import type { components } from "./types";

export type Schemas = components["schemas"];
export type TokenResponse = Schemas["TokenResponse"];
export type MeResponse = Schemas["MeResponse"];
export type Source = Schemas["Source"];
export type SessionInfo = Schemas["SessionInfo"];
export type Role = "employee" | "manager" | "hr";
// Derived from the codegen'd contract so it always matches the backend
// envelope: ok | partial | no_results | blocked | refused | tool_unavailable.
export type ChatStatus = Schemas["ChatResponse"]["status"];
