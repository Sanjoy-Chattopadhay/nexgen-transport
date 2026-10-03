/**
 * Reading Geofence Intelligence: the illustrated guide on How It Works.
 *
 * Every page of the application as a screenshot, with what each number means,
 * the columns it is read from and how it is worked out. The content is built
 * by scripts/guide (capture.mjs, collect.py, build.py) into
 * src/guide-data/, which is not committed -- the screenshots and numbers are
 * Tata Steel's operational data -- so a clone without it still builds, and the
 * page says how to generate it.
 */
import { createElement, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Library, List, Maximize2, X } from 'lucide-react';
import { Card, Note } from '../ui';

export interface GuidePart { name: string; value?: string; text?: string; cols?: string }
export interface GuideItem {
  img: string; fig: number; w: number; h: number; url: string; title: string; lead?: string;
  parts?: GuidePart[]; steps?: string[]; formula?: string; example?: string;
  data?: [string, string][]; api?: string[]; watch?: string;
}
export interface GuidePage { id: string; title: string; route: string; lead: string; api: string[]; tables: string[]; items: GuideItem[] }
interface BlockSample { kind: string; label: string; text: string; value?: string; source?: string }
interface GuideData {
  title: string; eyebrow: string; lede: string; facts: { label: string; value: string }[];
  fence: {
    viewBox: [number, number]; points: [number, number][]; north: number; title: string; caption: string;
    scale: { x0: number; x1: number; y: number; label: string };
  };
  intro: {
    title: string; lead: string; request_title: string; request: { title: string; text: string }[];
    columns_title: string; columns_lead: string; prefixes: { prefix: string; holds: string; sample: string }[];
    numbers_title: string; numbers: string; blocks_title: string; blocks: BlockSample[];
  };
  concepts: { title: string; lead: string; items: { term: string; text: string; stored: string }[] };
  pipeline: { title: string; lead: string; lanes: { title: string; sub: string; steps: { title: string; text: string; writes: string }[] }[] };
  groups: { name: string; id: string; lead: string; pages: GuidePage[] }[];
  reference: {
    title: string; lead: string; tables_title: string; tables_lead: string; endpoints_title: string; endpoints_lead: string;
    tables: { name: string; rows: { name: string; purpose: string; cols: string[] }[] }[];
    endpoints: { name: string; rows: { endpoint: string; params: string; returns: string; reads: string; used: string }[] }[];
  };
  footer: string; figures: number;
}

// Globs rather than imports: they match nothing when the guide has not been
// generated, instead of failing the build.
const DATA = import.meta.glob<GuideData>('../../guide-data/guide.json', { eager: true, import: 'default' });
const IMAGES = import.meta.glob<string>('../../guide-data/img/*.webp', { eager: true, import: 'default', query: '?url' });
const GUIDE: GuideData | null = Object.values(DATA)[0] ?? null;
export const guideAvailable = GUIDE !== null;
const imageUrl = (name: string) => IMAGES[`../../guide-data/img/${name}.webp`];
const stripTags = (s: string) => s.replace(/<[^>]+>/g, '');

/** Authored guide text: generated from the repository's own content, with a
 *  whitelist of markup (b, i, em, code, span.formula) enforced by build.py. */
function Rich({ html, as = 'span', className = '' }: { html: string; as?: 'span' | 'p' | 'div'; className?: string }) {
  return createElement(as, { className: `guide-rich ${className}`, dangerouslySetInnerHTML: { __html: html } });
}

const LABEL = 'text-xs font-semibold uppercase tracking-wider text-gray-500';
const CHIP = 'inline-block font-mono text-xs leading-snug text-gray-300 bg-gray-950 border border-gray-800 rounded-md px-2 py-1 break-all';
const SCROLL_MARGIN = 'scroll-mt-20 2xl:scroll-mt-6';

function Chips({ items }: { items: string[] }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((a, i) => <li key={i}><code className={CHIP}>{a}</code></li>)}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// One figure: the screenshot on the left, its explanation on the right
// ---------------------------------------------------------------------------

