import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import { ArrowRight } from 'lucide-react';
import PageContainer from './PageContainer';

export interface HubCard {
  path: string;
  title: string;
  desc: string;
  icon: LucideIcon;
  /** Tailwind text colour for the icon, e.g. 'text-cyan-400'. */
  color: string;
  /** Tailwind gradient stops for the card wash, e.g. 'from-cyan-600/15 to-blue-600/15'. */
  gradient: string;
  border: string;
  /** Optional grouping header; cards with the same group render together. */
  group?: string;
  badge?: string;
}

interface Props {
  title: string;
  intro: string;
  cards: HubCard[];
  /** Rendered above the cards — a KPI strip, a callout, anything. */
  children?: React.ReactNode;
}

/**
 * The landing page for a section: every page in it as a card, with a sentence
 * saying what question it answers.
 *
 * Sections used to drop you straight onto their busiest page, which meant the
 * only way to learn what else was in the section was to read the sidebar and
 * guess from ten two-word labels. A card says what a page is FOR, which is the
 * thing a label cannot.
 *
 * Shared by the Analytics, Transporter and ML sections so the three feel like
 * one product rather than three that grew separately.
 */
export default function SectionHub({ title, intro, cards, children }: Props) {
  const groups = cards.reduce<Record<string, HubCard[]>>((acc, c) => {
    const k = c.group ?? '';
    (acc[k] ||= []).push(c);
    return acc;
  }, {});

  return (
    <PageContainer title={title}>
      <p className="text-sm text-gray-400 -mt-4 mb-6 max-w-3xl leading-relaxed">{intro}</p>
      {children}
      {Object.entries(groups).map(([group, items]) => (
        <div key={group} className="mb-8">
          {group && (
            <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3">
              {group}
            </h2>
          )}
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {items.map(c => (
              <Link key={c.path} to={c.path}
                className={`group bg-gradient-to-br ${c.gradient} border ${c.border} rounded-xl p-4 transition-all hover:scale-[1.02] hover:border-gray-600`}>
                <div className="flex items-start justify-between gap-2 mb-2">
                  <c.icon className={`w-5 h-5 ${c.color}`} />
                  {c.badge && (
                    <span className="text-xs px-1.5 py-0.5 rounded-full bg-gray-900/70 text-gray-400 border border-gray-700">
                      {c.badge}
                    </span>
                  )}
                </div>
                <h3 className="text-sm font-semibold text-white flex items-center gap-1.5">
                  {c.title}
                  <ArrowRight className="w-3.5 h-3.5 opacity-0 -translate-x-1 transition-all group-hover:opacity-60 group-hover:translate-x-0" />
                </h3>
                <p className="text-xs text-gray-400 mt-1.5 leading-relaxed">{c.desc}</p>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </PageContainer>
  );
}
