import type { ChatResponse, ServerInfo } from "./types";

async function errorText(r: Response): Promise<string> {
  const body = await r.text();
  try {
    const detail = (JSON.parse(body) as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : body;
  } catch {
    return body || `${r.status} ${r.statusText}`;
  }
}

export async function sendMessage(message: string, sessionId: string | null): Promise<ChatResponse> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!r.ok) throw new Error(await errorText(r));
  return (await r.json()) as ChatResponse;
}

export async function getInfo(): Promise<ServerInfo | null> {
  try {
    const r = await fetch("/api/info");
    return r.ok ? ((await r.json()) as ServerInfo) : null;
  } catch {
    return null;
  }
}
