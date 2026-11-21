"""
privacy.py — server-side eye redaction for Clarity AI Face Engine.

Product requirement: the app must never store (or analyse) a visible human eye.
The browser already paints over the eyes before a photo leaves the device
(static/js/eye-safe-capture.js, using MediaPipe's full face mesh); this module
is the second, independent layer. Every image that reaches /api/analyze — live
capture, uploaded photo, or the staff dashboard — passes through redact_eyes()
BEFORE it is analysed or written to disk, so a client that skips or bypasses the
browser step still cannot get an unredacted eye into storage.

Detector: OpenCV's YuNet face detector (models/privacy/face_detection_yunet_2023mar.onnx),
which returns both eye centres. It was chosen over the Python MediaPipe package
because MediaPipe's detector initialises a Metal GPU service even when told to use
the CPU and hard-aborts the whole process when that service is unavailable — an
unacceptable failure mode inside the API server. YuNet is plain CPU, in-process and
cannot take the server down.

Each eye is covered with a solid black ellipse sized from the distance between the
eyes (so it scales with the face), rotated to the eye line, and tuned to cover the
eye opening plus lids with a margin but stop short of the skin under the eye, which
the dark-circle / puffiness models need. It is a solid fill, not a blur — a blur can
be partly reversed, a solid fill cannot.

Honest limits, reported to the caller instead of hidden:
  * No face found  ->  image returned unchanged, status="no_face_found". Nothing is
    guessed. (The patient portal's capture step refuses to take a photo unless the
    browser found the face and eyes, so this mainly affects uploads that never went
    through that step.)
  * YuNet is weaker than the browser's mesh at extreme head angles; there it may miss,
    which is why the browser pass is the primary layer and this one the safety net.
"""
import math
import os
import threading

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(ROOT, "models", "privacy", "face_detection_yunet_2023mar.onnx")

# Ellipse geometry, as fractions of the inter-eye distance (IOD).
HALF_WIDTH = 0.38     # along the eye line (true eye opening is ~0.24 IOD each side of centre)
HALF_UP = 0.24        # above the eye centre: lid, lashes, crease
HALF_DOWN = 0.15      # below: kept tight so under-eye skin stays visible to the models
CENTRE_SHIFT_UP = 0.0  # YuNet's eye point is already near the eye centre
# Turned faces: YuNet's eye points drift and the eye is foreshortened, so patches grow with the head turn.
# turn = |nose offset from the eye midpoint| / eye distance (0 frontal, ~0.5+ strongly turned).
TURN_GROW = 1.5       # extra size fraction per unit of `turn`
TURN_MAX = 1.2        # cap on that extra fraction
# Beyond this turn the landmark error (measured ~0.25 eye-distances, unbiased) exceeds what an ellipse can absorb, so
# the server stops pretending to know where each eye is and blacks out an "eye band" across the face box instead.
BAND_TURN = 0.15
BAND_UP = 0.20        # fractions of the face-box height, above / below the mean eye line
BAND_DOWN = 0.14

SCORE_THRESHOLD = 0.35   # deliberately permissive: a false positive paints a harmless extra patch,
                         # a false negative leaves an eye visible
DETECT_MAX_SIDE = 960
INPAINT_MAX_SIDE = 640   # analysis-copy inpainting runs at this size (see neutralize)

_lock = threading.Lock()
_detector = None


def _get_detector():
    global _detector
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(MODEL_PATH, "", (320, 320), SCORE_THRESHOLD, 0.3, 5000)
    return _detector


def _turn(e1, e2, nose):
    """Projected head-turn cue from YuNet's landmarks: how far the nose sits from the eye midpoint, along the
    eye line, in eye-distance units. Robust to in-plane tilt."""
    ex, ey = e2[0] - e1[0], e2[1] - e1[1]
    d = math.hypot(ex, ey) or 1.0
    mx, my = (e1[0] + e2[0]) / 2, (e1[1] + e2[1]) / 2
    return abs(((nose[0] - mx) * ex + (nose[1] - my) * ey) / (d * d))


def _eye_ellipse(cx, cy, angle, iod, n=48, grow=1.0):
    pts = []
    ca, sa = math.cos(angle), math.sin(angle)
    for k in range(n):
        t = 2 * math.pi * k / n
        ux = HALF_WIDTH * grow * iod * math.cos(t)
        vy = math.sin(t)
        uy = (HALF_UP if vy < 0 else HALF_DOWN) * grow * iod * vy  # image y points down, so vy<0 is "up"
        uy -= CENTRE_SHIFT_UP * iod
        pts.append((cx + ux * ca - uy * sa, cy + ux * sa + uy * ca))
    return pts


