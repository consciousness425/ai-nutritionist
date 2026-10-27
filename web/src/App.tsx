import { useEffect, useRef, useState } from "react";
import { getInfo, sendMessage } from "./api";
import { Message } from "./components/Message";
import { SidePanel } from "./components/SidePanel";
import type { ChatItem, Profile, ServerInfo, TraceEntry } from "./types";

const EXAMPLES = [
  "I'm 12 weeks pregnant and so tired. What can help?",
  "I take warfarin. Is there a hair supplement I can use?",
  "Compare your omega-3 options for me, I'm vegan",
  "Should I stop my metformin and take bitter melon instead?",
];

export default function App() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [info, setInfo] = useState<ServerInfo | null>(null);
  const [draft, setDraft] = useState("");
  const [busySince, setBusySince] = useState<number | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void getInfo().then(setInfo);
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [items, busySince]);

  async function submit(text: string) {
    const message = text.trim();
    if (!message || busySince !== null) return;
    setItems((xs) => [...xs, { kind: "user", text: message }]);
    setDraft("");
    setBusySince(Date.now());
    try {
      const d = await sendMessage(message, sessionId);
      setSessionId(d.session_id);
      setItems((xs) => [...xs, { kind: "bot", text: d.reply, cards: d.recommendations, route: d.route }]);
      setProfile(d.profile);
      setTrace(d.trace);
    } catch (e) {
      setItems((xs) => [...xs, { kind: "error", text: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setBusySince(null);
      inputRef.current?.focus();
    }
  }

  function reset() {
    setItems([]);
    setSessionId(null);
    setProfile(null);
    setTrace([]);
  }

  return (
    <div className="app">
      <main className="chat">
        <header>
          <div>
            <h1>Nutri - supplement assistant</h1>
            <p>Prototype. Not medical advice. In an emergency call 995.</p>
          </div>
          <button type="button" className="ghost" onClick={reset} disabled={busySince !== null || !items.length}>
            New chat
          </button>
        </header>

        <div className="log" ref={logRef}>
          {items.length === 0 && (
            <p className="empty">
              Ask about supplements for your goals, or try one of the examples. Nutri will check pregnancy,
              conditions, medicines and allergies before recommending anything.
            </p>
          )}
          {items.map((item, i) => (
            <Message key={i} item={item} />
          ))}
          {busySince !== null && <Pending since={busySince} slow={info?.backend === "claude-code"} />}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit(draft);
          }}
        >
          <textarea
            ref={inputRef}
            rows={2}
            value={draft}
            placeholder="Ask about supplements..."
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit(draft);
              }
            }}
          />
          <button type="submit" disabled={busySince !== null || !draft.trim()}>
            Send
          </button>
        </form>
      </main>

      <SidePanel
        examples={EXAMPLES}
        onExample={(t) => void submit(t)}
        busy={busySince !== null}
        profile={profile}
        trace={trace}
        info={info}
      />
    </div>
  );
}

function Pending({ since, slow }: { since: number; slow: boolean }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.floor((now - since) / 1000);
  return (
    <div className="msg bot pending" aria-live="polite">
      Thinking… {secs}s
      {slow && secs > 15 && <span className="hint"> (the Claude Code backend takes 30-90 s per reply)</span>}
    </div>
  );
}
