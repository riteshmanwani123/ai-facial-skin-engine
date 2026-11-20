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
            const data = await r.json();
            if (!r.ok) throw new Error(data.detail || data.error || 'Could not create account');
            window.location.href = '/patient';
        } catch (err) {
            errBox.textContent = err.message;
        }
    });
})();
