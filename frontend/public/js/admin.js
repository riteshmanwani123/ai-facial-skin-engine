/* admin.js — admin console: create/remove doctor & clinic-staff (clinician) accounts. */
(function () {
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const fmtDate = iso => iso ? new Date(iso).toLocaleString() : '—';
    const api = (url, opts = {}) => fetch(url, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...opts })
        .then(async r => {
            let d = {};
            const text = await r.text();
            try { d = JSON.parse(text); } catch (_) { d = { detail: text || r.statusText }; }
            if (!r.ok) throw new Error(d.error || d.detail || d.message || `Request failed (${r.status})`);
            return d;
        });

    function loadClinicians() {
        api('/api/admin/clinicians').then(d => {
            const tb = document.getElementById('clinicians-tbody');
            tb.innerHTML = d.clinicians.length ? d.clinicians.map(c => `
                <tr>
                    <td><span class="tag">${esc(c.user_code || '—')}</span></td>
                    <td>${esc(c.full_name)}</td>
                    <td>${esc(c.email)}</td>
                    <td>${fmtDate(c.created_at)}</td>
                    <td>${fmtDate(c.last_login_at)}</td>
                    <td><button class="btn btn-secondary btn-small" data-remove="${esc(c.id)}"><i class="fa-solid fa-trash"></i> Remove</button></td>
                </tr>`).join('') : '<tr><td colspan="6" class="empty-note">No clinician accounts yet.</td></tr>';
            tb.querySelectorAll('[data-remove]').forEach(btn => btn.onclick = () => {
                if (!confirm('Remove this clinician account? They will no longer be able to sign in.')) return;
                api(`/api/admin/clinicians/${btn.dataset.remove}`, { method: 'DELETE' }).then(loadClinicians).catch(err => alert(err.message));
            });
        });
    }

    document.getElementById('add-clinician-form').addEventListener('submit', e => {
        e.preventDefault();
        const errBox = document.getElementById('add-clinician-error');
        errBox.textContent = '';
        const f = Object.fromEntries(new FormData(e.target).entries());
        api('/api/admin/clinicians', { method: 'POST', body: JSON.stringify(f) })
            .then(() => { e.target.reset(); loadClinicians(); })
            .catch(err => errBox.textContent = err.message);
    });

    // ------------------------------------------------------------- reservations: hours, closures, upcoming appointments
    function loadSchedule() {
        api('/api/schedule/config').then(c => {
            const brks = c.breaks.map(b => `${b.label} ${b.from}–${b.to}`).join(', ');
            document.getElementById('sched-summary').textContent =
                `${c.days[0]}–${c.days[c.days.length - 1]}, ${c.open}–${c.close} ${c.tz_label}. ${c.slots_per_hour} slots an hour (${c.slot_minutes} min) = ${c.slots_per_day} bookable slots per doctor per day, excluding ${brks}. ` +
                `Booking opens ${c.lead_minutes} min ahead, up to ${c.horizon_days} days out; patients can change or cancel until ${c.change_cutoff_hours} h before.`;
        }).catch(() => { });
        api('/api/admin/schedule-blocks').then(d => {
            const sel = document.getElementById('block-doctor');
            sel.innerHTML = '<option value="">Whole clinic</option>' + d.doctors.map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
            const tb = document.getElementById('blocks-tbody');
            tb.innerHTML = d.blocks.length ? d.blocks.map(b => `<tr><td>${esc(b.label)}</td><td>${esc(b.doctor_name)}</td><td class="muted">${esc(b.reason || '—')}</td>
                <td><button class="btn btn-secondary btn-small" data-unblock="${esc(b.id)}">Remove</button></td></tr>`).join('')
                : '<tr><td colspan="4" class="empty-note">No closures scheduled.</td></tr>';
            tb.querySelectorAll('[data-unblock]').forEach(btn => btn.onclick = () => {
                if (!confirm('Re-open this time for bookings?')) return;
                api(`/api/admin/schedule-blocks/${btn.dataset.unblock}`, { method: 'DELETE' }).then(() => { loadSchedule(); loadCapacity(capWeek && capWeek.week_start); }).catch(err => alert(err.message));
            });
        });
    }

    // ------------------------------------------------------------- capacity (utilisation) for a week
    let capWeek = null;
    function loadCapacity(dateStr) {
        api('/api/admin/schedule-summary' + (dateStr ? `?date=${dateStr}` : '')).then(w => {
            capWeek = w;
            document.getElementById('cap-week').textContent = `Week of ${w.days[0].label} · ${w.slots_per_doctor_day} slots per doctor per day · ${w.tz_label}`;
            const docs = w.days[0].doctors;
            document.getElementById('cap-head').innerHTML = `<tr><th>Doctor</th>${w.days.map(d => `<th>${esc(d.label)}</th>`).join('')}</tr>`;
            const cell = (b, c) => `<td><strong>${b}</strong> / ${c}${c ? ` <span class="muted">(${Math.round(100 * b / c)}%)</span>` : ''}</td>`;
            document.getElementById('cap-body').innerHTML = docs.length
                ? docs.map((dr, i) => `<tr><td>${esc(dr.doctor_name)}</td>${w.days.map(d => cell(d.doctors[i].booked, d.doctors[i].capacity)).join('')}</tr>`).join('') +
                  `<tr><td><strong>All doctors</strong></td>${w.days.map(d => cell(d.booked, d.capacity)).join('')}</tr>`
                : '<tr><td colspan="6" class="empty-note">No doctor accounts yet — add one above to open bookings.</td></tr>';
        });
    }
    document.getElementById('cap-prev').onclick = () => capWeek && loadCapacity(capWeek.prev);
    document.getElementById('cap-next').onclick = () => capWeek && loadCapacity(capWeek.next);

    // ------------------------------------------------------------- appointments: filter, reschedule, cancel, assign a time
    let reschedScheduler = null;
    const pillFor = st => `<span class="pill ${st === 'booked' ? 'ok' : st === 'cancelled' ? 'danger' : ''}">${esc(st)}</span>`;
    function openResched(consultationId, title) {
        const box = document.getElementById('admin-resched'), btn = document.getElementById('admin-resched-confirm'), err = document.getElementById('admin-resched-err');
        document.getElementById('admin-resched-title').textContent = title;
        err.textContent = ''; btn.disabled = true; box.style.display = 'block';
        reschedScheduler = window.ClarityScheduler.create(document.getElementById('admin-resched-root'), { onSelect: slot => { btn.disabled = !slot; } });
        reschedScheduler.load();
        box.scrollIntoView({ behavior: 'smooth', block: 'center' });
        document.getElementById('admin-resched-close').onclick = () => { box.style.display = 'none'; };
        btn.onclick = () => {
            const slot = reschedScheduler.selected(); if (!slot) return;
            btn.disabled = true;
            api(`/api/consultations/${consultationId}/appointment`, { method: 'POST', body: JSON.stringify({ slot_start: slot.start }) })
                .then(() => { box.style.display = 'none'; loadAppointments(); loadCapacity(capWeek && capWeek.week_start); })
                .catch(e => { err.textContent = e.message; reschedScheduler.clear(); reschedScheduler.reload(); });
        };
    }
    function loadAppointments() {
        const f = Object.fromEntries(new FormData(document.getElementById('appt-filter')).entries());
        const qs = Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
        api('/api/admin/appointments' + (qs ? '?' + qs : '')).then(d => {
            const sel = document.getElementById('appt-doctor');
            if (sel.options.length <= 1) sel.innerHTML = '<option value="">All doctors</option>' + d.doctors.map(x => `<option value="${esc(x.id)}">${esc(x.name)} (${esc(x.code || '')})</option>`).join('');
            const nt = document.getElementById('needs-tbody');
            nt.innerHTML = d.needs_time.length ? d.needs_time.map(n => `<tr><td>${esc(n.patient_name)}</td><td class="muted">${esc(n.paid_label || '—')}</td>
                <td><button class="btn btn-secondary btn-small" data-assign="${esc(n.consultation_id)}" data-name="${esc(n.patient_name)}">Assign a time</button></td></tr>`).join('')
                : '<tr><td colspan="3" class="empty-note">Every paid case has a time.</td></tr>';
            nt.querySelectorAll('[data-assign]').forEach(b => b.onclick = () => openResched(b.dataset.assign, `Assign a time — ${b.dataset.name}`));
            const tb = document.getElementById('appts-tbody');
            tb.innerHTML = d.appointments.length ? d.appointments.map(a => `<tr><td>${esc(a.day_label)} · ${esc(a.time_label)}</td><td>${esc(a.patient_name || '—')}</td><td>${esc(a.doctor_name || '—')}</td><td>${pillFor(a.status)}${a.cancel_reason && a.status === 'cancelled' ? ` <span class="muted">${esc(a.cancel_reason)}</span>` : ''}</td>
                <td>${a.status === 'booked' ? `<button class="btn btn-secondary btn-small" data-resched="${esc(a.consultation_id)}" data-name="${esc(a.patient_name || '')}">Reschedule</button>
                    <button class="btn btn-secondary btn-small" data-cancel-appt="${esc(a.id)}">Cancel</button>` : ''}</td></tr>`).join('')
                : '<tr><td colspan="5" class="empty-note">No appointments match.</td></tr>';
            tb.querySelectorAll('[data-resched]').forEach(b => b.onclick = () => openResched(b.dataset.resched, `Reschedule — ${b.dataset.name}`));
            tb.querySelectorAll('[data-cancel-appt]').forEach(btn => btn.onclick = () => {
                if (!confirm('Cancel this appointment? The patient will be able to book another time.')) return;
                api(`/api/appointments/${btn.dataset.cancelAppt}/cancel`, { method: 'POST' }).then(() => { loadAppointments(); loadCapacity(capWeek && capWeek.week_start); }).catch(err => alert(err.message));
            });
        });
    }
    document.getElementById('appt-filter').addEventListener('submit', e => { e.preventDefault(); loadAppointments(); });

    // ------------------------------------------------------------- all accounts (roles & unique IDs)
    function loadUsers() {
        const role = document.getElementById('users-role').value;
        api('/api/admin/users' + (role ? `?role=${role}` : '')).then(d => {
            const roleName = r => ({ patient: 'Patient', clinician: 'Doctor', admin: 'Admin' }[r] || r);
            document.getElementById('users-tbody').innerHTML = d.users.length ? d.users.map(u => `<tr><td><span class="tag">${esc(u.user_code || '—')}</span></td><td>${esc(roleName(u.role))}</td>
                <td>${esc(u.full_name)}</td><td>${esc(u.email)}</td><td class="muted">${esc(u.mrn || '—')}</td><td class="muted">${fmtDate(u.last_login_at)}</td></tr>`).join('')
                : '<tr><td colspan="6" class="empty-note">No accounts.</td></tr>';
        });
    }
    document.getElementById('users-role').addEventListener('change', loadUsers);

    document.getElementById('block-form').addEventListener('submit', e => {
        e.preventDefault();
        const errBox = document.getElementById('block-error');
        errBox.textContent = '';
        const f = Object.fromEntries(new FormData(e.target).entries());
        api('/api/admin/schedule-blocks', { method: 'POST', body: JSON.stringify(f) })
            .then(r => {
            e.target.reset(); loadSchedule(); loadCapacity(capWeek && capWeek.week_start); loadAppointments();
            if (r.conflicts && r.conflicts.length) alert(`${r.conflicts.length} existing appointment(s) fall inside this closure and were NOT cancelled:\n\n` +
                r.conflicts.map(a => `• ${a.patient_name} — ${a.day_label} ${a.time_label} (${a.doctor_name})`).join('\n') + '\n\nReschedule or cancel them from the Appointments list below.');
        })
            .catch(err => errBox.textContent = err.message);
    });

    (async function init() {
        const user = await window.requireAuth('admin');
        if (!user) return;
        const bar = document.getElementById('session-bar');
        if (bar) bar.innerHTML = `${esc(user.full_name)} · ID ${esc(user.user_code || '')} · <a href="#" id="logout-link">Sign out</a>`;
        document.getElementById('logout-link')?.addEventListener('click', e => { e.preventDefault(); window.clarityLogout(); });
        loadClinicians();
        loadSchedule();
        loadCapacity();
        loadAppointments();
        loadUsers();
    })();
})();
