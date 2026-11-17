import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { useTheme } from '@/contexts/ThemeContext';
import { toast } from 'sonner';
import { safePost } from '@/lib/safeXhr';
import getRoleHome from '../lib/roleHome';
import API_URL from '../lib/apiConfig';

const API = API_URL;

export default function AuthPage() {
  const [form, setForm] = useState({ email: '', password: '' });
  const [loading, setLoading] = useState(false);
  const [showForgot, setShowForgot] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const [forgotLoading, setForgotLoading] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [capsLockOn, setCapsLockOn] = useState(false);
  const { login } = useAuth();
  const { isDark } = useTheme();
  const logoSrc = isDark ? '/logo-3.png' : '/logo-2.png';
  const navigate = useNavigate();
  const location = useLocation();
  const rawFrom = location.state?.from?.pathname;
  const from = !rawFrom || rawFrom === '/' ? '/dashboard' : rawFrom;

  const set = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handlePasswordKey = (e) => {
    if (typeof e.getModifierState === 'function') {
      setCapsLockOn(e.getModifierState('CapsLock'));
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const data = await login(form.email, form.password);
      const roleHome = getRoleHome(data?.user);
      // Honour the original destination unless it was just '/' or '/dashboard' (generic)
      const target = !rawFrom || rawFrom === '/' || rawFrom === '/dashboard' ? roleHome : rawFrom;
      navigate(target, { replace: true });
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = () => {
    if (!process.env.REACT_APP_GOOGLE_CLIENT_ID) {
      toast.error('Google Sign-In is not configured');
      return;
    }
    const nonce = crypto.randomUUID();
    sessionStorage.setItem('aegisaxiom_oauth_state', `login:${nonce}`);
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
      state: `login:${nonce}`,
      access_type: 'offline',
      prompt: 'consent',
    });
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
  };

  const handleForgotSubmit = async (e) => {
    e.preventDefault();
    setForgotLoading(true);
    try {
      await safePost(`${API}/api/auth/forgot-password`, { email: forgotEmail });
      setForgotSent(true);
    } catch (err) {
      toast.error(err.message || 'Request failed');
    } finally {
      setForgotLoading(false);
    }
  };

  const closeForgot = () => {
    setShowForgot(false);
    setForgotSent(false);
    setForgotEmail('');
  };

  const inputCls = 'input-field';

  return (
    <div className="min-h-screen bg-aegisaxiom-base flex flex-col lg:flex-row">
      {/* Forgot Password Modal */}
      {showForgot && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8 w-full max-w-md shadow-2xl">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-lg font-bold text-aegisaxiom-text">Reset Password</h2>
                <p className="text-aegisaxiom-text-secondary text-sm mt-0.5">
                  We'll send a reset link to your email.
                </p>
              </div>
              <button
                onClick={closeForgot}
                className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors p-1"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>

            {forgotSent ? (
              <div className="text-center py-4">
                <div className="w-14 h-14 rounded-full bg-violet-50 dark:bg-violet-500/20 flex items-center justify-center mx-auto mb-4">
                  <svg
                    className="w-7 h-7 text-violet-600 dark:text-violet-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                    />
                  </svg>
                </div>
                <p className="text-aegisaxiom-text font-medium mb-2">Check your email</p>
                <p className="text-aegisaxiom-text-secondary text-sm mb-6">
                  If <span className="text-violet-600 dark:text-violet-400">{forgotEmail}</span> is
                  registered, a reset link has been sent. Check your inbox and spam folder.
                </p>
                <button onClick={closeForgot} className="btn-primary w-full py-2.5 text-sm">
                  Done
                </button>
              </div>
            ) : (
              <form onSubmit={handleForgotSubmit} className="space-y-4">
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
                    Email Address
                  </label>
                  <input
                    type="email"
                    value={forgotEmail}
                    onChange={(e) => setForgotEmail(e.target.value)}
                    className={inputCls}
                    placeholder="you@aegisai.io"
                    required
                    autoFocus
                  />
                </div>
                <button type="submit" disabled={forgotLoading} className="btn-primary w-full py-3">
                  {forgotLoading ? 'Sending…' : 'Send Reset Link'}
                </button>
                <button
                  type="button"
                  onClick={closeForgot}
                  className="w-full text-sm text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors py-1"
                >
                  Cancel
                </button>
              </form>
            )}
          </div>
        </div>
      )}

      {/* Mobile Hero Banner — compact brand strip for small screens only */}
      <div className="lg:hidden relative overflow-hidden bg-gradient-to-br from-violet-950 via-[#13102a] to-[#020617] px-6 py-8">
        <div className="relative z-10">
          <h2
            className="text-2xl font-extrabold text-white leading-tight"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Technology Services,
            <span className="block bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              Aegis Enabled.
            </span>
          </h2>
          <p className="text-[#a5a7c0] text-sm mt-2">
            Your unified commission management hub, powered by AI.
          </p>
        </div>
      </div>

      {/* Form Panel */}
      <div className="w-full lg:w-[480px] flex flex-col justify-center p-8 lg:p-16 shrink-0">
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-1">
            <img
              src={logoSrc}
              alt="AEGISAXIOM"
              className={`w-10 h-10 object-contain ${
                isDark ? 'drop-shadow-[0_0_8px_rgba(139,92,246,0.6)]' : ''
              }`}
            />
            <span
              className="text-aegisaxiom-text font-bold text-xl"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              AEGISAXIOM
            </span>
          </div>
          <p className="text-aegisaxiom-text-muted text-sm">by AegisAI</p>
        </div>

        {/* Welcome heading */}
        <div className="mb-8" data-testid="login-tab">
          <h1 className="text-2xl font-bold text-aegisaxiom-text mb-1">Welcome back</h1>
          <p className="text-sm text-aegisaxiom-text-secondary">Sign in to your AEGISAXIOM workspace</p>
        </div>

        {/* Google SSO */}
        <button
          onClick={handleGoogle}
          data-testid="google-signin-btn"
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
          <span className="text-aegisaxiom-text-muted text-xs">or</span>
          <div className="flex-1 border-t border-aegisaxiom-border" />
        </div>

        <form onSubmit={handleLogin} className="space-y-4" data-testid="login-form">
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
              Email
            </label>
            <input
              name="email"
              type="email"
              value={form.email}
              onChange={set}
              className={inputCls}
              placeholder="you@aegis.io"
              required
              data-testid="email-input"
            />
          </div>
          <div>
            <label className="text-xs text-aegisaxiom-text-secondary mb-1.5 block font-medium">
              Password
            </label>
            <div className="relative">
              <input
                name="password"
                type={showPassword ? 'text' : 'password'}
                value={form.password}
                onChange={set}
                onKeyDown={handlePasswordKey}
                onKeyUp={handlePasswordKey}
                className={`${inputCls} pr-11`}
                placeholder="••••••••"
                required
                data-testid="password-input"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                tabIndex={-1}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition-colors"
              >
                {showPassword ? (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.878 9.878L3 3m6.878 6.878L21 21"
                    />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                    />
                  </svg>
                )}
              </button>
            </div>
            {capsLockOn && (
              <div className="flex items-center gap-1.5 mt-1.5 text-xs text-amber-600 dark:text-amber-400">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                  />
                </svg>
                <span>Caps Lock is on</span>
              </div>
            )}
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowForgot(true)}
              className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition-colors"
            >
              Forgot password?
            </button>
          </div>
          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-3"
            data-testid="login-submit-btn"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      </div>

      {/* Hero Panel — theme-locked to dark for brand consistency */}
      <div className="hidden lg:flex flex-1 relative overflow-hidden bg-gradient-to-br from-violet-950 via-[#13102a] to-[#020617]">
        <img
          src="https://images.unsplash.com/photo-1770520215319-9db74621a448?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1MTN8MHwxfHNlYXJjaHw0fHxhYnN0cmFjdCUyMGZ1dHVyaXN0aWMlMjBkaWdpdGFsJTIwdGVjaG5vbG9neSUyMG5ldHdvcmslMjBkYXJrJTIwcHVycGxlJTIwYmFja2dyb3VuZHxlbnwwfHx8fDE3NzEzNTk1NDZ8MA&ixlib=rb-4.1.0&q=85"
          alt="Tech"
          className="absolute inset-0 w-full h-full object-cover opacity-20"
        />
        <div className="relative z-10 flex flex-col justify-center p-16">
          <h2
            className="text-5xl font-extrabold text-white leading-tight mb-5"
            style={{ fontFamily: 'Manrope, sans-serif' }}
          >
            Technology Services,
            <span className="block bg-gradient-to-r from-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              AegisAI Enabled.
            </span>
          </h2>
          <p className="text-[#a5a7c0] text-base leading-relaxed mb-10">
            Your unified commission management hub - track deals, partners, payouts, contracts, and
            client relationships, powered by AI.
          </p>
          {[
            'Enforced data security',
            'Real-time finance sync',
            'AI-powered insights',
          ].map((item) => (
            <div key={item} className="flex items-center gap-3 mb-3">
              <div className="w-5 h-5 rounded-full bg-violet-500/20 border border-violet-400/40 flex items-center justify-center shrink-0">
                <svg
                  className="w-3 h-3 text-violet-300"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={3}
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              </div>
              <span className="text-[#a5a7c0] text-sm">{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