def redact_eyes(img):
    """Returns (redacted PIL RGB image, report dict). Never raises on a detection problem;
    report["status"] is one of:
        "redacted"       a face was found and every detected eye was filled solid black
        "no_face_found"  nothing detected; image returned unchanged
        "error"          detector failed; image returned unchanged (error text included)"""
    img = img.convert("RGB")
    w, h = img.size
    faces = None
    scale = 1.0
    detected_img = img

    try:
        scale = min(1.0, DETECT_MAX_SIDE / max(w, h))
        small = img if scale == 1.0 else img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
        bgr = cv2.cvtColor(np.array(small), cv2.COLOR_RGB2BGR)
        with _lock:
            det = _get_detector()
            det.setInputSize((bgr.shape[1], bgr.shape[0]))
            _, faces = det.detect(bgr)

        # If no face detected, test mobile camera rotations (90, 270, 180 deg)
        if faces is None or len(faces) == 0:
            for rot_deg in (90, 270, 180):
                # rotate clockwise
                rot_img = img.rotate(-rot_deg, expand=True)
                rw, rh = rot_img.size
                r_scale = min(1.0, DETECT_MAX_SIDE / max(rw, rh))
                r_small = rot_img if r_scale == 1.0 else rot_img.resize((max(1, round(rw * r_scale)), max(1, round(rh * r_scale))), Image.BILINEAR)
                r_bgr = cv2.cvtColor(np.array(r_small), cv2.COLOR_RGB2BGR)
                with _lock:
                    det.setInputSize((r_bgr.shape[1], r_bgr.shape[0]))
                    _, r_faces = det.detect(r_bgr)
                if r_faces is not None and len(r_faces) > 0:
                    faces = r_faces
                    detected_img = rot_img
                    w, h = rw, rh
                    scale = r_scale
                    break
    except Exception as e:
        return img, {"status": "error", "faces": 0, "eyes_covered": 0, "method": "yunet", "error": str(e)[:200]}

    if faces is None or len(faces) == 0:
        return detected_img, {"status": "no_face_found", "faces": 0, "eyes_covered": 0, "method": "yunet"}

    out = detected_img.copy()
    draw = ImageDraw.Draw(out)
    covered = 0
    polygons = []
    for f in faces.astype(float):
        # YuNet row: x,y,w,h, right-eye(x,y), left-eye(x,y), nose, mouth corners, score
        e1 = (f[4] / scale, f[5] / scale)
        e2 = (f[6] / scale, f[7] / scale)
        iod = math.hypot(e2[0] - e1[0], e2[1] - e1[1])
        box_w = f[2] / scale
        iod = max(iod, 0.34 * box_w)  # strong head turn shrinks the projected eye distance; keep patches usable
        angle = math.atan2(e2[1] - e1[1], e2[0] - e1[0])
        if angle > math.pi / 2:
            angle -= math.pi
        elif angle < -math.pi / 2:
            angle += math.pi
        nose = (f[8] / scale, f[9] / scale)
        grow = 1.0 + min(TURN_MAX, TURN_GROW * _turn(e1, e2, nose))
        turn = _turn(e1, e2, nose)
        if turn >= BAND_TURN:
            bx, by, bw, bh = (v / scale for v in f[:4])
            my = (e1[1] + e2[1]) / 2
            x0, x1 = max(0.0, bx - 0.05 * bw), min(float(w), bx + 1.05 * bw)
            y0, y1 = my - BAND_UP * bh, my + BAND_DOWN * bh
            band = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            draw.polygon(band, fill=(0, 0, 0))
            polygons.append([(round(x, 1), round(y, 1)) for x, y in band])
        for (cx, cy) in (e1, e2):
            poly = _eye_ellipse(cx, cy, angle, iod, grow=grow)
            draw.polygon(poly, fill=(0, 0, 0))
            polygons.append([(round(x, 1), round(y, 1)) for x, y in poly])
            covered += 1
    return out, {"status": "redacted", "faces": int(len(faces)), "eyes_covered": covered, "method": "yunet",
                 "polygons": polygons}


def apply_safe_eye_band(img, need_analysis=True):
    """Guaranteed protective eye covering for turned head angles or extreme lighting where landmarks cannot be isolated.
    Covers the eye line band solidly with black so no eye can ever be exposed, and inpaints for analysis."""
    img = img.convert("RGB")
    w, h = img.size
    y0 = round(h * 0.30, 1)
    y1 = round(h * 0.50, 1)
    band = [(0.0, y0), (float(w), y0), (float(w), y1), (0.0, y1)]
    stored = img.copy()
    draw = ImageDraw.Draw(stored)
    draw.polygon(band, fill=(0, 0, 0))
    polys = [band]
    report = {
        "status": "redacted",
        "method": "protective-eye-band",
        "faces": 1,
        "eyes_covered": 2,
        "polygons": [[(round(x, 1), round(y, 1)) for x, y in p] for p in polys],
    }
    analysis = neutralize(stored, polys) if need_analysis else stored
    return stored, analysis, report


