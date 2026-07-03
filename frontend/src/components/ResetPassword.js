import React, { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';
import { safePost } from '../lib/safeXhr';
import API_URL from '../lib/apiConfig';

const API = API_URL;

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const navigate = useNavigate();
  const { setUser } = useAuth();

  const [form, setForm] = useState({ password: '', confirm: '' });
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) {
      toast.error('Invalid reset link.');
      navigate('/login');
    }
  }, [token, navigate]);

  const set = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (form.password !== form.confirm) {
      toast.error('Passwords do not match.');
      return;
    }
    if (form.password.length < 8) {
      toast.error('Password must be at least 8 characters.');
      return;
    }
    setLoading(true);

    try {
      const { data } = await safePost(`${API}/api/auth/reset-password`, {
        token,
        new_password: form.password,
      });
      if (data.token) localStorage.setItem('aegisaxiom_session_token', data.token);
      setUser(data.user);
      setDone(true);
      toast.success('Password updated! Redirecting…');
      setTimeout(() => navigate('/dashboard', { replace: true }), 1500);
    } catch (err) {
      toast.error(err.message || 'Reset failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-6">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex items-center gap-2 mb-8">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center">
            <span className="text-aegisaxiom-text font-bold text-sm">B</span>
          </div>
          <span
            className="text-aegisaxiom-text font-bold text-xl"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            AEGISAXIOM
          </span>
        </div>

        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8">
          <h1 className="text-xl font-bold text-aegisaxiom-text mb-1">Set New Password</h1>
          <p className="text-aegisaxiom-text-secondary text-sm mb-6">Enter and confirm your new password below.</p>

          {done ? (
            <div className="flex flex-col items-center gap-3 py-4">
              <div className="w-12 h-12 rounded-full bg-violet-50 dark:bg-violet-500/20 flex items-center justify-center">
                <svg
                  className="w-6 h-6 text-violet-600 dark:text-violet-400"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              </div>
              <p className="text-aegisaxiom-text-secondary text-sm text-center">
                Password updated! Signing you in…
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                  New Password
                </label>
                <input
                  name="password"
                  type="password"
                  value={form.password}
                  onChange={set}
                  className="input-field"
                  placeholder="Min. 8 characters"
                  required
                  minLength={8}
                />
              </div>
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                  Confirm Password
                </label>
                <input
                  name="confirm"
                  type="password"
                  value={form.confirm}
                  onChange={set}
                  className="input-field"
                  placeholder="Repeat password"
                  required
                  minLength={8}
                />
              </div>
              <button type="submit" disabled={loading} className="btn-primary w-full py-3 mt-2">
                {loading ? 'Updating…' : 'Set New Password'}
              </button>
            </form>
          )}

          <button
            onClick={() => navigate('/login')}
            className="mt-5 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors w-full text-center"
          >
            ← Back to Sign In
          </button>
        </div>
      </div>
    </div>
  );
}
