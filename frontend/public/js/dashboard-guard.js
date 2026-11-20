/* dashboard-guard.js — the clinic dashboard (upload / drag-and-drop analysis, patient records, model reports) is for
   admins only. Anyone else is sent to their own portal or the sign-in page; the page stays hidden until the check passes.
   The API enforces the same rule, so this is a convenience, not the security boundary. */
(async function () {
    const go = url => { window.location.replace(url); };
    let user = null;
    try {
        const r = await fetch('/api/auth/me', { credentials: 'include' });
        if (r.ok) user = await r.json();
    } catch (e) { /* offline: fall through to sign-in */ }
    if (!user) return go('/login?role=admin');
    if (user.role !== 'admin') return go(user.role === 'patient' ? '/patient' : user.role === 'clinician' ? '/doctor' : '/login');
    const bar = document.getElementById('session-bar');
    if (bar) {
        bar.innerHTML = `${user.full_name.replace(/[<>&]/g, '')} · ID ${user.user_code || ''} · <a href="#" id="logout-link">Sign out</a>`;
        document.getElementById('logout-link').addEventListener('click', async e => {
            e.preventDefault();
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
            go('/login');
        });
    }
    document.body.style.visibility = 'visible';
})();
