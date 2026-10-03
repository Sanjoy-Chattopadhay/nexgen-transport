/**
 * Page wrapper with an optional heading.
 *
 * The heading is only rendered when `title` is non-empty. Most top-level pages
 * pass nothing: the sidebar already highlights where you are, so a banner
 * repeating "Dashboard" or "Vehicles" is a row of dead vertical space above
 * the content. Pages whose title carries real information -- "Trip 28844567",
 * the analytics section headings -- still pass one.
 *
 * Previously the h1 rendered unconditionally, so the ~18 pages already passing
 * an empty string still paid for its bottom margin.
 */
export default function PageContainer({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      {title ? <h1 className="text-2xl font-bold text-white mb-6">{title}</h1> : null}
      {children}
    </div>
  );
}
