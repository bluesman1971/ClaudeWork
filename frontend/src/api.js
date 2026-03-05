/**
 * api.js — Core fetch wrapper and auth flow.
 *
 * Auth success/logout are signalled via DOM CustomEvents so this module
 * has no imports from clients.js or trips.js (which would create circular deps).
 *
 *   'auth:success' — fired after checkAuth() or handleLogin() succeed
 *   'auth:logout'  — fired after handleLogout() completes
 *
 * main.js listens for these events and calls refreshClientList() / loadSavedTrips()
 * / resetForm() accordingly.
 */

import { state, API_URL } from './state.js';

export function showLoginScreen() {
    document.getElementById('loginScreen').classList.add('active');
    document.getElementById('mastheadUser').style.display = 'none';
    document.getElementById('mastheadIssue').style.display = '';
    setTimeout(() => document.getElementById('loginEmail').focus(), 50);
}

function _onAuthSuccess() {
    document.getElementById('loginScreen').classList.remove('active');
    document.getElementById('mastheadUser').style.display = '';
    document.getElementById('mastheadIssue').style.display = 'none';
    document.getElementById('mastheadUserName').textContent =
        state.currentUser.full_name || state.currentUser.email;
    document.dispatchEvent(new CustomEvent('auth:success'));
}

/**
 * apiFetch — drop-in fetch wrapper that:
 *  - Includes credentials (httpOnly cookie) on every request
 *  - Redirects to login screen on 401
 *  - Sends X-Requested-With for CSRF defence on state-changing requests
 */
export async function apiFetch(path, options = {}) {
    const res = await fetch(`${API_URL}${path}`, {
        credentials: 'include',
        ...options,
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            ...(options.headers || {}),
        },
    });
    if (res.status === 401) {
        state.currentUser = null;
        showLoginScreen();
        throw new Error('Not authenticated');
    }
    return res;
}

export async function checkAuth() {
    try {
        const res = await fetch(`${API_URL}/auth/me`, { credentials: 'include' });
        if (res.ok) {
            const data = await res.json();
            state.currentUser = data.user;
            _onAuthSuccess();
        } else {
            showLoginScreen();
        }
    } catch {
        showLoginScreen();
    }
}

export async function handleLogin(e) {
    e.preventDefault();
    const btn = document.getElementById('loginBtn');
    const err = document.getElementById('loginError');
    err.classList.remove('active');
    btn.disabled = true;
    btn.textContent = 'Signing in…';
    try {
        const res = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email:    document.getElementById('loginEmail').value.trim(),
                password: document.getElementById('loginPassword').value,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Login failed');
        state.currentUser = data.user;
        document.getElementById('loginPassword').value = '';
        _onAuthSuccess();
    } catch (ex) {
        err.textContent = ex.message;
        err.classList.add('active');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Sign In';
    }
}

export async function handleLogout() {
    await fetch(`${API_URL}/auth/logout`, { method: 'POST', credentials: 'include' });
    state.currentUser = null;
    document.dispatchEvent(new CustomEvent('auth:logout'));
    showLoginScreen();
}