function Parts({ parts }: { parts: GuidePart[] }) {
  return (
    <dl className="border-y border-gray-800 divide-y divide-gray-800 mt-3">
      {parts.map((p, i) => (
        <div key={i} className="py-2.5">
          <dt className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <Rich html={p.name} className="text-sm font-medium text-gray-100" />
            {p.value && (
              <span className="font-mono text-xs tabular text-blue-400 bg-blue-950/40 border border-blue-900/40 rounded px-1.5 py-0.5">
                {p.value}
              </span>
            )}
          </dt>
          {(p.text || p.cols) && (
            <dd className="mt-1">
              {p.text && <Rich as="p" html={p.text} className="text-sm text-gray-400 leading-relaxed" />}
              {p.cols && (
                <p className="mt-1 text-xs leading-relaxed text-gray-500">
                  <span className={`${LABEL} mr-2`}>Source</span><code className="font-mono text-gray-400 break-words">{p.cols}</code>
                </p>
              )}
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

function Block({ label, tone, children }: { label: string; tone?: 'example' | 'watch'; children: ReactNode }) {
  const box = tone === 'example' ? 'rounded-lg border border-blue-900/50 bg-blue-950/20 p-3'
    : tone === 'watch' ? 'rounded-lg border border-amber-900/50 bg-amber-950/20 p-3' : '';
  const labelColor = tone === 'example' ? 'text-blue-400' : tone === 'watch' ? 'text-amber-400' : 'text-gray-500';
  return (
    <div className={`mt-4 ${box}`}>
      <p className={`text-xs font-semibold uppercase tracking-wider mb-1.5 ${labelColor}`}>{label}</p>
      {children}
    </div>
  );
}

function Explanation({ item }: { item: GuideItem }) {
  return (
    <div className="min-w-0">
      <h4 className="text-lg font-semibold text-white leading-snug"><Rich html={item.title} /></h4>
      {item.lead && <Rich as="p" html={item.lead} className="mt-2 text-sm text-gray-300 leading-relaxed" />}
      {item.parts && <Parts parts={item.parts} />}
      {item.steps && (
        <Block label="How it is worked out">
          <ol className="list-decimal pl-5 space-y-1.5 text-sm text-gray-400 leading-relaxed marker:text-gray-600">
            {item.steps.map((s, i) => <li key={i}><Rich html={s} /></li>)}
          </ol>
        </Block>
      )}
      {item.formula && (
        <Block label="Formula">
          <pre className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs leading-relaxed text-gray-300 font-mono whitespace-pre-wrap break-words">{item.formula}</pre>
        </Block>
      )}
      {item.example && (
        <Block label="Worked example" tone="example">
          <Rich as="p" html={item.example} className="text-sm text-gray-300 leading-relaxed" />
        </Block>
      )}
      {item.data && (
        <Block label="Database columns">
          <table className="w-full text-xs leading-relaxed">
            <tbody>
              {item.data.map(([col, meaning], i) => (
                <tr key={i} className="border-t border-gray-800 align-top">
                  <td className={`py-1.5 pr-3 ${meaning ? 'w-[46%]' : ''}`} colSpan={meaning ? 1 : 2}>
                    <code className="font-mono text-gray-300 break-words">{col}</code>
                  </td>
                  {meaning && <td className="py-1.5 text-gray-400"><Rich html={meaning} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </Block>
      )}
      {item.api && <Block label={item.api.length === 1 ? 'Endpoint' : 'Endpoints'}><Chips items={item.api} /></Block>}
      {item.watch && (
        <Block label="Read with care" tone="watch">
          <Rich as="p" html={item.watch} className="text-sm text-gray-300 leading-relaxed" />
        </Block>
      )}
    </div>
  );
}

function FigureRow({ page, item, onZoom }: { page: GuidePage; item: GuideItem; onZoom: (i: GuideItem) => void }) {
  // Short pictures stay beside a long explanation while it is read.
  const sticky = (Math.min(820, item.w) * item.h) / item.w <= 640;
  return (
    <article id={`g-fig-${item.img}`} className={`grid grid-cols-1 lg:grid-cols-12 gap-5 lg:gap-8 py-8 border-t border-gray-800 ${SCROLL_MARGIN}`}>
      <figure className={`lg:col-span-7 min-w-0 self-start ${sticky ? 'lg:sticky lg:top-16 2xl:top-6' : ''}`}>
        <button type="button" onClick={() => onZoom(item)} style={{ maxWidth: item.w }} aria-label={`Enlarge figure ${item.fig}`}
          className="group relative block w-full rounded-lg overflow-hidden border border-gray-700 bg-gray-950 cursor-zoom-in focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
          <img src={imageUrl(item.img)} width={item.w} height={item.h} loading="lazy" decoding="async"
            alt={`Screenshot of ${page.title}: ${stripTags(item.title)}`} className="block w-full h-auto" />
          <span className="absolute right-2 bottom-2 inline-flex items-center gap-1 rounded-md bg-gray-950/85 border border-gray-700 px-2 py-1 text-xs text-gray-200 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity">
            <Maximize2 className="w-3 h-3" /> Enlarge
          </span>
        </button>
        <figcaption className="mt-2.5 flex items-baseline gap-2 text-xs text-gray-500">
          <span className="font-mono text-blue-400 whitespace-nowrap">Fig. {item.fig}</span>
          <span className="min-w-0">
            <Rich html={item.title} className="text-gray-400" />
            {' · '}
            <Link to={item.url} className="font-mono break-all hover:text-blue-400" title="Open this page">{item.url}</Link>
          </span>
        </figcaption>
      </figure>
      <div className="lg:col-span-5 min-w-0"><Explanation item={item} /></div>
    </article>
  );
}

function Zoom({ item, onClose }: { item: GuideItem; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    close.current?.focus();
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow; };
  }, [onClose]);
  return (
    <div role="dialog" aria-modal="true" aria-label={`Figure ${item.fig}, enlarged`} onClick={onClose}
      className="fixed inset-0 z-[1000] bg-black/85 overflow-auto p-4 md:p-8">
      <div className="mx-auto w-fit max-w-full" onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between gap-4 bg-gray-950/95 border border-gray-800 rounded-t-lg px-3 py-2">
          <p className="text-sm text-gray-300 min-w-0 truncate">Fig. {item.fig} · {stripTags(item.title)}</p>
          <button ref={close} type="button" onClick={onClose}
            className="inline-flex items-center gap-1.5 text-xs text-gray-300 hover:text-white border border-gray-700 rounded-md px-2.5 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
            <X className="w-3.5 h-3.5" /> Close
          </button>
        </div>
        <img src={imageUrl(item.img)} width={item.w} height={item.h} alt={stripTags(item.title)}
          style={{ width: item.w }} className="block max-w-full h-auto rounded-b-lg border border-t-0 border-gray-800" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Framing sections
// ---------------------------------------------------------------------------

function Section({ id, title, lead, children }: { id: string; title: string; lead: string; children: ReactNode }) {
  return (
    <section id={id} className={`bg-gray-900 rounded-xl border border-gray-800 p-6 mt-6 ${SCROLL_MARGIN}`}>
      <h2 className="text-2xl font-bold text-white">{title}</h2>
      <Rich as="p" html={lead} className="mt-2 text-sm text-gray-400 leading-relaxed max-w-4xl" />
      {children}
    </section>
  );
}

function Hero({ g }: { g: GuideData }) {
  const f = g.fence;
  return (
    <section id="g-top" className="bg-gray-900 rounded-xl border border-gray-800 p-6 grid grid-cols-1 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6 items-center">
      <div className="min-w-0">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-blue-400">{g.eyebrow}</p>
        <h2 className="mt-2 text-3xl font-bold text-white">{g.title}</h2>
        <Rich as="p" html={g.lede} className="mt-3 text-sm text-gray-400 leading-relaxed max-w-3xl" />
        <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-3">
          {g.facts.map(x => (
            <div key={x.label}>
              <dt className={LABEL}>{x.label}</dt>
              <dd className="mt-1 font-mono text-sm tabular text-gray-200">{x.value}</dd>
            </div>
          ))}
        </dl>
      </div>
      <figure className="w-full max-w-sm justify-self-center">
        <svg viewBox={`0 0 ${f.viewBox[0]} ${f.viewBox[1]}`} role="img" aria-label={f.title} className="block w-full h-auto">
          <polygon points={f.points.map(p => p.join(',')).join(' ')} className="fill-blue-950 stroke-blue-400" strokeWidth={2.2} strokeLinejoin="round" />
          {f.points.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3.4} className="fill-gray-900 stroke-gray-300" strokeWidth={1.4} />)}
          <path d={`M${f.scale.x0} ${f.scale.y - 6} V${f.scale.y} H${f.scale.x1} V${f.scale.y - 6}`} className="fill-none stroke-gray-400" strokeWidth={1.6} />
          <text x={f.scale.x0} y={f.scale.y - 11} className="fill-gray-400 font-mono text-xs">0</text>
          <text x={f.scale.x1} y={f.scale.y - 11} textAnchor="end" className="fill-gray-400 font-mono text-xs">{f.scale.label}</text>
          <path d={`M${f.north} 58 V26`} className="fill-none stroke-gray-400" strokeWidth={1.6} />
          <path d={`M${f.north} 14 l-6 12 h12 z`} className="fill-gray-400" />
          <text x={f.north} y={76} textAnchor="middle" className="fill-gray-400 font-mono text-xs">N</text>
        </svg>
        <figcaption className="mt-2 text-xs text-gray-500 leading-relaxed">{f.caption}</figcaption>
      </figure>
    </section>
  );
}

function SampleBlock({ b }: { b: BlockSample }) {
  switch (b.kind) {
    case 'part':
      return <Parts parts={[{ name: b.label, value: b.value, text: b.text, cols: b.source }]} />;
    case 'steps':
      return <Block label={b.label}><ol className="list-decimal pl-5 text-sm text-gray-400"><li><Rich html={b.text} /></li></ol></Block>;
    case 'formula':
      return <Block label={b.label}><pre className="bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-xs text-gray-300 font-mono">{b.text}</pre></Block>;
    case 'data':
      return (
        <Block label={b.label}>
          <table className="w-full text-xs"><tbody><tr className="border-t border-gray-800">
            <td className="py-1.5 pr-3 w-[46%]"><code className="font-mono text-gray-300">{b.value}</code></td>
            <td className="py-1.5 text-gray-400"><Rich html={b.text} /></td>
          </tr></tbody></table>
        </Block>
      );
    case 'api':
      return <Block label={b.label}><Chips items={[b.value ?? '']} /><Rich as="p" html={b.text} className="mt-1.5 text-xs text-gray-500" /></Block>;
    default:
      return (
        <Block label={b.label} tone={b.kind === 'example' || b.kind === 'watch' ? b.kind : undefined}>
          <Rich as="p" html={b.text} className="text-sm text-gray-300" />
        </Block>
      );
  }
}

function HowToRead({ g }: { g: GuideData }) {
  const it = g.intro;
  const sub = 'mt-7 mb-3 text-base font-semibold text-white';
  return (
    <Section id="g-how-to-read" title={it.title} lead={it.lead}>
      <h3 className={sub}>{it.request_title}</h3>
      <ol className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {it.request.map((r, i) => (
          <li key={r.title} className="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
            <p className="text-sm font-semibold text-white">{i + 1} · {r.title}</p>
            <Rich as="p" html={r.text} className="mt-1.5 text-sm text-gray-400 leading-relaxed" />
          </li>
        ))}
      </ol>
      <h3 className={sub}>{it.columns_title}</h3>
      <Rich as="p" html={it.columns_lead} className="text-sm text-gray-400 leading-relaxed max-w-4xl" />
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className={`text-left ${LABEL}`}><th className="py-2 pr-4 font-semibold">Starts with</th><th className="py-2 pr-4 font-semibold">Holds</th><th className="py-2 font-semibold">Example from this run</th></tr>
          </thead>
          <tbody>
            {it.prefixes.map(p => (
              <tr key={p.prefix} className="border-t border-gray-800 align-top">
                <td className="py-2 pr-4"><code className="font-mono font-semibold text-blue-400">{p.prefix}</code></td>
                <td className="py-2 pr-4 text-gray-400">{p.holds}</td>
                <td className="py-2"><code className="font-mono text-xs text-gray-300 break-words">{p.sample}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className={sub}>{it.numbers_title}</h3>
      <Rich as="p" html={it.numbers} className="text-sm text-gray-400 leading-relaxed max-w-4xl" />
      <h3 className={sub}>{it.blocks_title}</h3>
      <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-6 gap-y-2 [&>li>div]:mt-0 [&>li>dl]:mt-0">
        {it.blocks.map(b => <li key={b.kind} className="min-w-0"><SampleBlock b={b} /></li>)}
      </ul>
    </Section>
  );
}

function Concepts({ g }: { g: GuideData }) {
  return (
    <Section id="g-ideas" title={g.concepts.title} lead={g.concepts.lead}>
      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-x-8">
        {g.concepts.items.map(c => (
          <div key={c.term} className="border-t border-gray-800 py-4 min-w-0">
            <h3 className="text-base font-semibold text-white">{c.term}</h3>
            <Rich as="p" html={c.text} className="mt-1.5 text-sm text-gray-400 leading-relaxed" />
            <p className="mt-2 text-xs leading-relaxed text-gray-500">
              <span className={`${LABEL} mr-2`}>Stored in</span><code className="font-mono text-gray-400 break-words">{c.stored}</code>
            </p>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Pipeline({ g }: { g: GuideData }) {
  const [batch, live] = g.pipeline.lanes;
  const lane = (l: typeof batch, span: string) => (
    <div className={`rounded-lg border border-gray-800 bg-gray-950/40 p-4 min-w-0 ${span}`}>
      <p className="text-base font-semibold text-white">{l.title}</p>
      <p className="text-xs text-gray-500 mt-0.5 mb-4">{l.sub}</p>
      <ol>
        {l.steps.map((s, i) => (
          <li key={s.title} className="relative grid grid-cols-[28px_minmax(0,1fr)] gap-3 pb-4 last:pb-0">
            {i < l.steps.length - 1 && <span aria-hidden className="absolute left-[13px] top-8 bottom-1 w-0.5 bg-gray-800" />}
            <span className="w-7 h-7 rounded-full grid place-items-center font-mono text-xs font-semibold bg-blue-950/60 text-blue-400">{i + 1}</span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-gray-100 mt-1">{s.title}</p>
              <p className="text-sm text-gray-400 leading-relaxed mt-0.5">{s.text}</p>
              <p className="mt-1 text-xs text-gray-500"><span className={`${LABEL} mr-2`}>Writes</span><code className="font-mono text-gray-400 break-words">{s.writes}</code></p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
  return (
    <Section id="g-pipeline" title={g.pipeline.title} lead={g.pipeline.lead}>
      <div className="mt-5 grid grid-cols-1 lg:grid-cols-5 gap-4 items-start">
        {lane(batch, 'lg:col-span-3')}
        {live && lane(live, 'lg:col-span-2')}
      </div>
    </Section>
  );
}

function PageSection({ page, onZoom }: { page: GuidePage; onZoom: (i: GuideItem) => void }) {
  return (
    <section id={`g-${page.id}`} className={`mt-8 ${SCROLL_MARGIN}`}>
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
        <h3 className="text-xl font-bold text-white">{page.title}</h3>
        <p className="mt-1.5 text-xs text-gray-500">
          <span className={`${LABEL} mr-2`}>Address</span>
          {page.route.startsWith('/') ? <code className="font-mono text-gray-300">{page.route}</code> : page.route}
        </p>
        <Rich as="p" html={page.lead} className="mt-3 text-sm text-gray-300 leading-relaxed max-w-5xl" />
        {(page.api.length > 0 || page.tables.length > 0) && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {page.api.length > 0 && <div><p className={`${LABEL} mb-1.5`}>Endpoints this page calls</p><Chips items={page.api} /></div>}
            {page.tables.length > 0 && <div><p className={`${LABEL} mb-1.5`}>Tables it reads</p><Chips items={page.tables} /></div>}
          </div>
        )}
      </div>
      {page.items.map(item => <FigureRow key={item.img} page={page} item={item} onZoom={onZoom} />)}
    </section>
  );
}

function Reference({ g }: { g: GuideData }) {
  const r = g.reference;
  return (
    <>
      <div className={`mt-14 pt-5 border-t-2 border-gray-700 ${SCROLL_MARGIN}`} id="g-reference">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-blue-400">Look-up</p>
        <h2 className="text-3xl font-bold text-white mt-1">{r.title}</h2>
        <p className="text-sm text-gray-400 mt-1">{r.lead}</p>
      </div>
      <Section id="g-tables" title={r.tables_title} lead={r.tables_lead}>
        {r.tables.map(t => (
          <div key={t.name} className="mt-6">
            <p className="text-base font-semibold text-white mb-2">{t.name}</p>
            {t.rows.map(row => (
              <div key={row.name} className="grid grid-cols-1 md:grid-cols-[200px_minmax(0,1fr)] gap-x-6 gap-y-1 border-t border-gray-800 py-3">
                <code className="font-mono text-sm font-semibold text-gray-100 break-words">{row.name}</code>
                <div className="min-w-0">
                  <Rich as="p" html={row.purpose} className="text-sm text-gray-400" />
                  <p className="mt-2 flex flex-wrap gap-1">
                    {row.cols.map(c => <span key={c} className="font-mono text-xs text-gray-300 bg-gray-950 border border-gray-800 rounded px-1.5 py-0.5 break-words">{c}</span>)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        ))}
      </Section>
      <Section id="g-endpoints" title={r.endpoints_title} lead={r.endpoints_lead}>
        <div className="mt-5 overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full min-w-[980px] text-sm">
            <thead>
              <tr className={`text-left ${LABEL} border-b border-gray-700`}>
                {['Endpoint', 'Parameters', 'What it returns', 'Tables it reads', 'Used on'].map(h => <th key={h} className="px-3 py-2.5 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {r.endpoints.map(grp => [
                <tr key={grp.name}><td colSpan={5} className={`px-3 py-2 bg-gray-950/60 ${LABEL} text-gray-400`}>{grp.name}</td></tr>,
                ...grp.rows.map(row => (
                  <tr key={row.endpoint} className="border-t border-gray-800 align-top">
                    <td className="px-3 py-2 min-w-[240px]"><code className="font-mono text-xs text-gray-100 break-words">{row.endpoint}</code></td>
                    <td className="px-3 py-2 max-w-[240px] font-mono text-xs text-gray-400 break-words">{row.params || '—'}</td>
                    <td className="px-3 py-2 text-gray-400"><Rich html={row.returns} /></td>
                    <td className="px-3 py-2 max-w-[240px] font-mono text-xs text-gray-400 break-words">{row.reads}</td>
                    <td className="px-3 py-2 text-gray-400">{row.used}</td>
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Contents and scrolling
// ---------------------------------------------------------------------------

function useActiveSection(ids: string[]) {
  const [active, setActive] = useState(ids[0]);
  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      const line = window.innerHeight * 0.3;
      let current = ids[0];
      for (const id of ids) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.getBoundingClientRect().top > line) break;
        current = id;
      }
      setActive(current);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [ids]);
  return active;
}

function scrollToSection(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
  window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}#${id}`);
}

export function GuideView() {
  const g = GUIDE;
  const [zoom, setZoom] = useState<GuideItem | null>(null);
  const closeZoom = useCallback(() => setZoom(null), []);
  const toc = useMemo(() => g ? [
    { label: 'Start here', entries: [
      { id: 'g-how-to-read', label: g.intro.title }, { id: 'g-ideas', label: g.concepts.title }, { id: 'g-pipeline', label: g.pipeline.title },
    ] },
    ...g.groups.map(grp => ({ label: grp.name, entries: grp.pages.map(p => ({ id: `g-${p.id}`, label: p.title })) })),
    { label: g.reference.title, entries: [{ id: 'g-tables', label: g.reference.tables_title }, { id: 'g-endpoints', label: g.reference.endpoints_title }] },
  ] : [], [g]);
  const ids = useMemo(() => toc.flatMap(t => t.entries.map(e => e.id)), [toc]);
  const active = useActiveSection(ids);
  const tocBox = useRef<HTMLDivElement>(null);

  // Arriving on /method#g-trip lands on that section.
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, []);
  // Keep the current entry visible in a long contents list. The list is
  // scrolled directly: scrollIntoView would interrupt a smooth page scroll.
  useEffect(() => {
    const box = tocBox.current;
    const link = box?.querySelector<HTMLElement>(`[data-id="${active}"]`);
    if (!box || !link) return;
    if (link.offsetTop < box.scrollTop || link.offsetTop + link.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTop = link.offsetTop - box.clientHeight / 2;
    }
  }, [active]);

  if (!g) return <GuideMissing />;
  return (
    <div className="flex gap-8">
      <nav aria-label="Guide contents" className="hidden 2xl:block w-56 shrink-0">
        <div ref={tocBox} className="sticky top-6 max-h-[calc(100vh-3rem)] overflow-y-auto pr-2 pb-4">
          <p className={`${LABEL} mb-3`}>Contents</p>
          {toc.map(t => (
            <div key={t.label} className="mb-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-600 mb-1">{t.label}</p>
              <ul>
                {t.entries.map(e => (
                  <li key={e.id}>
                    <a href={`#${e.id}`} data-id={e.id} onClick={ev => { ev.preventDefault(); scrollToSection(e.id); }}
                      aria-current={active === e.id ? 'true' : undefined}
                      className={`block border-l py-1 pl-3 text-xs ${active === e.id
                        ? 'border-blue-400 text-blue-400 font-medium' : 'border-gray-800 text-gray-400 hover:text-gray-200'}`}>
                      {e.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </nav>

      <div className="min-w-0 flex-1">
        <div className="2xl:hidden sticky top-0 z-20 -mx-6 px-6 py-2.5 mb-6 bg-gray-950 border-b border-gray-800 flex items-center gap-3">
          <List className="w-4 h-4 text-gray-500 shrink-0" />
          <label htmlFor="guide-jump" className="text-xs text-gray-500 shrink-0">Jump to</label>
          <select id="guide-jump" value={active} onChange={e => scrollToSection(e.target.value)}
            className="min-w-0 max-w-full bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-3 py-1.5 focus:outline-none focus:border-blue-500">
            {toc.map(t => (
              <optgroup key={t.label} label={t.label}>
                {t.entries.map(e => <option key={e.id} value={e.id}>{e.label}</option>)}
              </optgroup>
            ))}
          </select>
        </div>

        <Hero g={g} />
        <HowToRead g={g} />
        <Concepts g={g} />
        <Pipeline g={g} />
        {g.groups.map(grp => (
          <div key={grp.id}>
            <div id={`g-${grp.id}`} className={`mt-14 pt-5 border-t-2 border-gray-700 ${SCROLL_MARGIN}`}>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-blue-400">Sidebar section</p>
              <h2 className="text-3xl font-bold text-white mt-1">{grp.name}</h2>
              <p className="text-sm text-gray-400 mt-1 max-w-3xl">{grp.lead}</p>
            </div>
            {grp.pages.map(p => <PageSection key={p.id} page={p} onZoom={setZoom} />)}
          </div>
        ))}
        <Reference g={g} />
        <Rich as="p" html={g.footer} className="mt-10 pt-5 border-t border-gray-800 text-xs text-gray-500 leading-relaxed max-w-5xl" />
      </div>

      {zoom && <Zoom item={zoom} onClose={closeZoom} />}
    </div>
  );
}

export function GuideMissing() {
  return (
    <Card title="Illustrated guide" icon={Library} className="max-w-4xl">
      <Note tone="warn" title="Not generated on this machine.">
        The guide's screenshots and worked examples are Tata Steel's operational data, so they are not kept in the repository.
        With the application running, generate them and rebuild the interface:
      </Note>
      <pre className="mt-3 bg-gray-950 border border-gray-800 rounded-lg p-3 text-xs text-gray-300 overflow-x-auto">{`node scripts/guide/capture.mjs
python scripts/guide/collect.py
python scripts/guide/build.py
npm --prefix frontend run build`}</pre>
    </Card>
  );
}
