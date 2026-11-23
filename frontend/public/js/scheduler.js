/* scheduler.js — the appointment picker used for booking and rescheduling a consultation.

   ClarityScheduler.create(rootEl, { onSelect(slot|null), initial }) renders a Mon–Fri week strip and the day's
   20-minute slots (breaks shown as their own rows) from GET /api/schedule/week. All times come from the server,
   already formatted in clinic time (IST), so the browser's own timezone never shifts a slot.
   The server is the authority: it re-checks everything when the booking is made. */
(function () {
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function create(root, opts = {}) {
        const st = { week: null, dayIdx: 0, selected: null, loading: false, error: '' };
        const onSelect = opts.onSelect || (() => { });

        async function fetchWeek(dateStr) {
            st.loading = true; st.error = ''; paint();
            try {
                const r = await fetch('/api/schedule/week' + (dateStr ? `?date=${encodeURIComponent(dateStr)}` : ''), { credentials: 'include' });
                let d = {};
                const text = await r.text();
                try { d = JSON.parse(text); } catch (_) { d = { detail: text || r.statusText }; }
                if (!r.ok) throw new Error(d.detail || d.error || d.message || 'Could not load times');
                st.week = d;
                // keep the chosen day when paging weeks isn't possible; otherwise land on the first day with room
                const sel = st.selected ? d.days.findIndex(x => x.date === st.selected.date) : -1;
                st.dayIdx = sel >= 0 ? sel : Math.max(0, d.days.findIndex(x => x.available > 0));
            } catch (e) { st.error = e.message; }
            st.loading = false; paint();
        }

        function pick(slot, day) {
            st.selected = slot ? { start: slot.start, label: slot.label, date: day.date, day_label: day.label, tz_label: st.week.tz_label, minutes: st.week.slot_minutes } : null;
            paint(); onSelect(st.selected);
        }

        function jumpToNext() {
            const n = st.week && st.week.next_available;
            if (!n) return;
            const day = n.start.slice(0, 10);   // UTC date can differ from IST date: ask the server for the right week
            fetchWeek(day).then(() => {
                for (const d of st.week.days) for (const it of d.items) if (it.kind === 'slot' && it.start === n.start) { st.dayIdx = st.week.days.indexOf(d); pick(it, d); return; }
            });
        }

        // Slots that have already passed are noise: hide them (and any break left dangling before the first visible slot).
        function visibleItems(day) {
            const items = day.items.filter(it => !(it.kind === 'slot' && it.status === 'past'));
            while (items.length && items[0].kind === 'break') items.shift();
            return items;
        }

        function paint() {
            if (st.error) { root.innerHTML = `<div class="sched-err">${esc(st.error)} <button type="button" class="link-btn" data-act="retry">Try again</button></div>`; bind(); return; }
            if (!st.week) { root.innerHTML = '<div class="sched-loading">Loading available times…</div>'; return; }
            const w = st.week, day = w.days[st.dayIdx];
            const month = new Date(w.week_start + 'T00:00:00').toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
            root.innerHTML = `
                <div class="sched-head">
                    <div><div class="sched-title">Choose a time</div><div class="muted">Dermatologist consultation · ${w.slot_minutes} minutes · ${esc(w.tz_label)}</div></div>
                    ${w.next_available ? `<button type="button" class="btn btn-secondary btn-small" data-act="next">Next available</button>` : ''}
                </div>
                <div class="sched-weeknav">
                    <button type="button" class="link-btn" data-act="prev" ${w.prev ? '' : 'disabled'}>‹ Previous week</button>
                    <span class="sched-month">${esc(month)}</span>
                    <button type="button" class="link-btn" data-act="nextweek" ${w.next ? '' : 'disabled'}>Next week ›</button>
                </div>
                <div class="sched-days" role="tablist">
                    ${w.days.map((d, i) => `<button type="button" role="tab" class="sched-day ${i === st.dayIdx ? 'on' : ''} ${d.available ? '' : 'none'}" data-day="${i}" aria-selected="${i === st.dayIdx}">
                        <span class="sd-wd">${esc(d.weekday)}</span><span class="sd-n">${d.day}</span>
                        <span class="sd-c">${d.closed_reason ? 'Closed' : d.available ? d.available + ' open' : d.past ? 'Past' : 'Full'}</span></button>`).join('')}
                </div>
                <div class="sched-slots">
                    ${day.closed_reason && !day.available ? `<div class="sched-note">Closed — ${esc(day.closed_reason)}</div>` : ''}
                    ${visibleItems(day).map(it => it.kind === 'break'
                        ? `<div class="sched-break"><span>${esc(it.label)}</span><span>${esc(it.range)}</span></div>`
                        : `<button type="button" class="sched-slot ${it.status} ${st.selected && st.selected.start === it.start ? 'on' : ''}" data-start="${esc(it.start)}" ${it.status === 'available' ? '' : 'disabled'}>${esc(it.label)}</button>`).join('')}
                </div>
                <div class="sched-legend"><span><i class="lg avail"></i> Available</span><span><i class="lg taken"></i> Booked / unavailable</span><span><i class="lg brk"></i> Break</span></div>`;
            bind();
        }

        function bind() {
            root.querySelectorAll('[data-act]').forEach(b => b.onclick = () => {
                const a = b.dataset.act;
                if (a === 'retry') fetchWeek(st.week && st.week.week_start);
                if (a === 'prev') fetchWeek(st.week.prev);
                if (a === 'nextweek') fetchWeek(st.week.next);
                if (a === 'next') jumpToNext();
            });
            root.querySelectorAll('[data-day]').forEach(b => b.onclick = () => { st.dayIdx = Number(b.dataset.day); paint(); });
            root.querySelectorAll('.sched-slot.available').forEach(b => b.onclick = () => {
                const day = st.week.days[st.dayIdx], it = day.items.find(x => x.start === b.dataset.start);
                pick(it, day);
            });
        }

        return {
            load: (dateStr) => fetchWeek(dateStr),
            reload: () => fetchWeek(st.week && st.week.week_start),
            clear: () => { st.selected = null; paint(); onSelect(null); },
            selected: () => st.selected,
        };
    }

    window.ClarityScheduler = { create };
})();
