import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';
import { safePost } from '../lib/safeXhr';
import API_URL from '../lib/apiConfig';
import getRoleHome from '../lib/roleHome';

const API = API_URL;

export default function AuthCallback() {
  const navigate = useNavigate();
  const { setUser } = useAuth();
  const hasProcessed = useRef(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');

    // Clear auth code from URL immediately to prevent leaking via referrer/logs
    window.history.replaceState({}, '', '/auth/google/callback');

    if (!code) {
      toast.error('Authentication failed');
      navigate('/login');
      return;
    }

    // Validate state nonce against stored value to prevent CSRF
    const storedState = sessionStorage.getItem('aegisaxiom_oauth_state');
    sessionStorage.removeItem('aegisaxiom_oauth_state');
    if (!state || !storedState || state !== storedState) {
      toast.error('Authentication failed: invalid state');
      navigate('/login');
      return;
    }

    const flow = state.split(':')[0];
    const inviteToken = sessionStorage.getItem('aegisaxiom_invite_token');

    if (flow === 'invite' && inviteToken) {
      acceptInviteGoogle(code, inviteToken);
    } else {
      regularGoogleSignIn(code);
    }
  }, [navigate, setUser]); // eslint-disable-line react-hooks/exhaustive-deps

  const acceptInviteGoogle = async (code, inviteToken) => {
    try {
      const { data } = await safePost(`${API}/api/auth/accept-invite-google`, {
        code,
        invite_token: inviteToken,
      });
      sessionStorage.removeItem('aegisaxiom_invite_token');
      if (data.token) localStorage.setItem('aegisaxiom_session_token', data.token);
      setUser(data.user);
      toast.success('Account activated with Google! Welcome to AEGISAXIOM.');
      navigate(getRoleHome(data.user), { replace: true });
    } catch (err) {
      toast.error(err.message || 'Failed to activate account with Google.');
      navigate('/login');
    }
  };

  const regularGoogleSignIn = async (code) => {
    try {
      const { data } = await safePost(`${API}/api/auth/google/session`, {
        code,
      });
      if (data.token) localStorage.setItem('aegisaxiom_session_token', data.token);
      setUser(data.user);
      navigate(getRoleHome(data.user), { replace: true });
    } catch (err) {
      toast.error(err.message || 'Google sign-in failed.');
      navigate('/login');
    }
  };

  return (
    <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center">
      <div className="text-center">
        <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
        <p className="text-aegisaxiom-text-secondary text-sm" data-testid="auth-callback-loading">
          Signing you in with Google...
        </p>
      </div>
    </div>
  );
}
