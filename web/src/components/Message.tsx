import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatItem } from "../types";
import { ProductCards } from "./ProductCards";

export function Message({ item }: { item: ChatItem }) {
  if (item.kind === "user") return <div className="msg user">{item.text}</div>;
  if (item.kind === "error") return <div className="msg error">Error: {item.text}</div>;
  return (
    <>
      <div className={`msg bot${item.route.startsWith("emergency") ? " emergency" : ""}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" /> }}
        >
          {item.text}
        </ReactMarkdown>
      </div>
      {item.cards.length > 0 && <ProductCards cards={item.cards} />}
    </>
  );
}
