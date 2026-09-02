import Link from "next/link";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <nav className="breadcrumb" style={{ marginBottom: 16 }}>
      {items.map((item, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {i > 0 && <span className="sep">/</span>}
          {item.href ? <Link href={item.href}>{item.label}</Link> : <span className="current">{item.label}</span>}
        </span>
      ))}
    </nav>
  );
}
