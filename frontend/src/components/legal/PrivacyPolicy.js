import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTheme } from '@/contexts/ThemeContext';

/**
 * AEGISAXIOM — Privacy Policy
 *
 * v1.1 — 2026-05-06
 *
 * Changelog:
 *   v1.1 (2026-05-06): No content changes. Version bump to stay aligned
 *                      with Terms of Service v1.1 (which received a §14
 *                      arbitration update).
 *   v1.0 (2026-05-05): Initial draft.
 *
 * This page is a public-facing legal document. v1.x ships now per Ryan's
 * approval; counsel-reviewed v2.0 is a known follow-up over the coming
 * weeks. Update the LAST_UPDATED constant and add a brief note under
 * "Changes to This Policy" whenever material edits ship.
 *
 * Pre-deploy verification checklist (do BEFORE this page goes live on prod):
 *   1. Confirm Cloud Run prod env var EMERGENT_LLM_KEY is unset. If it is
 *      set, EITHER unset it OR add Emergent.sh to the subprocessor table
 *      below — publishing this policy while Emergent is actively called
 *      would be a misrepresentation.
 *   2. Confirm Anthropic and Google Gemini are the only AI providers
 *      reached directly from prod.
 *   3. Confirm PostHog session recording is still desired (Ryan approved
 *      keeping it 2026-05-05).
 *   4. Confirm support@simplimeta.io is monitored for privacy inquiries.
 */

const LAST_UPDATED = 'May 6, 2026';
const VERSION = '1.1';

