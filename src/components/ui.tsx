import type { ReactNode } from 'react';
import { Fragment } from 'react';

/* ------------------------------------------------------------------ */
/* 通用展示组件                                                        */
/* ------------------------------------------------------------------ */

export function Section({ title, extra, children }: { title?: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <div>
      {title ? (
        <div className="section-h">
          <span>{title}</span>
          {extra}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function Bullets({ items, ordered }: { items: string[]; ordered?: boolean }) {
  if (!items.length) return null;
  return (
    <ul className="bullets">
      {items.map((item, index) => (
        <li key={`${index}-${item.slice(0, 12)}`}>
          {ordered ? `${index + 1}. ` : ''}
          {item}
        </li>
      ))}
    </ul>
  );
}

export function Kv({ pairs }: { pairs: [string, ReactNode][] }) {
  const visible = pairs.filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!visible.length) return null;
  return (
    <dl className="kv">
      {visible.map(([key, value], index) => (
        // eslint-disable-next-line react/no-array-index-key
        <Fragment key={`${key}-${index}`}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <table className="data">
      <thead>
        <tr>
          {head.map((title) => (
            <th key={title}>{title}</th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

export function Chip({
  children,
  tone,
  mono,
  title,
}: {
  children: ReactNode;
  tone?: string;
  mono?: boolean;
  title?: string;
}) {
  const classes = ['chip'];
  if (mono) classes.push('mono');
  if (tone) classes.push(tone);
  return (
    <span className={classes.join(' ')} title={title}>
      {children}
    </span>
  );
}

export function Card({ title, children, extra }: { title?: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="card">
      {title || extra ? (
        <div className="card-title" style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <span>{title}</span>
          {extra}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <span className="spin" aria-hidden />;
}