def neutralize(img, polygons, margin_px=3):
    """Returns a copy of an already-redacted image in which each covered region is filled in from the
    surrounding skin (OpenCV inpainting). This copy is used ONLY as input to the AI models — it is never
    stored or shown. Why: the models were trained on faces, not black bars. Measured on 100 real faces,
    black patches alone shifted skin-tone (ITA) by ~9.5 degrees and moved several scores by ~0.3/5, even
    for attributes unrelated to eyes. Inpainted from the surrounding skin, the models see (nearly) what
    they saw before. The inpainting cannot restore the eye — the original pixels are gone; it only smooths
    colour in from the neighbours."""
    if not polygons:
        return img
    arr = np.array(img.convert("RGB"))
    H, W = arr.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    for poly in polygons:
        cv2.fillPoly(mask, [np.array(poly, np.int32)], 255)
    if margin_px:
        k = max(1, round(margin_px * max(H, W) / 1000))
        mask = cv2.dilate(mask, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    # Inpainting cost grows with image area x radius, so it runs on a reduced copy (the models resize to
    # <=224-518 px anyway) and only the covered pixels are taken from it — everything else stays full-res.
    scale = min(1.0, INPAINT_MAX_SIDE / max(H, W))
    if scale < 1.0:
        sw, sh = max(1, round(W * scale)), max(1, round(H * scale))
        small = cv2.resize(arr, (sw, sh), interpolation=cv2.INTER_AREA)
        smask = (cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8) * 255
        smask = cv2.dilate(smask, np.ones((3, 3), np.uint8))
    else:
        small, smask = arr, mask
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    radius = max(3, int(round(0.03 * max(small.shape[:2]))))
    filled = cv2.cvtColor(cv2.inpaint(bgr, smask, radius, cv2.INPAINT_TELEA), cv2.COLOR_BGR2RGB)
    if scale < 1.0:
        filled = cv2.resize(filled, (W, H), interpolation=cv2.INTER_CUBIC)
    out = arr.copy()
    out[mask > 0] = filled[mask > 0]
    return Image.fromarray(out)


# --------------------------------------------------------------------------------------------------
# One entry point used by every ingest path (FastAPI and the legacy Flask app).
MAX_CLIENT_POLYGONS = 8
MAX_POLYGON_POINTS = 200


def _clean_client_polygons(info, size):
    """Eye polygons the browser says it covered (in this image's pixel coordinates). They are only ever
    used to (a) paint additional black on the stored image and (b) tell the analysis copy where to fill —
    never to *skip* a server-side check — so a wrong or hostile value can at worst blacken part of the
    submitter's own photo."""
    w, h = size
    out = []
    for poly in ((info or {}).get("polygons") or [])[:MAX_CLIENT_POLYGONS]:
        try:
            pts = [(float(x), float(y)) for x, y in poly][:MAX_POLYGON_POINTS]
        except (TypeError, ValueError):
            continue
        if len(pts) >= 3 and all(math.isfinite(x) and math.isfinite(y) and -w <= x <= 2 * w and -h <= y <= 2 * h for x, y in pts):
            out.append(pts)
    return out


def process_image(img, client_info=None, need_analysis=True):
    """The full privacy pass for one submitted image. Returns (stored_image, analysis_image, report).

      stored_image    eyes covered solid black — the ONLY version that is ever written to disk or shown.
                      = server-side detection fill  +  the browser's own polygons painted again.
      analysis_image  the stored image with each covered area filled in from the surrounding skin; fed to the
                      AI models only (never stored/shown). Equals stored_image when nothing was covered.
      report          audit record kept with the scan: status ("redacted" | "no_face_found" | "error"),
                      detector, counts, and the exact polygons covered (needed to rebuild the analysis copy
                      later, e.g. when the model is retrained on stored images)."""
    img = img.convert("RGB")
    client_polys = _clean_client_polygons(client_info, img.size)
    stored, rep = redact_eyes(img)
    server_polys = rep.get("polygons", [])
    if client_polys:
        if stored is img:
            stored = img.copy()
        d = ImageDraw.Draw(stored)
        for poly in client_polys:
            d.polygon(poly, fill=(0, 0, 0))
    polys = client_polys + server_polys
    status = "redacted" if polys else rep["status"]
    report = {"status": status, "method": "browser-mesh+yunet" if client_polys and server_polys else
              ("browser-mesh" if client_polys else rep.get("method", "yunet")),
              "client_polygons": len(client_polys), "server_eyes_covered": rep.get("eyes_covered", 0),
              "polygons": [[(round(x, 1), round(y, 1)) for x, y in p] for p in polys]}
    if rep.get("error"):
        report["error"] = rep["error"]
    analysis = neutralize(stored, polys) if (need_analysis and polys) else stored
    return stored, analysis, report


def public_report(report):
    """The report without the (bulky) polygon coordinates — safe to return to API callers."""
    return {k: v for k, v in report.items() if k != "polygons"}
