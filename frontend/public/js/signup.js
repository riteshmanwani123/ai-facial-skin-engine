(function () {
    document.getElementById('signup-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const errBox = document.getElementById('signup-error');
        errBox.textContent = '';
        const f = Object.fromEntries(new FormData(e.target).entries());
        f.consent_research = e.target.consent_research && e.target.consent_research.checked ? 1 : 0;
        try {
            const r = await fetch('/api/auth/signup', {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(f),
            });
            let data = {};
            const text = await r.text();
            try { data = JSON.parse(text); } catch (_) { data = { detail: text || r.statusText }; }
            if (!r.ok) {
                if (r.status === 409 || (data.detail && data.detail.includes('already exists'))) {
                    const loginRes = await fetch('/api/auth/login', {
                        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email: f.email, password: f.password }),
                    });
                    if (loginRes.ok) {
                        window.location.href = '/patient';
                        return;
                    }
                }
                let msg = data.detail || data.error || data.message || 'Could not create account';
                if (typeof msg !== 'string') msg = JSON.stringify(msg);
                if (msg.includes('pattern') || msg.includes('SyntaxError') || msg.includes('429')) {
                    msg = 'Please check your information and try again.';
                }
                throw new Error(msg);
            }
            window.location.href = '/patient';
        } catch (err) {
            let msg = err.message || 'Could not create account';
            if (msg.includes('pattern') || msg.includes('SyntaxError')) {
                msg = 'Please check your information and try again.';
            }
            errBox.textContent = msg;
        }
    });
})();
