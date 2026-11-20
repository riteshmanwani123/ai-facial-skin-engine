/* eye-safe-capture.js — the browser half of the "we never capture your eyes" guarantee.

   What it does
     * Runs MediaPipe's Face Landmarker (full 478-point mesh, vendored locally under /vendor/mediapipe —
       no CDN, works offline) on every live camera frame.
     * Paints the eye openings solid black on a canvas BEFORE anything is displayed as "the scan", and the
       photo that is saved/sent is read back from that already-painted canvas. The raw camera frame is
       never exported, uploaded or stored.
     * Fails closed: a photo can only be taken from a frame in which exactly one face was found and both
       eyes are inside the frame. If detection fails, capture is refused — it never falls back to an
       unredacted frame.
     * Works out the head pose (yaw/pitch in degrees) so the guided flow can check that a "left cheek",
       "chin up", etc. photo really is that angle.
     * The same redaction runs on photos uploaded as a fallback (redactFile), so the upload path cannot
       skip it either.

   The server repeats an independent eye check on everything it receives (privacy.py).

   Exposes window.EyeSafeCapture. */
(function () {
    'use strict';

    // MediaPipe's WASM runtime logs plain "INFO: ..." lines through console.error. They are not errors, but
    // they trip error overlays, so route them to console.info.
    if (!console.error.__clarityFiltered) {
        const origError = console.error;
        const filtered = function (...a) { return (typeof a[0] === 'string' && a[0].startsWith('INFO:')) ? console.info(...a) : origError.apply(console, a); };
        filtered.__clarityFiltered = true;
        console.error = filtered;
    }

    const SCRIPT_SRC = document.currentScript ? document.currentScript.src : '';
    // /js/eye-safe-capture.js -> /vendor/mediapipe/   and   /static/js/... -> /static/vendor/mediapipe/
    const VENDOR = SCRIPT_SRC ? new URL('../vendor/mediapipe/', SCRIPT_SRC).href : '/vendor/mediapipe/';

    // Face-mesh landmark indices: outline of each eye opening, irises, and a few anchors for pose.
    const RIGHT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246];
    const LEFT_EYE = [263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466];
    const RIGHT_IRIS = [468, 469, 470, 471, 472];
    const LEFT_IRIS = [473, 474, 475, 476, 477];
    const NOSE_TIP = 1, FACE_EDGE_R = 234, FACE_EDGE_L = 454, FOREHEAD = 10, CHIN = 152;

    // Safety margin around each eye outline (multiples of its size from its centre). Generous sideways and
    // upward (lid, lashes, crease); tight downward so under-eye skin stays visible to the skin models.
    const GROW_X = 1.45, GROW_UP = 2.4, GROW_DOWN = 1.45;

    // Required head angle per view, in degrees. yaw > 0 = patient's RIGHT cheek toward the camera,
    // yaw < 0 = LEFT cheek; pitch > 0 = chin up, pitch < 0 = chin down. Tune here if real-world use needs it.
    const POSE_RULES = {
        // The mesh's pitch reads about -10 degrees for a person looking straight into a normal selfie camera
        // (measured on 46 real faces: frontal median -9.7), so "neutral" is centred there, not on 0.
        front: { maxAbsYaw: 15, minPitch: -22, maxPitch: 5 },
        left: { maxYaw: -18 },
        right: { minYaw: 18 },
        up: { minPitch: 6 },
        down: { maxPitch: -22 },
    };

    // Auto-capture tuning.
    const DWELL_MS = 700;          // pose must hold this long before the best frame is taken
    const GAP_MS = 180;            // brief tracking hiccups inside the dwell are tolerated
    const COOLDOWN_MS = 1300;      // after a capture, ignore frames while the user switches angle
    const FALLBACK_MS = 15000;     // nobody gets stuck: after this, the closest-to-right frame is taken
    const STILL_SPEED = 0.025;     // eye travel per frame (in eye-distances) still counted as "holding still"
    const BLUR_SPEED = 0.12;       // faster than this the mesh can't be trusted: the frame is hidden entirely

    const MAX_EXPORT_SIDE = 1280;
    const JPEG_QUALITY = 0.92;

    // ------------------------------------------------------------------ model loading
    const landmarkers = {};
    function getLandmarker(mode) {
        if (!landmarkers[mode]) {
            landmarkers[mode] = (async () => {
                const mod = await import(VENDOR + 'vision_bundle.mjs');
                const fileset = await mod.FilesetResolver.forVisionTasks(VENDOR + 'wasm');
                return mod.FaceLandmarker.createFromOptions(fileset, {
                    baseOptions: { modelAssetPath: VENDOR + 'face_landmarker.task', delegate: 'CPU' },
                    runningMode: mode, numFaces: 2,
                    minFaceDetectionConfidence: 0.4, minFacePresenceConfidence: 0.4, minTrackingConfidence: 0.4,
                    outputFacialTransformationMatrixes: true,
                });
            })();
        }
        return landmarkers[mode];
    }

    // ------------------------------------------------------------------ geometry
    function eyePolygons(face, W, H) {
        const polys = [];
        for (const [eye, iris] of [[RIGHT_EYE, RIGHT_IRIS], [LEFT_EYE, LEFT_IRIS]]) {
            const pts = eye.map(i => [face[i].x * W, face[i].y * H]);
            const irisPts = face.length > Math.max(...iris) ? iris.map(i => [face[i].x * W, face[i].y * H]) : [];
            const all = pts.concat(irisPts);
            const cx = all.reduce((s, p) => s + p[0], 0) / all.length;
            const cy = all.reduce((s, p) => s + p[1], 0) / all.length;
            const grown = pts.map(([x, y]) => [cx + (x - cx) * GROW_X, cy + (y - cy) * (y < cy ? GROW_UP : GROW_DOWN)]);
            irisPts.forEach(([x, y]) => grown.push([cx + (x - cx) * GROW_X * 1.15, cy + (y - cy) * (y < cy ? GROW_UP : GROW_DOWN) * 1.15]));
            polys.push(convexHull(grown));
        }
        return polys;
    }

    function convexHull(points) {
        const pts = points.map(p => [Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100])
            .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
        const lower = [], upper = [];
        for (const p of pts) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }
        for (const p of pts.slice().reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }
        return lower.slice(0, -1).concat(upper.slice(0, -1));
    }

    function inflate(poly, k) {
        const cx = poly.reduce((a, q) => a + q[0], 0) / poly.length, cy = poly.reduce((a, q) => a + q[1], 0) / poly.length;
        return poly.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k]);
    }

    function paintPolygons(ctx, polys) {
        ctx.save();
        ctx.fillStyle = '#000'; ctx.strokeStyle = '#000'; ctx.lineJoin = 'round';
        for (const poly of polys) {
            ctx.beginPath();
            poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
            ctx.closePath();
            const xs = poly.map(q => q[0]); // a rim around each patch absorbs sub-pixel tracking jitter and anti-aliased edges
            ctx.lineWidth = Math.max(3, 0.16 * (Math.max(...xs) - Math.min(...xs)));
            ctx.fill(); ctx.stroke();
        }
        ctx.restore();
    }

    // Both eyes must be fully inside the frame, with a small margin, or the mask could be clipped/unreliable.
    function eyesInsideFrame(face, W, H) {
        const m = 0.005;
        return [...RIGHT_EYE, ...LEFT_EYE].every(i => face[i].x > m && face[i].x < 1 - m && face[i].y > m && face[i].y < 1 - m);
    }

    // ------------------------------------------------------------------ head pose
    // Preferred: rotation from MediaPipe's facial transformation matrix (degrees, independent of face
    // proportions). Fallback: nose position relative to the face outline.
    function poseFromMatrix(m) {
        if (!m || !m.data || m.data.length < 16) return null;
        const d = m.data; // column-major 4x4; third column = where the face's forward axis points
        const fx = d[8], fy = d[9], fz = d[10];
        const yaw = Math.atan2(fx, fz) * 180 / Math.PI;
        const pitch = Math.atan2(fy, Math.hypot(fx, fz)) * 180 / Math.PI;
        return { yaw, pitch, source: 'matrix' };
    }

    function poseFromLandmarks(face) {
        const f = (face[NOSE_TIP].x - face[FACE_EDGE_R].x) / ((face[FACE_EDGE_L].x - face[FACE_EDGE_R].x) || 1e-6);
        const g = (face[NOSE_TIP].y - face[FOREHEAD].y) / ((face[CHIN].y - face[FOREHEAD].y) || 1e-6);
        // Rough degrees: neutral f~0.5 / g~0.55; slopes from simple head-geometry (see notes in README)
        return { yaw: (f - 0.5) * 120, pitch: -(g - 0.55) * 150, source: 'landmarks', f, g };
    }

    function poseOf(result, idx) {
        const m = result.facialTransformationMatrixes && result.facialTransformationMatrixes[idx];
        return poseFromMatrix(m) || poseFromLandmarks(result.faceLandmarks[idx]);
    }

    function poseMatches(view, pose) {
        const r = POSE_RULES[view];
        if (!r || !pose) return true;
        if (r.maxAbsYaw !== undefined && Math.abs(pose.yaw) > r.maxAbsYaw) return false;
        if (r.maxAbsPitch !== undefined && Math.abs(pose.pitch) > r.maxAbsPitch) return false;
        if (r.minYaw !== undefined && !(pose.yaw >= r.minYaw)) return false;
        if (r.maxYaw !== undefined && !(pose.yaw <= r.maxYaw)) return false;
        if (r.minPitch !== undefined && !(pose.pitch >= r.minPitch)) return false;
        if (r.maxPitch !== undefined && !(pose.pitch <= r.maxPitch)) return false;
        return true;
    }

    function exportCanvas(canvas) {
        let src = canvas;
        const side = Math.max(canvas.width, canvas.height);
        if (side > MAX_EXPORT_SIDE) {
            const s = MAX_EXPORT_SIDE / side;
            src = document.createElement('canvas');
            src.width = Math.round(canvas.width * s); src.height = Math.round(canvas.height * s);
            src.getContext('2d').drawImage(canvas, 0, 0, src.width, src.height);
        }
        return { dataUrl: src.toDataURL('image/jpeg', JPEG_QUALITY), scale: src.width / canvas.width };
    }

    // Polygons reported to the server include the painted rim (see paintPolygons), so the server's skin-fill for
    // the analysis copy covers the whole black patch, not just its core.
    function scalePolys(polys, s) { return polys.map(p => inflate(p, 1.18).map(([x, y]) => [Math.round(x * s * 10) / 10, Math.round(y * s * 10) / 10])); }

    // How many degrees the pose is outside the range a view needs (0 = inside), and which way to move.
    function poseGap(view, pose) {
        const r = POSE_RULES[view];
        if (!r || !pose) return { dist: 0, yaw: 0, pitch: 0 };
        let yaw = 0, pitch = 0; // signed correction needed (+ = increase)
        if (r.maxAbsYaw !== undefined && Math.abs(pose.yaw) > r.maxAbsYaw) yaw = -Math.sign(pose.yaw) * (Math.abs(pose.yaw) - r.maxAbsYaw);
        if (r.minYaw !== undefined && pose.yaw < r.minYaw) yaw = r.minYaw - pose.yaw;
        if (r.maxYaw !== undefined && pose.yaw > r.maxYaw) yaw = r.maxYaw - pose.yaw;
        if (r.minPitch !== undefined && pose.pitch < r.minPitch) pitch = r.minPitch - pose.pitch;
        if (r.maxPitch !== undefined && pose.pitch > r.maxPitch) pitch = r.maxPitch - pose.pitch;
        return { dist: Math.hypot(yaw, pitch), yaw, pitch };
    }

    // Plain-language nudge. Yaw>0 = patient's RIGHT cheek toward the camera (they turned to their LEFT).
    function guideText(view, gap) {
        if (gap.dist === 0) return '';
        if (Math.abs(gap.yaw) >= Math.abs(gap.pitch)) {
            if (view === 'front') return gap.yaw > 0 ? 'Turn a little to your left — face the camera straight on' : 'Turn a little to your right — face the camera straight on';
            return gap.yaw > 0 ? 'Turn a bit more to your left' : 'Turn a bit more to your right';
        }
        if (view === 'up') return 'Lift your chin a bit more';
        if (view === 'down') return 'Lower your chin a bit more';
        return gap.pitch > 0 ? 'Lift your chin slightly' : 'Lower your chin slightly';
    }

    // ------------------------------------------------------------------ live camera session
    // Hands-free: the person only follows the on-screen direction. Once the head angle for the requested view
    // is reached and held, the session picks the sharpest, best-lit, best-centred frame from that hold and
    // reports it through onAuto — the person never has to press anything.
    //
    // Eye safety is applied to EVERY frame that reaches the screen, before it is shown:
    //   * the eyes are painted from the mesh found in that very canvas (detect and paint use the same pixels);
    //   * while the head is moving, the previous frame's patches are painted too and the patches are enlarged,
    //     so the eye can't peek out on the side the mesh hasn't caught up with;
    //   * if no single face is tracked, or the head is moving too fast to trust the mesh, the whole frame is
    //     replaced by a coarse mosaic — never the raw camera image.
    class LiveSession {
        constructor(videoEl, canvasEl, onUpdate, onAuto) {
            this.video = videoEl; this.canvas = canvasEl; this.ctx = canvasEl.getContext('2d');
            this.onUpdate = onUpdate || (() => { }); this.onAuto = onAuto || null; this.target = 'front';
            this.stream = null; this.running = false; this.lastTs = 0; this.lastUi = 0;
            this.good = document.createElement('canvas');   // latest safe frame, already painted
            this.best = document.createElement('canvas');   // best frame of the current hold, already painted
            this.nearest = document.createElement('canvas'); // closest-to-right frame seen for this view (fallback)
            this.tiny = document.createElement('canvas'); this.tiny.width = 24; this.tiny.height = 18;
            this.probe = document.createElement('canvas'); this.probe.width = this.probe.height = 96;
            this.goodInfo = null; this.bestInfo = null; this.nearestInfo = null;
            this.prev = null;           // { centres, iod, polys }
            this.state = { face: false, faces: 0, eyesOk: false, safe: false, poseOk: false, pose: null, reason: 'Starting camera…', progress: 0, guide: '', fit: 'ok', still: true };
            this._resetHold(); this.targetSince = performance.now(); this.cooldownUntil = 0;
        }

        async start(stream) {
            this.stream = stream;
            this.video.srcObject = stream;
            await this.video.play();
            this.landmarker = await getLandmarker('VIDEO');
            this.running = true;
            const tick = () => { if (!this.running) return; try { this._frame(); } catch (e) { console.error('eye-safe-capture frame error', e); this._set({ safe: false, reason: 'Camera error — please retry' }); } requestAnimationFrame(tick); };
            requestAnimationFrame(tick);
        }

        _resetHold() { this.okSince = null; this.lastOk = 0; this.bestScore = -1; this.bestInfo = null; this.nearestGap = Infinity; this.nearestInfo = null; }

        setTarget(view) {
            this.target = view;
            this._resetHold(); this.targetSince = performance.now();
            this.cooldownUntil = Math.max(this.cooldownUntil || 0, this.targetSince + 600);
            // Re-judge the last frame against the new angle right away, so a stale "angle OK" from the
            // previous view can never let a capture through before the next frame is analysed.
            this.state.poseOk = this.state.pose ? poseMatches(view, this.state.pose) : false;
            this.state.progress = 0;
            this.onUpdate(Object.assign({}, this.state));
        }

        stop() {
            this.running = false;
            if (this.stream) this.stream.getTracks().forEach(t => t.stop());
            this.stream = null; this.video.srcObject = null; this.goodInfo = null; this.bestInfo = null; this.nearestInfo = null; this.prev = null;
        }

        _set(patch) {
            Object.assign(this.state, patch);
            const now = performance.now();
            if (now - this.lastUi > 90) { this.lastUi = now; this.onUpdate(Object.assign({}, this.state)); }
        }

        /** Replaces the frame with a coarse mosaic: enough to see where your head is, not enough to see an eye. */
        _mosaic(W, H) {
            const t = this.tiny.getContext('2d');
            t.imageSmoothingEnabled = true; t.drawImage(this.canvas, 0, 0, this.tiny.width, this.tiny.height);
            this.ctx.save(); this.ctx.imageSmoothingEnabled = false;
            this.ctx.drawImage(this.tiny, 0, 0, this.tiny.width, this.tiny.height, 0, 0, W, H);
            this.ctx.fillStyle = 'rgba(0,0,0,0.35)'; this.ctx.fillRect(0, 0, W, H);
            this.ctx.restore();
        }

        _faceBox(face, W, H) {
            let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
            for (let i = 0; i < face.length; i += 4) { const q = face[i]; if (q.x < x0) x0 = q.x; if (q.x > x1) x1 = q.x; if (q.y < y0) y0 = q.y; if (q.y > y1) y1 = q.y; }
            return { x: Math.max(0, x0) * W, y: Math.max(0, y0) * H, w: (Math.min(1, x1) - Math.max(0, x0)) * W, h: (Math.min(1, y1) - Math.max(0, y0)) * H };
        }

        /** Sharpness (variance of a Laplacian) and brightness of the face region, from the already-painted canvas. */
        _quality(box) {
            const c = this.probe, g = c.getContext('2d', { willReadFrequently: true }); const N = c.width;
            g.drawImage(this.canvas, box.x, box.y, Math.max(8, box.w), Math.max(8, box.h), 0, 0, N, N);
            const d = g.getImageData(0, 0, N, N).data; const L = new Float32Array(N * N); let mean = 0;
            for (let i = 0; i < N * N; i++) { const v = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2]; L[i] = v; mean += v; }
            mean /= N * N; let s = 0, s2 = 0, n = 0;
            for (let y = 1; y < N - 1; y++) for (let x = 1; x < N - 1; x++) { const i = y * N + x; const lap = 4 * L[i] - L[i - 1] - L[i + 1] - L[i - N] - L[i + N]; s += lap; s2 += lap * lap; n++; }
            return { sharp: s2 / n - (s / n) * (s / n), light: mean };
        }

        _frame() {
            const v = this.video;
            if (!v.videoWidth || v.readyState < 2) return;
            if (this.canvas.width !== v.videoWidth) {
                this.canvas.width = v.videoWidth; this.canvas.height = v.videoHeight;
                for (const c of [this.good, this.best, this.nearest]) { c.width = v.videoWidth; c.height = v.videoHeight; }
            }
            const W = this.canvas.width, H = this.canvas.height, now = performance.now();
            const ts = Math.max(this.lastTs + 1, Math.round(now)); this.lastTs = ts;
            // Draw once, then detect on those very pixels, so the mesh and the picture can never disagree.
            this.ctx.drawImage(v, 0, 0, W, H);
            const res = this.landmarker.detectForVideo(this.canvas, ts);
            const n = res.faceLandmarks ? res.faceLandmarks.length : 0;

            if (n !== 1) {
                this._mosaic(W, H);
                this.goodInfo = null; this.prev = null; this.okSince = null; this.tracked = 0;
                return this._set({ face: n > 0, faces: n, eyesOk: false, safe: false, poseOk: false, pose: null, progress: 0, guide: '', fit: 'ok',
                    reason: n === 0 ? 'No face found — centre your face in the frame' : 'Only one person should be in the frame' });
            }
            const face = res.faceLandmarks[0];
            // The mesh is least reliable in the first frames after a face is (re)acquired: keep the picture hidden until it settles.
            this.tracked = (this.tracked || 0) + 1;
            let polys = eyePolygons(face, W, H);

            // Head-motion safety.
            const centres = polys.map(pl => [pl.reduce((a, q) => a + q[0], 0) / pl.length, pl.reduce((a, q) => a + q[1], 0) / pl.length]);
            const iod = Math.hypot(centres[0][0] - centres[1][0], centres[0][1] - centres[1][1]) || 1;
            let speed = 0;
            if (this.prev) speed = Math.max(Math.hypot(centres[0][0] - this.prev.centres[0][0], centres[0][1] - this.prev.centres[0][1]), Math.hypot(centres[1][0] - this.prev.centres[1][0], centres[1][1] - this.prev.centres[1][1])) / iod;
            const prevPolys = this.prev ? this.prev.polys : null;
            this.prev = { centres, polys };
            if (this.tracked <= 2) {
                this._mosaic(W, H); this.goodInfo = null; this.okSince = null;
                return this._set({ face: true, faces: 1, eyesOk: false, safe: false, poseOk: false, pose: null, progress: 0, guide: '', fit: 'ok', reason: 'Finding your face…' });
            }
            if (speed > BLUR_SPEED) {
                this.tracked = 0;
                this._mosaic(W, H); this.goodInfo = null; this.okSince = null;
                return this._set({ face: true, faces: 1, eyesOk: false, safe: false, poseOk: false, pose: null, progress: 0, guide: '', fit: 'ok', still: false, reason: 'Moving too fast — slow down a little' });
            }
            {
                const k = 1 + Math.min(0.5, speed * 5);
                if (speed > 0.008) polys = polys.map(pl => inflate(pl, k));
                if (prevPolys) polys = polys.concat(prevPolys.map(pl => inflate(pl, k))); // the last frame's patches too, in case the mesh lags
            }
            paintPolygons(this.ctx, polys);

            const eyesOk = eyesInsideFrame(face, W, H);
            const pose = poseOf(res, 0);
            const gap = poseGap(this.target, pose);
            const poseOk = gap.dist === 0;
            const box = this._faceBox(face, W, H);
            const cx = (box.x + box.w / 2) / W, wFrac = box.w / W, hFrac = box.h / H;
            const minW = (this.target === 'left' || this.target === 'right') ? 0.1 : 0.16;
            let fit = 'ok';
            if (hFrac > 0.95 || wFrac > 0.8) fit = 'back'; else if (wFrac < minW && hFrac < 0.3) fit = 'closer'; else if (cx < 0.18 || cx > 0.82) fit = 'centre';
            const still = speed < STILL_SPEED;
            const safe = eyesOk;

            if (safe) { this.good.getContext('2d').drawImage(this.canvas, 0, 0); this.goodInfo = { polys, pose, W, H }; } else this.goodInfo = null;

            let progress = 0, guide = '';
            const cooling = now < this.cooldownUntil;
            if (safe && !cooling) {
                const q = this._quality(box);
                const lightF = q.light < 45 ? 0.4 : q.light > 220 ? 0.6 : 1;
                const score = Math.log1p(Math.max(0, q.sharp)) * lightF * (still ? 1 : 0.6) * (fit === 'ok' ? 1 : 0.7);
                const info = () => ({ polys, pose, W, H, score, poseOk });
                // Fallback candidate: the frame closest to the required angle (ties -> sharper).
                if (fit !== 'back' && (gap.dist < this.nearestGap - 0.5 || (Math.abs(gap.dist - this.nearestGap) <= 0.5 && score > (this.nearestInfo ? this.nearestInfo.score : -1)))) {
                    this.nearestGap = gap.dist; this.nearest.getContext('2d').drawImage(this.canvas, 0, 0); this.nearestInfo = info();
                }
                if (poseOk && (fit === 'ok' || fit === 'closer')) {
                    if (this.okSince === null) { this.okSince = now; this.bestScore = -1; }
                    this.lastOk = now;
                    // Prefer a clearly-held angle: closer to neutral for the front view, further past the threshold otherwise.
                    const bonus = 1 + 0.4 * (this.target === 'front' ? Math.max(0, 1 - Math.hypot(pose.yaw, pose.pitch + 10) / 20) : Math.min(1, this._margin(pose) / 15));
                    if ((still || !this.bestInfo) && score * bonus > this.bestScore) { this.bestScore = score * bonus; this.best.getContext('2d').drawImage(this.canvas, 0, 0); this.bestInfo = info(); }
                    progress = Math.min(1, (now - this.okSince) / DWELL_MS);
                    if (progress >= 1 && this.bestInfo) { this._fire(this.best, this.bestInfo, true); progress = 0; }
                } else if (this.okSince !== null && now - this.lastOk > GAP_MS) {
                    this.okSince = null; this.bestScore = -1; this.bestInfo = null;
                }
                if (progress === 0 && !poseOk) guide = fit !== 'ok' ? '' : guideText(this.target, gap);
                // Never leave anyone stuck.
                if (now - this.targetSince > FALLBACK_MS && this.nearestInfo) { this._fire(this.nearest, this.nearestInfo, this.nearestInfo.poseOk); progress = 0; }
            } else if (!safe) { this.okSince = null; }

            this._set({ face: true, faces: 1, eyesOk, safe, poseOk, pose, progress, guide, fit, still,
                reason: !eyesOk ? 'Move back — both eyes must be fully in the frame' : '' });
        }

        // How far past the required angle the pose is (degrees), for a small "clearly turned" bonus.
        _margin(pose) {
            const r = POSE_RULES[this.target]; if (!r) return 0;
            if (r.minYaw !== undefined) return pose.yaw - r.minYaw;
            if (r.maxYaw !== undefined) return r.maxYaw - pose.yaw;
            if (r.minPitch !== undefined && r.maxPitch === undefined) return pose.pitch - r.minPitch;
            if (r.maxPitch !== undefined && r.minPitch === undefined) return r.maxPitch - pose.pitch;
            return 0;
        }

        _fire(canvas, info, poseOk) {
            this.cooldownUntil = performance.now() + COOLDOWN_MS;
            this._resetHold();
            const { dataUrl, scale } = exportCanvas(canvas);
            const shot = { dataUrl, polygons: scalePolys(info.polys, scale), pose: info.pose, poseOk, auto: true, width: Math.round(info.W * scale), height: Math.round(info.H * scale) };
            if (this.onAuto) this.onAuto(shot);
        }

        /** Manual capture (the optional button): the latest already-redacted frame. Throws if no safe frame exists. */
        capture({ force = false } = {}) {
            if (!this.goodInfo || !this.state.safe) { const e = new Error(this.state.reason || 'No safe frame to capture'); e.code = 'not_safe'; throw e; }
            if (!this.state.poseOk && !force) { const e = new Error('Head angle does not match yet'); e.code = 'pose'; throw e; }
            const { dataUrl, scale } = exportCanvas(this.good);
            return { dataUrl, polygons: scalePolys(this.goodInfo.polys, scale), pose: this.goodInfo.pose, width: Math.round(this.goodInfo.W * scale), height: Math.round(this.goodInfo.H * scale) };
        }
    }

    // ------------------------------------------------------------------ uploaded photos
    async function loadBitmap(file) {
        if (window.createImageBitmap) { try { return await createImageBitmap(file); } catch (e) { /* fall through */ } }
        return new Promise((resolve, reject) => {
            const url = URL.createObjectURL(file); const img = new Image();
            img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
            img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read that image')); };
            img.src = url;
        });
    }

    /** Redacts an uploaded photo in the browser. Rejects (never returns an unredacted image) when no
        single face with both eyes in frame can be found. */
    async function redactFile(file, view) {
        const bmp = await loadBitmap(file);
        const w0 = bmp.width, h0 = bmp.height, s0 = Math.min(1, 1600 / Math.max(w0, h0));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(w0 * s0); canvas.height = Math.round(h0 * s0);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
        const lm = await getLandmarker('IMAGE');
        const res = lm.detect(canvas);
        const n = res.faceLandmarks ? res.faceLandmarks.length : 0;
        const fail = (code, msg) => { const e = new Error(msg); e.code = code; throw e; };
        if (n === 0) fail('no_face', "We couldn't find your face in that photo. Use a clear, well-lit photo where your face is fully visible.");
        if (n > 1) fail('multiple_faces', 'Only one person should be in the photo.');
        const face = res.faceLandmarks[0];
        const polys = eyePolygons(face, canvas.width, canvas.height);
        if (!eyesInsideFrame(face, canvas.width, canvas.height)) fail('eyes_out_of_frame', 'Both eyes must be fully inside the photo.');
        paintPolygons(ctx, polys);
        const pose = poseOf(res, 0);
        const { dataUrl, scale } = exportCanvas(canvas);
        return { dataUrl, polygons: scalePolys(polys, scale), pose, poseOk: poseMatches(view, pose), width: Math.round(canvas.width * scale), height: Math.round(canvas.height * scale) };
    }

    window.EyeSafeCapture = {
        LiveSession, redactFile, poseMatches, POSE_RULES,
        preload: () => Promise.all([getLandmarker('VIDEO'), getLandmarker('IMAGE')]),
        // exposed for the evaluation harness (tests/measurements), not used by the app UI
        _debug: { getLandmarker, eyePolygons, poseOf, poseFromLandmarks, poseFromMatrix },
    };
})();
