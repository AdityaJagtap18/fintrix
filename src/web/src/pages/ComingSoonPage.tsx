export function ComingSoonPage({ title, note }: { title: string; note: string }) {
  return (
    <div className="coming-soon-page">
      <h1>{title}</h1>
      <div className="card coming-soon-page__card">
        <p>{note}</p>
      </div>
    </div>
  );
}
