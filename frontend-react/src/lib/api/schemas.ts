import type { components } from "./types";

export type Schemas = components["schemas"];
export type TokenResponse = Schemas["TokenResponse"];
export type MeResponse = Schemas["MeResponse"];
export type Source = Schemas["Source"];
export type SessionInfo = Schemas["SessionInfo"];
export type Role = "employee" | "manager" | "hr";
export type ChatStatus = "ok" | "no_results" | "blocked" | "refused";
