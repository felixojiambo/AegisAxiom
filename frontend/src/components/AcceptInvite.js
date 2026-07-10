import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';
import { safePost } from '../lib/safeXhr';
import API_URL from '../lib/apiConfig';

const API = API_URL;

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const [info, setInfo] = useState(null);
  const [form, setForm] = useState({ password: '', confirmPassword: '' });
  const [loading, setLoading] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const token = params.get('token');

  useEffect(() => {
    if (!token) {
      toast.error('Invalid invite link');
      navigate('/login');
      return;
    }
    safePost(`${API}/api/auth/validate-invite`, { token, new_password: 'validate_only' })
      .then(({ data }) => setInfo(data))
      .catch(() => {
        toast.error('Invite expired or invalid');
        navigate('/login');
      });
  }, [token, navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (form.password !== form.confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }
    if (form.password.length < 8) {
      toast.error('Password must be at least 8 characters');
      return;
    }
    setLoading(true);
    try {
      const { data } = await safePost(`${API}/api/auth/accept-invite`, {
        token,
        new_password: form.password,
      });
      if (data.token) localStorage.setItem('aegisaxiom_session_token', data.token);
      setUser(data.user);
      toast.success('Account activated! Welcome to AEGISAXIOM.');
      navigate('/dashboard');
    } catch (err) {
      toast.error(err.message || 'Activation failed');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSSO = () => {
    if (!process.env.REACT_APP_GOOGLE_CLIENT_ID) {
      toast.error('Google Sign-In is not configured');
      return;
    }
    sessionStorage.setItem('aegisaxiom_invite_token', token);
    const nonce = crypto.randomUUID();
    sessionStorage.setItem('aegisaxiom_oauth_state', `invite:${nonce}`);
    const redirectUri = `${window.location.origin}/auth/google/callback`;
    const scopes = [
      'openid',
      'email',
      'profile',
      'https://www.googleapis.com/auth/documents',
      'https://www.googleapis.com/auth/drive.file',
    ].join(' ');
    const params = new URLSearchParams({
      client_id: process.env.REACT_APP_GOOGLE_CLIENT_ID,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope: scopes,
      state: `invite:${nonce}`,
      access_type: 'offline',
      prompt: 'consent',
    });
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
  };

  if (!info)
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );

  return (
    <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-4">
      <div className="w-full max-w-md glass-card p-8 animate-fadeInUp">
        <div className="flex items-center gap-2 mb-6">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center">
            <span className="text-aegisaxiom-text font-bold text-sm">B</span>
          </div>
          <span
            className="text-aegisaxiom-text font-bold text-lg"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            AEGISAXIOM
          </span>
        </div>
        <h1
          className="text-xl font-bold text-aegisaxiom-text mb-1"
          style={{ fontFamily: 'Manrope, sans-serif' }}
        >
          Activate Your Account
        </h1>
        <p className="text-aegisaxiom-text-secondary text-sm mb-6">
          Hi <strong className="text-aegisaxiom-text">{info.name}</strong>! You've been invited as a{' '}
          <span className="text-violet-600 dark:text-violet-400">{info.role?.replace('_', ' ')}</span>.
        </p>

        <button
          onClick={handleGoogleSSO}
          data-testid="invite-google-sso-btn"
          className="w-full flex items-center justify-center gap-3 bg-aegisaxiom-surface hover:bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text rounded-lg px-4 py-3 font-medium transition-all mb-5 text-sm"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24">
            <path
              fill="#4285F4"
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
            />
            <path
              fill="#34A853"
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
            />
            <path
              fill="#FBBC05"
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
            />
            <path
              fill="#EA4335"
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
            />
          </svg>
          Continue with Google
        </button>

        <div className="flex items-center gap-4 mb-5">
          <div className="flex-1 border-t border-aegisaxiom-border" />
          <span className="text-aegisaxiom-text-muted text-xs">or set a password</span>
          <div className="flex-1 border-t border-aegisaxiom-border" />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4" data-testid="accept-invite-form">
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">New Password</label>
            <input
              type="password"
              className="input-field"
              placeholder="At least 8 characters"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              data-testid="new-password-input"
            />
          </div>
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
              Confirm Password
            </label>
            <input
              type="password"
              className="input-field"
              placeholder="Repeat password"
              value={form.confirmPassword}
              onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })}
              required
              data-testid="confirm-new-password-input"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-3"
            data-testid="activate-btn"
          >
            {loading ? 'Activating...' : 'Activate with Password'}
          </button>
        </form>
      </div>
    </div>
  );
}
