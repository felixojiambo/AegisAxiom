import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTheme } from '@/contexts/ThemeContext';

/**
 * AEGISAXIOM — Terms of Service
 *
 * v1.1 — 2026-05-06
 *
 * Changelog:
 *   v1.1 (2026-05-06): Section 14 replaced with Ryan-drafted binding-
 *                      arbitration text. AAA Commercial Rules, Atlanta
 *                      Georgia seat, class-action waiver, IP injunctive-
 *                      relief carveout, 30-day opt-out window.
 *   v1.0 (2026-05-05): Initial draft.
 *
 * Pre-deploy verification (do BEFORE this page goes live on prod):
 *   1. Confirm Cloud Run prod env var EMERGENT_LLM_KEY is unset (matches
 *      the Privacy Policy subprocessor list which omits Emergent).
 *   2. Confirm support@simplimeta.io is monitored — used in §4 and §17.
 *
 * This page is a public-facing legal document. v1.x ships now per Ryan's
 * approval; counsel-reviewed v2.0 is a known follow-up over the coming
 * weeks. Update the LAST_UPDATED constant and add a note under "Changes
 * to These Terms" whenever material edits ship.
 */

const LAST_UPDATED = 'May 6, 2026';
const VERSION = '1.1';

export default function TermsOfService() {
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
          <h1 className="text-4xl sm:text-5xl font-extrabold leading-tight mb-4">
            Terms of Service
          </h1>
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
              ['acceptance', '1. Acceptance of these Terms'],
              ['service', '2. Description of the Service'],
              ['eligibility', '3. Eligibility and account access'],
              ['account', '4. Your account'],
              ['acceptable-use', '5. Acceptable use'],
              ['ip', '6. Intellectual property'],
              ['user-content', '7. Your content'],
              ['third-party', '8. Third-party services'],
              ['beta', '9. Beta service and availability'],
              ['warranties', '10. Disclaimer of warranties'],
              ['liability', '11. Limitation of liability'],
              ['indemnification', '12. Indemnification'],
              ['termination', '13. Termination'],
              ['governing-law', '14. Governing law and dispute resolution'],
              ['changes', '15. Changes to these Terms'],
              ['miscellaneous', '16. Miscellaneous'],
              ['contact', '17. Contact us'],
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

        {/* Body */}
        <article className="space-y-12 text-aegisaxiom-text-secondary leading-relaxed">
          {/* 1. Acceptance */}
          <section id="acceptance">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">1. Acceptance of these Terms</h2>
            <p className="mb-4">
              These Terms of Service (the "Terms") form a binding agreement between you and{' '}
              <strong className="text-aegisaxiom-text">
                Metadata Engineering LLC, doing business as SimpliMeta
              </strong>{' '}
              ("SimpliMeta", "we", "our", or "us"), a limited liability company organized under the
              laws of the State of Georgia, United States.
            </p>
            <p>
              By accessing or using the AEGISAXIOM application available at{' '}
              <code className="bg-aegisaxiom-elevated px-1.5 py-0.5 rounded text-xs">
                aegisaxiom.simplimeta.io
              </code>{' '}
              and any related services (collectively, the "Service"), you agree to be bound by these
              Terms and by our{' '}
              <button
                onClick={() => navigate('/privacy')}
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                Privacy Policy
              </button>
              . If you do not agree, do not access or use the Service.
            </p>
          </section>

          {/* 2. Service */}
          <section id="service">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              2. Description of the Service
            </h2>
            <p>
              AEGISAXIOM is a business operations platform that enables organizations to deploy AI
              employees, discover and document workflows, build internal applications, and govern
              access to operational data and AI tooling. The specific features available to you
              depend on the role and permissions assigned to your account.
            </p>
          </section>

          {/* 3. Eligibility */}
          <section id="eligibility">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              3. Eligibility and account access
            </h2>
            <p className="mb-4">
              The Service is currently available on an invite-only basis. To use the Service, you
              must:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>Be at least 18 years of age</li>
              <li>Have legal capacity to enter into these Terms</li>
              <li>
                Have received an invitation from SimpliMeta or an authorized administrator of an
                organization using the Service
              </li>
              <li>Comply with all applicable laws and regulations in your use of the Service</li>
            </ul>
            <p>
              SimpliMeta may, in its sole discretion, decline to provide the Service to any person
              or entity, and may revoke access at any time.
            </p>
          </section>

          {/* 4. Account */}
          <section id="account">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">4. Your account</h2>
            <p className="mb-4">
              You are responsible for maintaining the confidentiality of your account credentials
              and for all activity that occurs under your account. You agree to notify SimpliMeta
              immediately at{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>{' '}
              of any unauthorized access to or use of your account.
            </p>
            <p>
              You agree to provide accurate, current, and complete information when creating your
              account and to update such information as needed to keep it accurate.
            </p>
          </section>

          {/* 5. Acceptable Use */}
          <section id="acceptable-use">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">5. Acceptable use</h2>
            <p className="mb-4">You agree not to use the Service to:</p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>
                Violate any applicable law, regulation, or third-party right, including intellectual
                property and privacy rights
              </li>
              <li>
                Upload, transmit, or store content that is unlawful, harmful, fraudulent,
                defamatory, obscene, or otherwise objectionable
              </li>
              <li>
                Attempt to gain unauthorized access to the Service, other users' accounts, or any
                underlying systems
              </li>
              <li>
                Interfere with or disrupt the integrity, security, or performance of the Service,
                including by introducing malware or conducting denial-of- service attacks
              </li>
              <li>
                Reverse engineer, decompile, disassemble, or otherwise attempt to derive the source
                code of the Service, except to the extent expressly permitted by applicable law
              </li>
              <li>
                Scrape, harvest, or otherwise collect data from the Service through automated means
                without our prior written consent
              </li>
              <li>
                Use the Service or AI features to generate content that is illegal, misleading,
                abusive, or designed to deceive or manipulate others
              </li>
              <li>
                Resell, sublicense, or otherwise commercially exploit the Service without our prior
                written agreement
              </li>
            </ul>
            <p>
              SimpliMeta reserves the right to investigate and take appropriate action, including
              suspending or terminating your account, in response to suspected violations of this
              Section.
            </p>
          </section>

          {/* 6. IP */}
          <section id="ip">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">6. Intellectual property</h2>
            <p className="mb-4">
              The Service, including all software, designs, text, graphics, logos, and other content
              provided by SimpliMeta, is the exclusive property of SimpliMeta or its licensors and
              is protected by copyright, trademark, and other intellectual property laws.
            </p>
            <p>
              Subject to your compliance with these Terms, SimpliMeta grants you a limited,
              non-exclusive, non-transferable, revocable license to access and use the Service for
              your internal business purposes during the term of these Terms. No other rights are
              granted by implication, estoppel, or otherwise.
            </p>
          </section>

          {/* 7. User Content */}
          <section id="user-content">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">7. Your content</h2>
            <p className="mb-4">
              You retain all rights, title, and interest in and to the data and content you submit
              to the Service ("Your Content"). You grant SimpliMeta a limited, worldwide,
              non-exclusive, royalty-free license to host, store, process, and display Your Content
              solely for the purpose of providing and improving the Service to you.
            </p>
            <p>
              You represent and warrant that you have all rights necessary to grant this license,
              and that Your Content does not infringe or violate the rights of any third party or
              any applicable law.
            </p>
          </section>

          {/* 8. Third-party */}
          <section id="third-party">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">8. Third-party services</h2>
            <p>
              The Service integrates with third-party services, including authentication and AI
              providers. Your use of these third-party services may be subject to their own terms
              and privacy practices. SimpliMeta is not responsible for the availability, content, or
              practices of third-party services, and your relationship with any third-party provider
              is solely between you and that provider.
            </p>
          </section>

          {/* 9. Beta */}
          <section id="beta">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              9. Beta service and availability
            </h2>
            <p className="mb-4">
              The Service is currently provided as a beta release. You acknowledge that:
            </p>
            <ul className="list-disc pl-6 space-y-2 mb-4">
              <li>The Service may contain bugs, errors, and incomplete functionality</li>
              <li>Features may change, be added, or be removed without prior notice</li>
              <li>
                The Service is not guaranteed to be available without interruption and may be
                unavailable for maintenance, upgrades, or other reasons
              </li>
              <li>
                Your data, while protected in accordance with our Privacy Policy and security
                practices, should not be considered an exclusive system of record during the beta
                period
              </li>
            </ul>
            <p>
              SimpliMeta does not offer a service-level agreement during the beta period and
              provides no commitment regarding uptime, response time, or support availability.
            </p>
          </section>

          {/* 10. Warranties */}
          <section id="warranties">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">10. Disclaimer of warranties</h2>
            <p className="mb-4 font-semibold text-aegisaxiom-text">
              THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND,
              EXPRESS OR IMPLIED.
            </p>
            <p>
              To the fullest extent permitted by applicable law, SimpliMeta disclaims all
              warranties, including without limitation implied warranties of merchantability,
              fitness for a particular purpose, non-infringement, accuracy, and quiet enjoyment.
              SimpliMeta does not warrant that the Service will be uninterrupted, error-free,
              secure, or free from harmful components, or that any errors will be corrected. You
              assume all risk and responsibility for your use of the Service and any reliance you
              place on it.
            </p>
          </section>

          {/* 11. Liability */}
          <section id="liability">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">11. Limitation of liability</h2>
            <p className="mb-4 font-semibold text-aegisaxiom-text">
              TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT WILL SIMPLIMETA, ITS
              AFFILIATES, OFFICERS, DIRECTORS, EMPLOYEES, OR AGENTS BE LIABLE FOR ANY INDIRECT,
              INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, INCLUDING WITHOUT
              LIMITATION LOST PROFITS, LOST REVENUES, LOST DATA, OR BUSINESS INTERRUPTION, ARISING
              OUT OF OR RELATING TO THESE TERMS OR YOUR USE OF THE SERVICE, WHETHER BASED ON
              CONTRACT, TORT, STRICT LIABILITY, OR OTHERWISE, AND REGARDLESS OF WHETHER SIMPLIMETA
              HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
            </p>
            <p>
              SimpliMeta's total aggregate liability arising out of or relating to these Terms or
              the Service will not exceed one hundred U.S. dollars (US $100) or the amount you have
              paid to SimpliMeta in the twelve months immediately preceding the event giving rise to
              the claim, whichever is greater. Some jurisdictions do not allow the exclusion or
              limitation of certain damages, so portions of this Section may not apply to you.
            </p>
          </section>

          {/* 12. Indemnification */}
          <section id="indemnification">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">12. Indemnification</h2>
            <p>
              You agree to indemnify, defend, and hold harmless SimpliMeta and its affiliates,
              officers, directors, employees, and agents from and against any claims, liabilities,
              damages, losses, and expenses (including reasonable attorneys' fees) arising out of or
              in any way connected with: (a) Your Content; (b) your use of the Service in violation
              of these Terms or applicable law; or (c) your violation of any third-party right,
              including intellectual property or privacy rights.
            </p>
          </section>

          {/* 13. Termination */}
          <section id="termination">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">13. Termination</h2>
            <p className="mb-4">
              You may stop using the Service at any time. SimpliMeta may suspend or terminate your
              access to the Service at any time, with or without notice, for any reason, including
              suspected violations of these Terms.
            </p>
            <p>
              Upon termination, your right to access and use the Service will immediately cease.
              Sections of these Terms that by their nature should survive termination — including
              without limitation Sections 6, 10, 11, 12, 14, and 17 — will survive.
            </p>
          </section>

          {/* 14. Governing Law and Dispute Resolution — REPLACED v1.1 */}
          <section id="governing-law">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">
              14. Governing law and dispute resolution
            </h2>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">A. Governing Law.</strong> These Terms are governed
              by and construed in accordance with the laws of the State of Georgia, United States,
              without regard to its conflict of laws principles.
            </p>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">B. Informal Dispute Resolution.</strong> Before
              filing a claim, you agree to try to resolve the dispute informally by contacting{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>
              . We will try to resolve the dispute informally by contacting you via email. If a
              dispute is not resolved within 30 days of submission, either you or SimpliMeta may
              bring a formal proceeding.
            </p>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">C. Binding Arbitration.</strong> You and SimpliMeta
              agree that any dispute, claim, or controversy arising out of or relating to these
              Terms or the Service will be resolved through final and binding arbitration, rather
              than in court, except that you may assert claims in small claims court if your claims
              qualify. The Federal Arbitration Act governs the interpretation and enforcement of
              this dispute resolution provision.
            </p>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">D. Arbitration Rules and Forum.</strong> The
              arbitration will be conducted by the American Arbitration Association (AAA) under its
              Commercial Arbitration Rules. The arbitration will be conducted in the State of
              Georgia, unless you and SimpliMeta agree otherwise.
            </p>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">E. Class Action Waiver.</strong>{' '}
              <span className="font-semibold text-aegisaxiom-text">
                YOU AND SIMPLIMETA AGREE THAT EACH MAY BRING CLAIMS AGAINST THE OTHER ONLY IN YOUR
                OR ITS INDIVIDUAL CAPACITY, AND NOT AS A PLAINTIFF OR CLASS MEMBER IN ANY PURPORTED
                CLASS OR REPRESENTATIVE PROCEEDING.
              </span>{' '}
              The arbitrator may not consolidate more than one person's claims, and may not
              otherwise preside over any form of a representative or class proceeding.
            </p>

            <p className="mb-4">
              <strong className="text-aegisaxiom-text">F. Exceptions.</strong> Notwithstanding the above,
              either party may bring a lawsuit solely for injunctive relief to stop unauthorized use
              or abuse of the Service, or intellectual property infringement (for example,
              trademark, trade secret, copyright, or patent rights) without first engaging in
              arbitration or the informal dispute resolution process described above.
            </p>

            <p>
              <strong className="text-aegisaxiom-text">G. Right to Opt-Out.</strong> You can decline this
              agreement to arbitrate by emailing{' '}
              <a
                href="mailto:support@simplimeta.io"
                className="text-violet-600 dark:text-violet-400 hover:underline"
              >
                support@simplimeta.io
              </a>{' '}
              within 30 days of first accepting these Terms. Include your name, the email address
              associated with your account, and a clear statement that you want to opt out of this
              arbitration agreement.
            </p>
          </section>

          {/* 15. Changes */}
          <section id="changes">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">15. Changes to these Terms</h2>
            <p>
              SimpliMeta may modify these Terms from time to time. When we make material changes, we
              will update the "Last updated" date at the top of this page and, where appropriate,
              notify you by email or through the Service. Your continued use of the Service after a
              change becomes effective constitutes your acceptance of the updated Terms. If you do
              not agree with the updated Terms, you must stop using the Service.
            </p>
          </section>

          {/* 16. Misc */}
          <section id="miscellaneous">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">16. Miscellaneous</h2>
            <p className="mb-4">
              These Terms, together with the Privacy Policy, constitute the entire agreement between
              you and SimpliMeta regarding the Service and supersede any prior agreements on the
              subject matter.
            </p>
            <p className="mb-4">
              If any provision of these Terms is held to be invalid or unenforceable, that provision
              will be modified to the minimum extent necessary to make it enforceable, and the
              remaining provisions will remain in full force and effect.
            </p>
            <p>
              SimpliMeta's failure to enforce any right or provision of these Terms will not be
              deemed a waiver of that right or provision. You may not assign or transfer these Terms
              without SimpliMeta's prior written consent. SimpliMeta may assign these Terms without
              restriction.
            </p>
          </section>

          {/* 17. Contact */}
          <section id="contact">
            <h2 className="text-2xl font-bold text-aegisaxiom-text mb-4">17. Contact us</h2>
            <p className="mb-4">If you have questions about these Terms, contact us at:</p>
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
              onClick={() => navigate('/privacy')}
              className="text-aegisaxiom-text-secondary hover:text-violet-500 dark:hover:text-violet-400 transition-colors"
            >
              Privacy
            </button>
          </nav>
        </div>
      </footer>
    </div>
  );
}
