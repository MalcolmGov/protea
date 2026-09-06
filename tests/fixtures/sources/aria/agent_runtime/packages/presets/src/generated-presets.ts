/* AUTO-GENERATED */
import type { ToolBinding } from "@zara/connectors";
export interface GeneratedPreset { agentId: string; phase: 1 | 2; bindings: ToolBinding[]; }
export const GENERATED_PRESETS: GeneratedPreset[] = [
  {"agentId": "salon-booking", "phase": 1, "bindings": [{"tool": "list_services", "connector": "webhook"}, {"tool": "check_availability", "connector": "google_calendar"}, {"tool": "book_appointment", "connector": "google_calendar"}, {"tool": "handoff_to_human", "connector": "slack"}]},
  {"agentId": "eu-pharmacy", "phase": 1, "bindings": [{"tool": "list_services", "connector": "webhook"}, {"tool": "handoff_to_human", "connector": "teams"}]}
];
