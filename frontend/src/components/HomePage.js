import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTheme } from '@/contexts/ThemeContext';

export default function HomePage() {
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const logoSrc = isDark ? '/logo-3.png' : '/logo-2.png';

  const scrollToFeatures = () => {
    document.getElementById('features')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Per-card accent palette — gives each feature its own identity
  const accents = {
    violet: {
      top: 'from-violet-500 via-violet-400 to-fuchsia-400',
      iconBg: 'bg-violet-50 dark:bg-violet-500/15 group-hover:bg-violet-500/25',
      iconText: 'text-violet-600 dark:text-violet-400',
      border: 'hover:border-violet-500/40',
    },
    fuchsia: {
      top: 'from-fuchsia-500 via-pink-400 to-rose-400',
      iconBg: 'bg-fuchsia-50 dark:bg-fuchsia-500/15 group-hover:bg-fuchsia-500/25',
      iconText: 'text-fuchsia-600 dark:text-fuchsia-400',
      border: 'hover:border-fuchsia-500/40',
    },
    blue: {
      top: 'from-blue-500 via-indigo-400 to-violet-400',
      iconBg: 'bg-blue-50 dark:bg-blue-500/15 group-hover:bg-blue-500/25',
      iconText: 'text-blue-600 dark:text-blue-400',
      border: 'hover:border-blue-500/40',
    },
    emerald: {
      top: 'from-emerald-500 via-teal-400 to-cyan-400',
      iconBg: 'bg-emerald-50 dark:bg-emerald-500/15 group-hover:bg-emerald-500/25',
      iconText: 'text-emerald-600 dark:text-emerald-400',
      border: 'hover:border-emerald-500/40',
    },
    amber: {
      top: 'from-amber-500 via-orange-400 to-rose-400',
      iconBg: 'bg-amber-50 dark:bg-amber-500/15 group-hover:bg-amber-500/25',
      iconText: 'text-amber-600 dark:text-amber-400',
      border: 'hover:border-amber-500/40',
    },
    rose: {
      top: 'from-rose-500 via-pink-400 to-fuchsia-400',
      iconBg: 'bg-rose-50 dark:bg-rose-500/15 group-hover:bg-rose-500/25',
      iconText: 'text-rose-600 dark:text-rose-400',
      border: 'hover:border-rose-500/40',
    },
  };

  const features = [
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
          />
        </svg>
      ),
      title: 'AI Employees',
      desc: 'Deploy AI agents that work like accountable staff.',
      accent: 'violet',
    },
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
          />
        </svg>
      ),
      title: 'Workflow Discovery',
      desc: 'Vega maps your processes and scores automation ROI.',
      accent: 'fuchsia',
    },
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"
          />
        </svg>
      ),
      title: 'App Framework',
      desc: 'Replace legacy SaaS with custom-built AI apps.',
      accent: 'blue',
    },
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
          />
        </svg>
      ),
      title: 'Governance & Security',
      desc: 'Enterprise-grade compliance, RBAC, and audit trails.',
      accent: 'emerald',
    },
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
          />
        </svg>
      ),
      title: 'Performance Dashboards',
      desc: 'Real-time KPIs for every AI employee you deploy.',
      accent: 'amber',
    },
    {
      icon: (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M12 14l9-5-9-5-9 5 9 5zm0 0l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14zm-4 6v-7.5l4-2.222"
          />
        </svg>
      ),
      title: 'ARC Certification',
      desc: 'Train and certify your team to sell AI solutions.',
      accent: 'rose',
    },
  ];

  return (
    <div
      className="min-h-screen bg-aegisaxiom-base text-aegisaxiom-text"
      style={{ fontFamily: 'Manrope, sans-serif' }}
    >
      {/* Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-aegisaxiom-base/80 backdrop-blur-md border-b border-aegisaxiom-border">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-10 h-10 shrink-0 flex items-center justify-center">
              <img
                src={logoSrc}
                alt="AEGISAXIOM"
                className={`w-[44px] h-[44px] object-contain ${
                  isDark ? 'drop-shadow-[0_0_8px_rgba(139,92,246,0.6)]' : ''
                }`}
              />
            </div>
            <span className="text-aegisaxiom-text font-bold text-lg">AEGISAXIOM</span>
            <span className="text-aegisaxiom-text-muted text-xs ml-1">by Aegis</span>
          </div>
          <button
            onClick={() => navigate('/login')}
            className="bg-violet-600 hover:bg-violet-500 text-white text-sm font-semibold px-5 py-2 rounded-lg transition-colors"
          >
            Sign In
          </button>
        </div>
      </nav>

      {/* Hero */}
      <div className="relative pt-32 pb-24 px-6 overflow-hidden">
        {/* Dot grid backdrop — adds texture without being busy */}
        <div
          className="absolute inset-0 opacity-[0.25] dark:opacity-[0.15] pointer-events-none text-aegisaxiom-text-muted"
          style={{
            backgroundImage: 'radial-gradient(circle, currentColor 1px, transparent 1px)',
            backgroundSize: '22px 22px',
            maskImage: 'radial-gradient(ellipse 70% 60% at center, transparent 0%, black 85%)',
            WebkitMaskImage:
              'radial-gradient(ellipse 70% 60% at center, transparent 0%, black 85%)',
          }}
        />

        {/* Gradient blobs */}
        <div className="absolute top-20 left-1/4 w-96 h-96 bg-violet-200/40 dark:bg-violet-600/20 rounded-full blur-3xl pointer-events-none" />
        <div className="absolute top-40 right-1/4 w-80 h-80 bg-fuchsia-200/40 dark:bg-fuchsia-600/15 rounded-full blur-3xl pointer-events-none" />

        <div className="relative max-w-4xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 bg-violet-50 dark:bg-violet-500/10 border border-violet-500/30 rounded-full px-4 py-1.5 mb-6">
            <div className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
            <span className="text-violet-700 dark:text-violet-300 text-xs font-medium">
              AI Operations · Powered by AegisAI
            </span>
          </div>

          <h1 className="text-5xl sm:text-6xl font-extrabold leading-tight mb-6">
            Your Business,
            <span className="block bg-gradient-to-r from-violet-400 via-fuchsia-400 to-violet-300 bg-clip-text text-transparent">
              AegisAI Systems.
            </span>
          </h1>

          <p className="text-aegisaxiom-text-secondary text-lg max-w-2xl mx-auto mb-10 leading-relaxed">
            AEGISAXIOM is AegisAI's AI operations platform — deploy accountable AI employees, replace
            legacy SaaS with custom apps, and govern every workflow in one place.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
            <button
              onClick={() => navigate('/login')}
              className="bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:from-violet-500 hover:to-fuchsia-500 text-white font-bold px-8 py-3.5 rounded-xl transition-all text-sm shadow-lg shadow-violet-500/25"
            >
              Sign In to AEGISAXIOM →
            </button>
            <button
              onClick={scrollToFeatures}
              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text border border-aegisaxiom-border hover:border-violet-500/40 hover:bg-aegisaxiom-surface px-8 py-3.5 rounded-xl transition-all text-sm font-semibold"
            >
              See how it works
            </button>
          </div>

          {/* Invite-only + trust strip */}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-aegisaxiom-text-muted">
            <div className="flex items-center gap-1.5">
              <svg
                className="w-3.5 h-3.5 text-violet-600 dark:text-violet-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                />
              </svg>
              <span>Invite-only access</span>
            </div>
            <span className="hidden sm:inline text-aegisaxiom-border">·</span>
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              <span>All systems operational</span>
            </div>
            <span className="hidden sm:inline text-aegisaxiom-border">·</span>
            <div className="flex items-center gap-1.5">
              <svg
                className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
                />
              </svg>
              <span>SOC 2 ready · 99.9% uptime</span>
            </div>
          </div>
        </div>
      </div>

      {/* Features */}
      <div id="features" className="max-w-6xl mx-auto px-6 pb-24 scroll-mt-24">
        <div className="text-center mb-12">
          <h2 className="text-2xl font-bold text-aegisaxiom-text mb-3">
            Everything you need to operate AI
          </h2>
          <p className="text-aegisaxiom-text-secondary text-sm">
            From workflow discovery to deployment, governance, and certification — built end-to-end.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map((f) => {
            const a = accents[f.accent];
            return (
              <div
                key={f.title}
                className={`bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl overflow-hidden ${a.border} transition-all duration-300 group hover:-translate-y-0.5 hover:shadow-lg hover:shadow-violet-500/5`}
              >
                <div
                  className={`h-0.5 bg-gradient-to-r ${a.top} opacity-60 group-hover:opacity-100 transition-opacity`}
                />
                <div className="p-6">
                  <div
                    className={`w-9 h-9 rounded-lg ${a.iconBg} ${a.iconText} flex items-center justify-center mb-4 transition-colors`}
                  >
                    {f.icon}
                  </div>
                  <h3 className="text-aegisaxiom-text font-semibold text-sm mb-2">{f.title}</h3>
                  <p className="text-aegisaxiom-text-secondary text-sm leading-relaxed">{f.desc}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-aegisaxiom-border">
        <div className="max-w-6xl mx-auto px-6 py-10">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
            {/* Brand block */}
            <div className="flex items-center gap-3">
              <img
                src={logoSrc}
                alt="AEGISAXIOM"
                className={`w-8 h-8 object-contain ${
                  isDark ? 'drop-shadow-[0_0_6px_rgba(139,92,246,0.5)]' : ''
                }`}
              />
              <div>
                <div className="text-aegisaxiom-text font-bold text-sm">AEGISAXIOM by AegisAI</div>
                <div className="text-aegisaxiom-text-muted text-xs">
                  Unified operations, AegisAI.
                </div>
              </div>
            </div>

            {/* Links */}
            <nav className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <a
                href="#features"
                onClick={(e) => {
                  e.preventDefault();
                  scrollToFeatures();
                }}
                className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
              >
                Features
              </a>
              <a
                href="mailto:support@simplimeta.io"
                className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
              >
                Support
              </a>
              <a
                href="/privacy"
                onClick={(e) => {
                  e.preventDefault();
                  navigate('/privacy');
                }}
                className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
              >
                Privacy
              </a>
              <a
                href="/terms"
                onClick={(e) => {
                  e.preventDefault();
                  navigate('/terms');
                }}
                className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
              >
                Terms
              </a>
              <span
                className="text-aegisaxiom-text-secondary inline-flex items-center gap-1.5"
                title="All systems operational"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                Status
              </span>
            </nav>
          </div>

          {/* Bottom row */}
          <div className="mt-8 pt-6 border-t border-aegisaxiom-border flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <p className="text-aegisaxiom-text-muted text-xs">
              © {new Date().getFullYear()} AegisAI. All rights reserved.
            </p>
            <p className="text-aegisaxiom-text-muted text-xs">AEGISAXIOM · v1.0</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
