export default function PageHeader({ title, subtitle, badge, actions }) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        <div className="page-header-title">
          <h1>{title}</h1>
          {badge}
        </div>
        {subtitle && <p className="page-header-sub">{subtitle}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}
