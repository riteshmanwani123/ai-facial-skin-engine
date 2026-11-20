(function () {
    const params = new URLSearchParams(window.location.search);
    const role = params.get('role');
    const err = params.get('error');
    const note = document.getElementById('auth-context-note');
    if (role === 'patient') note.textContent = 'Sign in to continue to your skin scan.';
    else if (role === 'clinician') note.textContent = 'Doctor & clinic staff accounts are created by an administrator — sign in with the credentials you were given.';
    else if (role === 'admin') note.textContent = 'Sign in with your administrator account.';
    if (err === 'wrong_role') document.getElementById('login-error').textContent = 'That account type can\'t access this page.';

    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target).entries());
        const errBox = document.getElementById('login-error');
        errBox.textContent = '';
        try {
            const r = await fetch('/api/auth/login', {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(f),
            });
            const data = await r.json();
            if (!r.ok) throw new Error(data.detail || data.error || 'Sign-in failed');
            window.location.href = data.role === 'admin' ? '/admin' : data.role === 'clinician' ? '/doctor' : '/patient';
        } catch (err) {
            errBox.textContent = err.message;
        }
    });
})();
