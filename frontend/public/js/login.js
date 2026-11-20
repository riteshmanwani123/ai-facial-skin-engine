(function () {
    const params = new URLSearchParams(window.location.search);
    const role = params.get('role');
    const autoAs = params.get('as');
    const err = params.get('error');
    const note = document.getElementById('auth-context-note');
    const emailInput = document.querySelector('input[name="email"]');
    const passInput = document.querySelector('input[name="password"]');

    const DEMO_CREDENTIALS = {
        patient: { email: 'amantadikademo@gmail.com', pass: '12345678', target: '/patient', label: 'Patient Portal' },
        doctor: { email: 'doctor@clarity.local', pass: 'Doctor12345', target: '/doctor', label: 'Doctor Portal' },
        admin: { email: 'admin@clarity.local', pass: 'Admin12345', target: '/admin', label: 'Admin Console' },
        clinic: { email: 'admin@clarity.local', pass: 'Admin12345', target: '/', label: 'Clinic Dashboard' },
    };

    if (role === 'patient') {
        note.textContent = 'Sign in to continue to your skin scan. (Demo credentials pre-filled below)';
        if (emailInput && !emailInput.value) emailInput.value = DEMO_CREDENTIALS.patient.email;
        if (passInput && !passInput.value) passInput.value = DEMO_CREDENTIALS.patient.pass;
    } else if (role === 'clinician') {
        note.textContent = 'Doctor & clinic staff account required. (Demo credentials pre-filled below)';
        if (emailInput && !emailInput.value) emailInput.value = DEMO_CREDENTIALS.doctor.email;
        if (passInput && !passInput.value) passInput.value = DEMO_CREDENTIALS.doctor.pass;
    } else if (role === 'admin') {
        note.textContent = 'Administrator account required. (Demo credentials pre-filled below)';
        if (emailInput && !emailInput.value) emailInput.value = DEMO_CREDENTIALS.admin.email;
        if (passInput && !passInput.value) passInput.value = DEMO_CREDENTIALS.admin.pass;
    }
    if (err === 'wrong_role') document.getElementById('login-error').textContent = 'That account type can\'t access this page. Sign in with the required role.';

    async function loginWith(email, password, redirectTarget) {
        const errBox = document.getElementById('login-error');
        if (errBox) errBox.textContent = 'Signing in…';
        try {
            const r = await fetch('/api/auth/login', {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password }),
            });
            const data = await r.json();
            if (!r.ok) throw new Error(data.detail || data.error || 'Sign-in failed');
            window.location.href = redirectTarget || (data.role === 'admin' ? '/admin' : data.role === 'clinician' ? '/doctor' : '/patient');
        } catch (err) {
            if (errBox) errBox.textContent = err.message;
        }
    }

    // Auto-login trigger if ?as=... is specified in the URL
    if (autoAs && DEMO_CREDENTIALS[autoAs]) {
        const cred = DEMO_CREDENTIALS[autoAs];
        loginWith(cred.email, cred.pass, cred.target);
        return;
    }

    // Attach Quick Demo Test Buttons into the DOM if not already present
    const card = document.querySelector('.auth-card');
    if (card && !document.getElementById('quick-demo-box')) {
        const demoBox = document.createElement('div');
        demoBox.id = 'quick-demo-box';
        demoBox.style.marginTop = '24px';
        demoBox.style.padding = '16px';
        demoBox.style.borderRadius = '8px';
        demoBox.style.background = 'rgba(0, 210, 255, 0.05)';
        demoBox.style.border = '1px solid rgba(0, 210, 255, 0.2)';

        demoBox.innerHTML = `
            <div style="font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent-cyan, #00d2ff); margin-bottom: 8px;">
                ⚡ 1-Click Demo Testing Portals
            </div>
            <p style="font-size: 12px; color: var(--text-secondary, #94a3b8); margin-bottom: 12px; line-height: 1.4;">
                Click any button to instantly log in and test each live portal:
            </p>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                <button type="button" id="demo-patient-btn" class="btn btn-secondary" style="font-size: 12px; padding: 8px 10px; text-align: left;">
                    👤 Patient Portal
                </button>
                <button type="button" id="demo-doctor-btn" class="btn btn-secondary" style="font-size: 12px; padding: 8px 10px; text-align: left;">
                    🩺 Doctor Portal
                </button>
                <button type="button" id="demo-admin-btn" class="btn btn-secondary" style="font-size: 12px; padding: 8px 10px; text-align: left;">
                    🛡️ Admin Console
                </button>
                <button type="button" id="demo-clinic-btn" class="btn btn-secondary" style="font-size: 12px; padding: 8px 10px; text-align: left;">
                    🏥 Clinic Dashboard
                </button>
            </div>
        `;

        card.appendChild(demoBox);

        document.getElementById('demo-patient-btn')?.addEventListener('click', () => {
            loginWith(DEMO_CREDENTIALS.patient.email, DEMO_CREDENTIALS.patient.pass, DEMO_CREDENTIALS.patient.target);
        });
        document.getElementById('demo-doctor-btn')?.addEventListener('click', () => {
            loginWith(DEMO_CREDENTIALS.doctor.email, DEMO_CREDENTIALS.doctor.pass, DEMO_CREDENTIALS.doctor.target);
        });
        document.getElementById('demo-admin-btn')?.addEventListener('click', () => {
            loginWith(DEMO_CREDENTIALS.admin.email, DEMO_CREDENTIALS.admin.pass, DEMO_CREDENTIALS.admin.target);
        });
        document.getElementById('demo-clinic-btn')?.addEventListener('click', () => {
            loginWith(DEMO_CREDENTIALS.clinic.email, DEMO_CREDENTIALS.clinic.pass, DEMO_CREDENTIALS.clinic.target);
        });
    }

    document.getElementById('login-form')?.addEventListener('submit', async (e) => {
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
