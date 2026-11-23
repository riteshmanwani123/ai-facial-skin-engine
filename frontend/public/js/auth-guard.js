/* auth-guard.js — shared session check used by the patient and doctor portals.
   Exposes window.requireAuth(role) -> Promise<user> (redirects to /login if not
   signed in or wrong role) and window.clarityLogout(). */
(function () {
    async function fetchMe() {
        try {
            const r = await fetch('/api/auth/me', { credentials: 'include' });
            if (!r.ok) return null;
            return await r.json();
        } catch (_) {
            return null;
        }
    }

    window.requireAuth = async function (role) {
        const user = await fetchMe();
        if (!user) {
            window.location.href = `/login?role=${encodeURIComponent(role)}`;
            return null;
        }
        if (user.role !== role) {
            window.location.href = '/login?error=wrong_role';
            return null;
        }
        return user;
    };

    window.clarityLogout = async function () {
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
        window.location.href = '/login';
    };
})();
