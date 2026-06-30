import { apiFetch } from "./client";
import type { Schemas } from "./schemas";

export type Preferences = Schemas["PreferencesResponse"];
export type PreferencesUpdate = Schemas["UpdatePreferencesRequest"];

export const getPreferences = () => apiFetch<Preferences>("/me/preferences", { auth: true });

export const updatePreferences = (body: PreferencesUpdate) =>
  apiFetch<Preferences>("/me/preferences", {
    method: "PUT",
    auth: true,
    body: JSON.stringify(body),
  });