export default function PrivacyPolicy() {
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const logoSrc = isDark ? '/logo-3.png' : '/logo-2.png';

  return (
    <div
      className="min-h-screen bg-aegisaxiom-base text-aegisaxiom-text"
      style={{ fontFamily: 'Manrope, sans-serif' }}
    >
      {/* Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-aegisaxiom-base/80 backdrop-blur-md border-b border-aegisaxiom-border">
        <div className="max-w-4xl mx-auto px-6 h-16 flex items-center justify-between">
          <button
            onClick={() => navigate('/')}
            className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
          >
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
            <span className="text-aegisaxiom-text-muted text-xs ml-1">by SimpliMeta</span>
          </button>
          <button
            onClick={() => navigate('/')}
            className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors text-sm font-medium"
          >
            ← Back to home
          </button>
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-3xl mx-auto px-6 pt-28 pb-20">
        {/* Header */}
        <header className="mb-12">
          <p className="text-violet-600 dark:text-violet-400 text-xs font-semibold tracking-wider uppercase mb-3">
            Legal
          </p>
          <h1 className="text-4xl sm:text-5xl font-extrabold leading-tight mb-4">Privacy Policy</h1>
          <p className="text-aegisaxiom-text-muted text-sm">
            Last updated: {LAST_UPDATED} · Version {VERSION}
          </p>
        </header>

        {/* Table of contents */}
        <nav
          aria-label="Table of contents"
          className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6 mb-12"
        >
          <h2 className="text-aegisaxiom-text font-semibold text-sm mb-4">On this page</h2>
          <ol className="space-y-2 text-sm">
            {[
              ['who-we-are', '1. Who we are'],
              ['scope', '2. Scope of this policy'],
              ['information-we-collect', '3. Information we collect'],
              ['how-we-use-information', '4. How we use your information'],
              ['google-user-data', '5. Google user data'],
              ['analytics', '6. Product analytics and session recording'],
              ['subprocessors', '7. Subprocessors and third parties'],
              ['data-storage', '8. Where your data is stored'],
              ['retention', '9. How long we keep your data'],
              ['security', '10. Security'],
              ['your-rights', '11. Your rights'],
              ['ccpa', '12. California residents (CCPA)'],
              ['gdpr', '13. EU and UK residents (GDPR)'],
              ['children', '14. Children'],
              ['changes', '15. Changes to this policy'],
              ['contact', '16. Contact us'],
            ].map(([id, label]) => (
              <li key={id}>
                <a
                  href={`#${id}`}
                  className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
                >
                  {label}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        {/* Body — prose styles applied to direct children for readability */}
        <article className="space-y-12 text-aegisaxiom-text-secondary leading-relaxed">
          {/* 1. Who we are */}
          <section id="who-we-are">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">1. Who we are</h2>
            <p className="mb-4">
              AEGISAXIOM is a business operations platform operated by{' '}
              <strong className="text-aegisaxiom-text">
                Metadata Engineering LLC, doing business as SimpliMeta
              </strong>{' '}
              ("SimpliMeta", "we", "our", or "us"). SimpliMeta is a limited liability company
              organized under the laws of the State of Georgia, United States.
            </p>
            <p>
              For purposes of the EU General Data Protection Regulation (GDPR) and the UK GDPR,
              SimpliMeta is the data controller of personal data processed through AEGISAXIOM.
            </p>
          </section>

          {/* 2. Scope */}
          <section id="scope">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">2. Scope of this policy</h2>
            <p className="mb-4">
              This Privacy Policy explains how SimpliMeta collects, uses, shares, and protects
              personal information in connection with the AEGISAXIOM application available at{' '}
              <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">
                aegisaxiom.simplimeta.io
              </code>{' '}
              and its related services (collectively, the "Service").
            </p>
            <p>
              This policy does not cover SimpliMeta's corporate website at{' '}
              <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">simplimeta.io</code>,
              which is governed by a separate privacy policy.
            </p>
          </section>

          {/* 3. Information we collect */}
          <section id="information-we-collect">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">3. Information we collect</h2>
            <p className="mb-4">We collect the following categories of information:</p>
            <h3 className="text-lg font-semibold text-aegisaxiom-text mt-6 mb-3">
              Information you provide directly
            </h3>
            <p className="mb-4">
              When you sign in to AEGISAXIOM using Google Single Sign-On, we receive your name, email
              address, and profile picture from Google. When you use the Service, you may also
              create or upload business records — for example, contracts, invoices, deals, contacts,
              and AI Employee configurations. The contents of those records are personal information
              you choose to provide.
            </p>
            <h3 className="text-lg font-semibold text-aegisaxiom-text mt-6 mb-3">
              Information collected automatically
            </h3>
            <p className="mb-4">
              When you use the Service, we and our analytics provider automatically collect:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                Device and browser information (browser type, operating system, screen size,
                language)
              </li>
              <li>Usage data (pages visited, features used, clicks, time spent, referrer)</li>
              <li>Session recordings of your interactions with the Service (see Section 6)</li>
              <li>IP address and approximate geographic location derived from it</li>
              <li>Authentication tokens and session identifiers</li>
            </ul>
          </section>

          {/* 4. How we use information */}
          <section id="how-we-use-information">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              4. How we use your information
            </h2>
            <p className="mb-4">We use personal information to:</p>
            <ul className="list-disc pl-6 space-y-2">
              <li>Authenticate you and provide access to your account</li>
              <li>Operate, maintain, and improve the Service</li>
              <li>Provide AI-assisted features you invoke within the Service</li>
              <li>Communicate with you about your account, security alerts, and Service updates</li>
              <li>Monitor for abuse, fraud, and security incidents</li>
              <li>Comply with legal obligations and enforce our Terms of Service</li>
              <li>Generate aggregate, de-identified analytics about how the Service is used</li>
            </ul>
          </section>

          {/* 5. Google user data */}
          <section id="google-user-data">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">5. Google user data</h2>
            <p className="mb-4">
              AEGISAXIOM uses Google Single Sign-On to authenticate users. When you sign in, Google asks
              for your consent to share the following information with us:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">openid</code> —
                confirms your identity to AEGISAXIOM
              </li>
              <li>
                <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">
                  https://www.googleapis.com/auth/userinfo.email
                </code>{' '}
                — provides your email address
              </li>
              <li>
                <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">
                  https://www.googleapis.com/auth/userinfo.profile
                </code>{' '}
                — provides your name and profile picture
              </li>
            </ul>
            <p className="mb-4">
              AEGISAXIOM does not request access to any sensitive or restricted Google API scopes. We do
              not access your Gmail, Google Drive, Google Calendar, Google Contacts, Google
              Workspace data, or any other Google service beyond the basic identity scopes listed
              above.
            </p>
            <p className="mb-4 font-semibold text-aegisaxiom-text">Limited Use disclosure.</p>
            <p>
              SimpliMeta's use and transfer of information received from Google APIs to any other
              application will adhere to the{' '}
              <a
                href="https://developers.google.com/terms/api-services-user-data-policy"
                target="_blank"
                rel="noopener noreferrer"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                Google API Services User Data Policy
              </a>
              , including the Limited Use requirements.
            </p>
          </section>

          {/* 6. Analytics */}
          <section id="analytics">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              6. Product analytics and session recording
            </h2>
            <p className="mb-4">
              We use{' '}
              <a
                href="https://posthog.com/privacy"
                target="_blank"
                rel="noopener noreferrer"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                PostHog
              </a>
              , a product analytics platform, to understand how the Service is used and to identify
              areas for improvement. PostHog automatically captures product usage events (pageviews,
              clicks, form submissions, navigation) and records video-style replays of user
              sessions, including mouse movements, clicks, and scrolls.
            </p>
            <p className="mb-4">
              PostHog is configured to associate captured events with anonymous device identifiers
              rather than your named user account, except where we expressly link a session to your
              account for a specific support or debugging purpose. Sensitive form fields (such as
              password inputs) are masked by default in session recordings.
            </p>
            <p>
              PostHog data is hosted in the United States. You can read PostHog's privacy practices
              in the link above.
            </p>
          </section>

          {/* 7. Subprocessors */}
          <section id="subprocessors">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              7. Subprocessors and third parties
            </h2>
            <p className="mb-4">
              We use the following third-party service providers ("subprocessors") to operate the
              Service. Each subprocessor is contractually obligated to protect your data and process
              it only for the purposes described.
            </p>
            <div className="overflow-x-auto -mx-2 sm:mx-0 mb-4">
              <table className="w-full text-sm border border-aegisaxiom-border rounded-lg overflow-hidden">
                <thead className="bg-aegisaxiom-elevated text-aegisaxiom-text">
                  <tr>
                    <th className="text-left px-4 py-3 font-semibold">Subprocessor</th>
                    <th className="text-left px-4 py-3 font-semibold">Purpose</th>
                    <th className="text-left px-4 py-3 font-semibold">Location</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-aegisaxiom-border">
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">Google Cloud Platform</td>
                    <td className="px-4 py-3 align-top">
                      Application hosting (Cloud Run), build pipeline (Cloud Build), authentication
                    </td>
                    <td className="px-4 py-3 align-top">United States (us-central1)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">MongoDB Atlas</td>
                    <td className="px-4 py-3 align-top">Primary database</td>
                    <td className="px-4 py-3 align-top">United States (GCP us-central1)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">Anthropic</td>
                    <td className="px-4 py-3 align-top">AI features (Claude language model)</td>
                    <td className="px-4 py-3 align-top">United States</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">Google Gemini</td>
                    <td className="px-4 py-3 align-top">Fallback AI model provider</td>
                    <td className="px-4 py-3 align-top">United States</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">n8n</td>
                    <td className="px-4 py-3 align-top">Workflow automation</td>
                    <td className="px-4 py-3 align-top">United States (where invoked)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 align-top text-aegisaxiom-text">PostHog</td>
                    <td className="px-4 py-3 align-top">Product analytics and session recording</td>
                    <td className="px-4 py-3 align-top">United States</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              We do not sell personal information to third parties, and we do not share personal
              information with third parties for their own marketing purposes.
            </p>
          </section>

          {/* 8. Data storage */}
          <section id="data-storage">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">8. Where your data is stored</h2>
            <p>
              Your data is stored on servers located in the United States, primarily in Google Cloud
              Platform's{' '}
              <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">us-central1</code>{' '}
              region (Iowa, USA). If you access the Service from outside the United States, your
              information will be transferred to and processed in the United States, which may have
              data protection laws that differ from those of your country of residence.
            </p>
          </section>

          {/* 9. Retention */}
          <section id="retention">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              9. How long we keep your data
            </h2>
            <p className="mb-4">
              We retain personal information for as long as your account is active and for a limited
              period thereafter:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                <strong className="text-aegisaxiom-text">Active accounts:</strong> Data is retained while
                your account remains active.
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Deactivated accounts:</strong> When your account
                is deactivated, we retain associated personal data for up to{' '}
                <strong className="text-aegisaxiom-text">30 days</strong> to allow for account recovery,
                after which it is deleted from our active systems.
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Deletion on request:</strong> If you request
                deletion of your data (see Section 11), we will remove it from active systems
                promptly.
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Backups and logs:</strong> Data may persist in
                encrypted backups and audit logs for a longer period as required for security, fraud
                prevention, and legal compliance, and is deleted in accordance with backup retention
                schedules.
              </li>
            </ul>
            <p>
              Aggregate, de-identified analytics data that cannot reasonably be linked back to you
              may be retained indefinitely.
            </p>
          </section>

          {/* 10. Security */}
          <section id="security">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">10. Security</h2>
            <p className="mb-4">
              We implement reasonable technical and organizational measures designed to protect
              personal information from unauthorized access, disclosure, alteration, and
              destruction. These include:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>Encryption of data in transit using TLS</li>
              <li>Encryption of data at rest in our cloud infrastructure</li>
              <li>
                Role-based access controls (RBAC) limiting access to personal data on a need-to-know
                basis
              </li>
              <li>Audit logging of administrative and security-relevant actions</li>
              <li>
                OAuth 2.0 and Single Sign-On for user authentication, with no passwords stored by
                SimpliMeta
              </li>
            </ul>
            <p>
              No system can be made entirely secure. We cannot guarantee that unauthorized access
              will never occur, but we will notify affected users and applicable authorities of
              material security incidents in accordance with applicable law.
            </p>
          </section>

          {/* 11. Your rights */}
          <section id="your-rights">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">11. Your rights</h2>
            <p className="mb-4">Subject to applicable law, you have the right to:</p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                <strong className="text-aegisaxiom-text">Access</strong> the personal information we hold
                about you
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Correct</strong> inaccurate or incomplete
                personal information
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Delete</strong> your personal information
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Export</strong> a copy of your personal
                information in a portable format
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Object</strong> to or{' '}
                <strong className="text-aegisaxiom-text">restrict</strong> certain processing activities
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Withdraw consent</strong> where processing is
                based on consent
              </li>
            </ul>
            <p>
              To exercise any of these rights, contact us at{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>
              . We will respond within the timeframes required by applicable law.
            </p>
          </section>

          {/* 12. CCPA */}
          <section id="ccpa">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              12. California residents (CCPA)
            </h2>
            <p className="mb-4">
              If you are a California resident, the California Consumer Privacy Act (CCPA), as
              amended by the California Privacy Rights Act (CPRA), gives you specific rights
              regarding your personal information:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                The right to know what personal information we collect, use, disclose, and share
                about you
              </li>
              <li>The right to delete personal information we have collected</li>
              <li>The right to correct inaccurate personal information</li>
              <li>The right to opt out of the sale or sharing of your personal information</li>
              <li>The right to limit the use and disclosure of sensitive personal information</li>
              <li>The right to non-discrimination for exercising your CCPA rights</li>
            </ul>
            <p>
              <strong className="text-aegisaxiom-text">We do not sell your personal information</strong>{' '}
              and we do not share it for cross-context behavioral advertising. To exercise your CCPA
              rights, contact us at{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>
              .
            </p>
          </section>

          {/* 13. GDPR */}
          <section id="gdpr">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              13. EU and UK residents (GDPR)
            </h2>
            <p className="mb-4">
              If you are located in the European Economic Area (EEA), the United Kingdom, or
              Switzerland, the General Data Protection Regulation (GDPR) and equivalent local laws
              give you the rights described in Section 11, along with the right to lodge a complaint
              with your local data protection authority.
            </p>
            <p className="mb-4">Our lawful bases for processing personal data are:</p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                <strong className="text-aegisaxiom-text">Contract:</strong> Processing necessary to
                provide the Service you have requested
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Legitimate interests:</strong> Operating,
                securing, and improving the Service, where those interests are not overridden by
                your rights
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Consent:</strong> Where required by law,
                processing based on the consent you provide
              </li>
              <li>
                <strong className="text-aegisaxiom-text">Legal obligation:</strong> Compliance with
                applicable legal requirements
              </li>
            </ul>
            <p>
              International transfers of personal data from the EEA, UK, or Switzerland to the
              United States rely on appropriate safeguards, including Standard Contractual Clauses
              where applicable.
            </p>
          </section>

          {/* 14. Children */}
          <section id="children">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">14. Children</h2>
            <p>
              The Service is not directed to individuals under the age of 16, and we do not
              knowingly collect personal information from children. If we learn that we have
              collected personal information from a child without verifiable parental consent, we
              will delete it promptly. If you believe a child has provided us with personal
              information, please contact us at{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>
              .
            </p>
          </section>

          {/* 15. Changes */}
          <section id="changes">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">15. Changes to this policy</h2>
            <p>
              We may update this Privacy Policy from time to time. When we make material changes, we
              will update the "Last updated" date at the top of this page and, where appropriate,
              notify you by email or through the Service. Your continued use of the Service after a
              change becomes effective constitutes acceptance of the updated policy.
            </p>
          </section>

          {/* 16. Contact */}
          <section id="contact">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">16. Contact us</h2>
            <p className="mb-4">
              If you have questions about this Privacy Policy or how we handle your personal
              information, contact us at:
            </p>
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6">
              <p className="text-aegisaxiom-text font-semibold mb-1">
                Metadata Engineering LLC dba SimpliMeta
              </p>
              <p className="text-sm">
                Email:{' '}
                <a
                  href="mailto:support@simplimeta.io"
                  className="text-violet-600 dark:text-violet-400 hover:underline"
                >
                  support@simplimeta.io
                </a>
              </p>
            </div>
          </section>
        </article>
      </main>

      {/* Footer */}
      <footer className="border-t border-aegisaxiom-border">
        <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <p className="text-aegisaxiom-text-muted text-xs">
            © {new Date().getFullYear()} SimpliMeta. All rights reserved.
          </p>
          <nav className="flex items-center gap-6 text-sm">
            <button
              onClick={() => navigate('/')}
              className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
            >
              Home
            </button>
            <button
              onClick={() => navigate('/terms')}
              className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
            >
              Terms
            </button>
          </nav>
        </div>
      </footer>
    </div>
  );
}
