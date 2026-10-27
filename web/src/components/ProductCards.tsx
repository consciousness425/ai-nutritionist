import type { ProductCard } from "../types";

export function ProductCards({ cards }: { cards: ProductCard[] }) {
  return (
    <div className="cards">
      {cards.map((c) => (
        <article className="card" key={c.id}>
          {c.image && <img src={c.image} alt="" loading="lazy" />}
          <a href={c.url} target="_blank" rel="noreferrer">
            {c.title}
          </a>
          <div className="price">S${c.price_sgd.toFixed(2)}</div>
          <p>{c.why}</p>
          {c.warnings.map((w) => (
            <p className="warn" key={w}>
              ⚠ {w}
            </p>
          ))}
        </article>
      ))}
    </div>
  );
}
