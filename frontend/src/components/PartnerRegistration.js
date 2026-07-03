import React, { useState } from 'react';
import { toast } from 'sonner';
import { CheckCircle } from 'lucide-react';
import { api } from '../lib/api';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const SERVICES = [
  'Energy Solutions',
  'Communications',
  'Managed Services',
  'Hardware & Logistics',
  'Software as a Service',
  'Artificial Intelligence',
  'Cybersecurity',
  'Cloud Services',
];

export default function PartnerRegistration() {
  const [form, setForm] = useState({
    company_name: '',
    contact_name: '',
    email: '',
    phone: '',
    website: '',
    business_description: '',
    services_interested: [],
  });
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const toggle = (s) =>
    setForm((p) => ({
      ...p,
      services_interested: p.services_interested.includes(s)
        ? p.services_interested.filter((x) => x !== s)
        : [...p.services_interested, s],
    }));
  const set = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post(`${API}/api/partners/apply`, form);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail);
      }
      setSubmitted(true);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (submitted)
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-4">
        <div className="glass-card p-12 max-w-md w-full text-center animate-fadeInUp">
          <div className="w-16 h-16 rounded-full bg-emerald-50 dark:bg-emerald-600/20 border border-emerald-200 dark:border-emerald-600/30 flex items-center justify-center mx-auto mb-6">
            <CheckCircle size={32} className="text-emerald-600 dark:text-emerald-400" />
          </div>
          <h2
            className="text-2xl font-bold text-aegisaxiom-text mb-3"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Application Submitted!
          </h2>
          <p className="text-aegisaxiom-text-secondary mb-6">
            Thanks for applying to the SimpliMeta Partner Network. We'll review your application and
            contact you within 2–3 business days.
          </p>
          <a href="/login" className="btn-primary inline-block">
            Back to Sign In
          </a>
        </div>
      </div>
    );

  return (
    <div className="min-h-screen bg-aegisaxiom-base">
      <div className="relative overflow-hidden bg-gradient-to-br from-violet-950 via-aegisaxiom-surface to-[#020617] py-16 px-4">
        <img
          src="https://images.unsplash.com/photo-1758518727707-b023e285b709?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA2ODl8MHwxfHNlYXJjaHwzfHxwcm9mZXNzaW9uYWwlMjBkaXZlcnNlJTIwYnVzaW5lc3MlMjB0ZWFtJTIwbWVldGluZyUyMG1vZGVybiUyMG9mZmljZSUyMGRhcmslMjBhZXN0aGV0aWN8ZW58MHx8fHwxNzcxMzU5NTQ3fDA&ixlib=rb-4.1.0&q=85"
          alt="Team"
          className="absolute inset-0 w-full h-full object-cover opacity-10"
        />
        <div className="relative max-w-2xl mx-auto text-center">
          <div className="flex items-center justify-center gap-2 mb-6">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center">
              <span className="text-aegisaxiom-text font-bold">B</span>
            </div>
            <span
              className="text-aegisaxiom-text text-xl font-bold tracking-widest"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              SimpliMeta Partner Network
            </span>
          </div>
          <h1
            className="text-4xl md:text-5xl font-extrabold text-aegisaxiom-text mb-4"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Become a{' '}
            <span className="bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              SimpliMeta Partner
            </span>
          </h1>
          <p className="text-aegisaxiom-text-secondary text-lg mb-4">
            Join our network of technology partners and unlock co-selling opportunities, commission
            tracking, and dedicated support.
          </p>
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-4 py-12">
        <form
          onSubmit={handleSubmit}
          className="glass-card p-8 space-y-5 animate-fadeInUp"
          data-testid="partner-form"
        >
          <h2
            className="text-lg font-bold text-aegisaxiom-text"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Partner Application
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                Company Name *
              </label>
              <input
                name="company_name"
                className="input-field"
                value={form.company_name}
                onChange={set}
                required
                data-testid="company-name-input"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                Contact Name *
              </label>
              <input
                name="contact_name"
                className="input-field"
                value={form.contact_name}
                onChange={set}
                required
                data-testid="contact-name-input"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                Business Email *
              </label>
              <input
                name="email"
                type="email"
                className="input-field"
                value={form.email}
                onChange={set}
                required
                data-testid="partner-email-input"
              />
            </div>
            <div>
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">Phone</label>
              <input
                name="phone"
                className="input-field"
                value={form.phone}
                onChange={set}
                data-testid="phone-input"
              />
            </div>
            <div className="md:col-span-2">
              <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">Website</label>
              <input
                name="website"
                className="input-field"
                value={form.website}
                onChange={set}
                placeholder="https://yourcompany.com"
              />
            </div>
          </div>

          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-2 block font-medium">
              Services of Interest
            </label>
            <div className="flex flex-wrap gap-2">
              {SERVICES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggle(s)}
                  data-testid={`service-${s.replace(/\s/g, '-').toLowerCase()}`}
                  className={`text-xs px-3 py-1.5 rounded-full border transition-all ${form.services_interested.includes(s) ? 'bg-violet-600/30 border-violet-500/50 text-violet-700 dark:text-violet-300' : 'bg-aegisaxiom-elevated border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'}`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
              Business Description
            </label>
            <textarea
              name="business_description"
              rows={4}
              className="input-field resize-none"
              value={form.business_description}
              onChange={set}
              placeholder="Tell us about your company, target markets, and why you'd like to partner with SimpliMeta..."
              data-testid="description-input"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-3 text-base"
            data-testid="submit-application-btn"
          >
            {loading ? 'Submitting...' : 'Submit Application'}
          </button>
          <p className="text-center text-aegisaxiom-text-muted text-xs">
            Already have an account?{' '}
            <a href="/login" className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300">
              Sign In
            </a>
          </p>
        </form>
      </div>
    </div>
  );
}
