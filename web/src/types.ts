// Mirrors nutribot.agent.TurnResult and the dicts it carries.

export interface PackOption {
  id: string;
  bundle_of: number;
  price_sgd: number;
}

export interface ProductCard {
  id: string;
  title: string;
  price_sgd: number;
  url: string;
  image: string | null;
  why: string;
  warnings: string[];
  pack_options: PackOption[];
}

export interface Profile {
  age: number | null;
  sex: string | null;
  pregnancy_status: string | null;
  conditions: string[] | null;
  medications: string[] | null;
  allergies: string[] | null;
  diet: string[] | null;
  goals: string[] | null;
  risk_factors: string[];
  unrecognised: Record<string, string[]>;
  screening_complete: boolean;
}

export type TraceEntry =
  | { guard: "triage"; route: string; reason?: string }
  | { guard: "review"; attempt: number; verdict: "pass" | "revise"; problems: string[] }
  | { tool: string; input: unknown; error: boolean; summary: string };

export interface ChatResponse {
  session_id: string;
  reply: string;
  route: string;
  recommendations: ProductCard[];
  profile: Profile;
  trace: TraceEntry[];
}

export interface ServerInfo {
  backend: "api" | "claude-code";
  agent_model: string;
  guard_model: string;
}

export type ChatItem =
  | { kind: "user"; text: string }
  | { kind: "bot"; text: string; cards: ProductCard[]; route: string }
  | { kind: "error"; text: string };
