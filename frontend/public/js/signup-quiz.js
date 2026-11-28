/* signup-quiz.js — one question per screen, tap to answer, then create the account.
   Flow and feel follow the MDacne quiz (big serif question, pill answers, 3-part progress bar, "Next" only for
   multi-select, a personalised summary at the end); the questions are Clarity's own and deliberately few — only what
   a dermatologist needs before a skin scan (see intake.py for why each one is asked).

   Answers are held in the browser until the account form is submitted, then sent once with the sign-up request as
   `intake`. The server re-validates every value (intake.clean_intake), so this file is convenience, not trust. */
(function () {
    const root = document.getElementById('quiz-root');
    if (!root) return;
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const KEY = 'clarity_signup_quiz';

    const SECTIONS = ['About you', 'Your skin', 'Safety & history'];
    const OPT = {
        sex: [['Female', 'Female', 'fa-venus'], ['Male', 'Male', 'fa-mars'], ['Other', 'Other', 'fa-genderless'], ['', 'Prefer not to say', 'fa-minus']],
        goals: [['acne', 'Clear acne & breakouts', 'fa-circle-dot'], ['dark_spots', 'Fade dark spots & sun damage', 'fa-sun'], ['redness', 'Calm redness & irritation', 'fa-fire-flame-simple'],
            ['texture', 'Smooth texture & pores', 'fa-border-all'], ['aging', 'Fine lines & early ageing', 'fa-hourglass-half'], ['other', 'Something else', 'fa-ellipsis']],
        skin_type: [['dry', 'Dry', 'fa-wind'], ['normal', 'Normal', 'fa-face-smile'], ['oily', 'Oily', 'fa-droplet'], ['combination', 'Combination', 'fa-circle-half-stroke'], ['unsure', "I'm not sure", 'fa-question']],
        duration: [['lt3m', 'Less than 3 months'], ['3to12m', '3 months to a year'], ['gt1y', 'More than a year']],
        breakout: [['blackheads', 'Mostly blackheads & clogged pores'], ['few_pimples', 'A few pimples'], ['regular', 'Regular breakouts'], ['frequent_painful', 'Frequent or painful, deep breakouts']],
        sun: [['rarely', 'Rarely in the sun', 'fa-cloud'], ['some', 'Some — under an hour a day', 'fa-cloud-sun'], ['often', 'Often — 1 to 3 hours a day', 'fa-sun'], ['most_day', 'Outdoors most of the day', 'fa-temperature-high']],
        tried: [['otc_creams', 'Over-the-counter creams', 'fa-bottle-droplet'], ['prescription', 'Prescription creams or tablets', 'fa-prescription-bottle-medical'],
            ['isotretinoin', 'Isotretinoin (now or in the last 6 months)', 'fa-pills'], ['procedures', 'Peels, facials or laser', 'fa-wand-magic-sparkles'], ['nothing', 'Nothing yet', 'fa-ban']],
        sensitivities: [['sensitive_skin', 'My skin is easily irritated'], ['retinoids', 'Retinol / retinoids'], ['benzoyl_peroxide', 'Benzoyl peroxide'], ['acids', 'Salicylic acid / AHAs'], ['fragrance', 'Fragrance'], ['none', 'None that I know of']],
        pregnancy: [['yes', 'Pregnant or breastfeeding'], ['planning', 'Planning a pregnancy'], ['no', 'No'], ['skip', 'Prefer not to say']],
    };
    const label = (q, v) => ((OPT[q] || []).find(o => o[0] === v) || [])[1] || v;

    // section: 0..2. `when` hides a question that doesn't apply (keeps the quiz short).
    const STEPS = [
        { id: 'about', section: 0, type: 'about', title: 'Tell us about you', sub: 'This helps your doctor read your skin in context.' },
        { id: 'goals', section: 0, type: 'multi', title: 'What are your goals?', sub: 'Pick everything that matters to you.', opts: 'goals', required: true },
        { id: 'skin_type', section: 1, type: 'single', title: "What's your skin type?", opts: 'skin_type' },
        { id: 'duration', section: 1, type: 'single', title: 'How long have you had these concerns?', opts: 'duration' },
        { id: 'breakout', section: 1, type: 'single', title: 'How would you describe your breakouts?', opts: 'breakout', when: a => (a.goals || []).includes('acne') },
        { id: 'sun', section: 1, type: 'single', title: 'How much sun do you get on a typical day?', sub: 'Sun exposure shapes pigmentation and sun-damage risk.', opts: 'sun' },
        { id: 'tried', section: 2, type: 'multi', title: 'Which treatments have you tried?', opts: 'tried', exclusive: 'nothing', required: true },
        { id: 'sensitivities', section: 2, type: 'multi', title: 'Any known sensitivities?', sub: 'So nothing irritating is suggested.', opts: 'sensitivities', exclusive: 'none', required: true },
        { id: 'pregnancy', section: 2, type: 'single', title: 'Are you pregnant, breastfeeding or planning a pregnancy?', sub: 'Some active ingredients are not safe in pregnancy.', opts: 'pregnancy', when: a => a.sex !== 'Male' },
    ];

    let st = { answers: {}, i: 0, stage: 'quiz', age: '' };   // stage: quiz | account | done
    try { const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null'); if (saved && saved.answers) st = Object.assign(st, saved, { stage: saved.stage === 'done' ? 'quiz' : saved.stage }); } catch (e) { /* private mode */ }
    const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(st)); } catch (e) { /* ignore */ } };
    const visible = () => STEPS.filter(s => !s.when || s.when(st.answers));
    const cur = () => visible()[st.i];

    // ------------------------------------------------------------------ chrome
    function progress(sectionIdx, frac) {
        const segs = SECTIONS.map((name, k) => {
            const fill = k < sectionIdx ? 100 : k === sectionIdx ? Math.round(frac * 100) : 0;
            return `<div class="q-seg" title="${esc(name)}"><div class="q-fill" style="width:${fill}%"></div><span class="q-dot ${k <= sectionIdx ? 'on' : ''}">${k + 1}</span></div>`;
        }).join('');
        return `<div class="q-progress" aria-label="Progress">${segs}</div>`;
    }
    function topbar(canBack, sectionIdx, frac) {
        return `<div class="q-top"><button type="button" class="q-back" id="q-back" ${canBack ? '' : 'style="visibility:hidden"'}>‹ Back</button>${progress(sectionIdx, frac)}</div>`;
    }
    function shell(inner) {
        root.innerHTML = `<div class="q-card q-anim">${inner}</div>`;
    }
    const heading = (t, sub) => `<h1 class="q-title">${esc(t)}</h1>${sub ? `<p class="q-sub">${esc(sub)}</p>` : ''}`;

    // ------------------------------------------------------------------ quiz screens
    function render() {
        if (st.stage === 'account') return renderAccount();
        if (st.stage === 'done') return renderDone();
        const vs = visible();
        if (st.i >= vs.length) { st.stage = 'account'; save(); return renderAccount(); }
        const step = vs[st.i];
        const inSection = vs.filter(s => s.section === step.section);
        const frac = (inSection.indexOf(step) + 0.5) / inSection.length;
        const a = st.answers;
        let body = '';
        if (step.type === 'about') {
            const age = Number(st.age), young = st.age !== '' && age > 0 && age < 18;
            body = `<label class="q-field"><span>Your age</span><input id="q-age" type="number" inputmode="numeric" min="13" max="100" placeholder="e.g. 28" value="${esc(st.age)}"></label>
                <div class="q-label">Gender</div>
                <div class="q-options" role="radiogroup">${OPT.sex.map(([v, l, ic]) => pill(v, l, ic, a.sex === v && a.sex !== undefined, 'radio')).join('')}</div>
                ${young ? '<p class="q-note">Under 18? A parent or guardian should sign up with you and join the doctor consultation.</p>' : ''}
                <button type="button" class="q-next" id="q-next" disabled>Next</button>`;
        } else {
            const multi = step.type === 'multi', chosen = multi ? (a[step.id] || []) : a[step.id];
            body = `<div class="q-options" role="${multi ? 'group' : 'radiogroup'}">${OPT[step.opts].map(([v, l, ic]) => pill(v, l, ic, multi ? chosen.includes(v) : chosen === v, multi ? 'checkbox' : 'radio')).join('')}</div>
                ${multi ? `<button type="button" class="q-next" id="q-next" ${chosen.length ? '' : 'disabled'}>Next</button>` : ''}`;
        }
        shell(`${topbar(st.i > 0, step.section, frac)}${heading(step.title, step.sub)}${body}`);
        wireQuiz(step);
    }
    function pill(v, l, icon, on, role) {
        return `<button type="button" class="q-pill ${on ? 'on' : ''}" data-v="${esc(v)}" role="${role}" aria-checked="${on ? 'true' : 'false'}">
            ${icon ? `<i class="fa-solid ${icon} q-ico"></i>` : ''}<span>${esc(l)}</span><i class="fa-regular fa-circle-check q-check"></i></button>`;
    }
    const advance = () => { st.i += 1; save(); render(); };
    function wireQuiz(step) {
        document.getElementById('q-back').onclick = () => { if (st.i > 0) { st.i -= 1; save(); render(); } };
        const a = st.answers, next = document.getElementById('q-next');
        if (step.type === 'about') {
            const age = document.getElementById('q-age');
            const ok = () => { const n = Number(age.value); next.disabled = !(age.value !== '' && n >= 13 && n <= 100 && a.sex !== undefined); };
            age.oninput = () => { st.age = age.value; save(); ok(); };
            root.querySelectorAll('.q-pill').forEach(p => p.onclick = () => {
                a.sex = p.dataset.v; if (a.sex === 'Male') delete a.pregnancy; save();
                root.querySelectorAll('.q-pill').forEach(x => { const on = x === p; x.classList.toggle('on', on); x.setAttribute('aria-checked', on); });
                ok();
            });
            next.onclick = () => { if (!next.disabled) advance(); };
            ok(); age.focus();
            return;
        }
        if (step.type === 'single') {
            root.querySelectorAll('.q-pill').forEach(p => p.onclick = () => {
                a[step.id] = p.dataset.v; save();
                root.querySelectorAll('.q-pill').forEach(x => x.classList.toggle('on', x === p));
                setTimeout(advance, 230);          // tap = answer; the next screen follows by itself
            });
            return;
        }
        root.querySelectorAll('.q-pill').forEach(p => p.onclick = () => {
            const v = p.dataset.v; let list = a[step.id] || [];
            if (list.includes(v)) list = list.filter(x => x !== v);
            else list = (v === step.exclusive) ? [v] : list.filter(x => x !== step.exclusive).concat(v);
            a[step.id] = list; if (step.id === 'goals' && !list.includes('acne')) delete a.breakout; save();
            root.querySelectorAll('.q-pill').forEach(x => { const on = list.includes(x.dataset.v); x.classList.toggle('on', on); x.setAttribute('aria-checked', on); });
            next.disabled = !list.length;
        });
        next.onclick = () => { if (!next.disabled) advance(); };
    }

    // ------------------------------------------------------------------ account
    function renderAccount() {
        shell(`<div class="q-top"><button type="button" class="q-back" id="q-back">‹ Back</button>${progress(2, 1)}</div>
            ${heading('Last step — create your account', 'Your answers are saved to your private profile so your doctor can see them. Nothing is shared without you.')}
            <form id="q-account" class="q-form" novalidate>
                <label class="q-field"><span>Full name</span><input name="full_name" type="text" required autocomplete="name"></label>
                <label class="q-field"><span>Email</span><input name="email" type="email" required autocomplete="email"></label>
                <label class="q-field"><span>Password <em>(min. 8 characters)</em></span><input name="password" type="password" required minlength="8" autocomplete="new-password"></label>
                <label class="q-field"><span>Anything else your doctor should know? <em>(optional)</em></span><textarea name="note" rows="2" maxlength="500" placeholder="Medicines you take, past skin conditions, allergies…"></textarea></label>
                <p class="q-trust"><i class="fa-solid fa-shield-halved"></i> Your photos and answers are used only for your own care. We never use them to train AI.</p>
                <div id="q-error" class="q-error" role="alert"></div>
                <button type="submit" class="q-next" id="q-submit">Create my account</button>
            </form>
            <p class="q-alt">Already have an account? <a href="/login">Sign in</a></p>`);
        document.getElementById('q-back').onclick = () => { st.stage = 'quiz'; st.i = Math.max(0, visible().length - 1); save(); render(); };
        document.getElementById('q-account').addEventListener('submit', submit);
    }

    async function submit(e) {
        e.preventDefault();
        const f = e.target, err = document.getElementById('q-error'), btn = document.getElementById('q-submit');
        err.textContent = '';
        const a = st.answers;
        const intake = {
            goals: a.goals || [], skin_type: a.skin_type, duration: a.duration, breakout: a.breakout, sun: a.sun,
            tried: a.tried || [], sensitivities: a.sensitivities || [], pregnancy: a.sex !== 'Male' ? a.pregnancy : undefined, note: f.note.value,
        };
        const body = {
            full_name: f.full_name.value.trim(), email: f.email.value.trim(), password: f.password.value,
            sex: a.sex || '', age: st.age, intake,
        };
        if (!body.full_name) { err.textContent = 'Please enter your name'; return; }
        if (body.password.length < 8) { err.textContent = 'Password must be at least 8 characters'; return; }
        btn.disabled = true; btn.textContent = 'Creating your account…';
        try {
            const r = await fetch('/api/auth/signup', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
            let data = {};
            const text = await r.text();
            try { data = JSON.parse(text); } catch (_) { data = { detail: text || r.statusText }; }
            if (!r.ok) {
                // If account exists, seamlessly sign in and proceed
                if (r.status === 409 || (data.detail && data.detail.includes('already exists'))) {
                    const loginRes = await fetch('/api/auth/login', {
                        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email: body.email, password: body.password }),
                    });
                    if (loginRes.ok) {
                        const loginData = await loginRes.json();
                        st.name = loginData.full_name || body.full_name; st.stage = 'done'; save();
                        renderDone();
                        return;
                    }
                }
                let msg = data.detail || data.error || data.message || 'Could not create account';
                if (typeof msg !== 'string') msg = JSON.stringify(msg);
                if (msg.includes('pattern') || msg.includes('SyntaxError') || msg.includes('429')) {
                    msg = 'Please wait a moment and try again.';
                }
                throw new Error(msg);
            }
            st.name = data.full_name || body.full_name; st.stage = 'done'; save();
            renderDone();
        } catch (ex) {
            let msg = ex.message || 'Could not create account';
            if (msg.includes('pattern') || msg.includes('SyntaxError')) {
                msg = 'Please check your inputs and try again.';
            }
            err.textContent = msg; btn.disabled = false; btn.textContent = 'Create my account';
        }
    }

    // ------------------------------------------------------------------ personalised summary
    function renderDone() {
        const a = st.answers, first = (st.name || 'there').split(' ')[0];
        const profile = a.goals && a.goals.includes('acne') && a.breakout ? label('breakout', a.breakout)
            : [a.skin_type && a.skin_type !== 'unsure' ? label('skin_type', a.skin_type) + ' skin' : null, a.duration ? label('duration', a.duration).toLowerCase() : null].filter(Boolean).join(' · ') || 'Your skin profile';
        const goals = (a.goals || []).map(g => label('goals', g)), goal = goals[0] || 'Healthier-looking skin';
        shell(`<div class="q-done">
            <div class="q-eyebrow">Welcome to Clarity</div>
            <h1 class="q-title">${esc(first)}, based on what <em>you</em> told us:</h1>
            <div class="q-sum">
                <div><div class="q-sum-k">Your skin profile</div><div class="q-sum-v">${esc(profile)}</div></div>
                <div class="q-arrow" aria-hidden="true">→</div>
                <div><div class="q-sum-k">Your goal</div><div class="q-sum-v">${esc(goal)}${goals.length > 1 ? ` <span class="q-more">+${goals.length - 1} more</span>` : ''}</div></div>
            </div>
            <ol class="q-steps">
                <li><b>Guided face scan</b><span>Five quick photos. Your eyes are covered automatically and never stored.</span></li>
                <li><b>Instant AI skin report</b><span>Your score out of 100, what we see, and products matched to your skin.</span></li>
                <li><b>Doctor review</b><span>A dermatologist reviews everything, confirms a plan and books your follow-up.</span></li>
            </ol>
            <button type="button" class="q-next" id="q-go">Start my face scan</button>
            <p class="q-fine">AI supports review. Only a doctor approves prescriptions and changes.</p>
        </div>`);
        document.getElementById('q-go').onclick = () => { try { sessionStorage.removeItem(KEY); } catch (e) { /* ignore */ } window.location.href = '/patient'; };
    }

    document.body.classList.add('quiz-bg');
    render();
})();
