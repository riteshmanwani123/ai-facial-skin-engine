#!/usr/bin/env python3
"""
pipeline_probe.py — Automated Live Pipeline & Health Verification for Clarity AI Face Engine.

Runs end-to-end synthetic probes against the live cloud deployment (Vercel frontend + Render backend)
to guarantee that patient and doctor flows never hit broken links, 404 images, or 500 errors.

Usage:
  python3 pipeline_probe.py                  # Run a full probe suite once
  python3 pipeline_probe.py --watch          # Run continuously every 10 mins (keeps Render backend warm)
  python3 pipeline_probe.py --interval 300   # Run continuously every 5 mins
"""

import sys
import time
import json
import argparse
import urllib.request
import urllib.error
import http.cookiejar
from datetime import datetime

VERCEL_BASE = "https://ai-facial-skin-engine.vercel.app"
RENDER_BASE = "https://ai-facial-skin-engine.onrender.com"

# ANSI Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class LiveProbeRunner:
    def __init__(self, vercel_url=VERCEL_BASE, render_url=RENDER_BASE, timeout=45):
        self.vercel = vercel_url.rstrip("/")
        self.render = render_url.rstrip("/")
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.results = []

    def log_header(self, text):
        print(f"\n{BOLD}{CYAN}=== {text} ==={RESET}")

    def record(self, name, success, latency_ms, details=""):
        status_str = f"{GREEN}PASS{RESET}" if success else f"{RED}FAIL{RESET}"
        latency_str = f"{latency_ms:6.1f} ms"
        print(f"  [{status_str}] {name:<45} {latency_str}  {details}")
        self.results.append({"name": name, "success": success, "latency_ms": latency_ms, "details": details})
        return success

    def fetch(self, url, method="GET", data=None, headers=None):
        req_headers = {
            "User-Agent": "ClarityLiveProbe/1.0",
            "Accept": "*/*"
        }
        if headers:
            req_headers.update(headers)

        payload = None
        if data is not None:
            if isinstance(data, dict):
                payload = json.dumps(data).encode("utf-8")
                req_headers["Content-Type"] = "application/json"
            elif isinstance(data, (bytes, str)):
                payload = data.encode("utf-8") if isinstance(data, str) else data

        req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
        t0 = time.perf_counter()
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                elapsed = (time.perf_counter() - t0) * 1000
                body = resp.read()
                return resp.status, resp.headers, body, elapsed, None
        except urllib.error.HTTPError as e:
            elapsed = (time.perf_counter() - t0) * 1000
            body = e.read()
            return e.code, e.headers, body, elapsed, e
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            return 0, {}, b"", elapsed, e

    # ------------------------------------------------------------- Probe steps
    def probe_backend_health(self):
        url = f"{self.render}/health"
        status, _, body, ms, err = self.fetch(url)
        if status == 200:
            try:
                data = json.loads(body.decode("utf-8"))
                db_status = data.get("database", "unknown")
                svc_status = data.get("status", "unknown")
                ok = (svc_status == "healthy" and db_status == "connected")
                return self.record("Render Backend Health (/health)", ok, ms, f"db={db_status}, status={svc_status}")
            except Exception as ex:
                return self.record("Render Backend Health (/health)", False, ms, f"JSON parse error: {ex}")
        return self.record("Render Backend Health (/health)", False, ms, f"HTTP {status} {err or ''}")

    def probe_frontend_routes(self):
        routes = [
            ("/signup", "Patient Sign-Up Page"),
            ("/login", "Unified Login Page"),
            ("/patient", "Patient Portal"),
            ("/doctor", "Doctor Review Portal"),
            ("/admin", "Clinic Admin Console"),
        ]
        all_ok = True
        for path, label in routes:
            url = f"{self.vercel}{path}"
            status, _, body, ms, err = self.fetch(url)
            ok = (status == 200 and len(body) > 500)
            if not self.record(f"Vercel Route: {path} ({label})", ok, ms, f"HTTP {status}, size={len(body)}B"):
                all_ok = False
        return all_ok

    def probe_doctor_login(self):
        url = f"{self.vercel}/api/auth/login"
        payload = {"email": "doctor@clarity.local", "password": "Doctor12345"}
        status, _, body, ms, err = self.fetch(url, method="POST", data=payload)
        if status == 200:
            try:
                data = json.loads(body.decode("utf-8"))
                name = data.get("full_name")
                role = data.get("role")
                ok = (role == "clinician" and bool(name))
                return self.record("Doctor Auth API (POST /api/auth/login)", ok, ms, f"Logged in as {name} ({role})")
            except Exception as ex:
                return self.record("Doctor Auth API (POST /api/auth/login)", False, ms, f"JSON error: {ex}")
        return self.record("Doctor Auth API (POST /api/auth/login)", False, ms, f"HTTP {status} {err or ''}")

    def probe_consultation_queue_and_images(self):
        # 1. Fetch queue
        queue_url = f"{self.vercel}/api/consultations?status=awaiting_doctor"
        status, _, body, ms, err = self.fetch(queue_url)
        if status != 200:
            return self.record("Consultation Queue (GET /api/consultations)", False, ms, f"HTTP {status} {err or ''}")

        try:
            data = json.loads(body.decode("utf-8"))
            consultations = data.get("consultations", [])
            self.record("Consultation Queue (GET /api/consultations)", True, ms, f"{len(consultations)} cases awaiting review")
        except Exception as ex:
            return self.record("Consultation Queue (GET /api/consultations)", False, ms, f"JSON error: {ex}")

        if not consultations:
            # Fall back to any recent consultation (e.g. completed) to verify case detail and image serving
            any_url = f"{self.vercel}/api/consultations"
            a_status, _, a_body, _, _ = self.fetch(any_url)
            if a_status == 200:
                try:
                    consultations = json.loads(a_body.decode("utf-8")).get("consultations", [])
                except Exception:
                    consultations = []

        if not consultations:
            print(f"  {YELLOW}Note: No consultations found to inspect detail.{RESET}")
            return True

        # Inspect the latest case
        case = consultations[0]
        cid = case["id"]
        sid = case["scan_id"]
        patient_name = (case.get("patient") or {}).get("full_name", "Unknown")

        # 2. Inspect case detail (Verifies no 500 memoryview serialization bug)
        detail_url = f"{self.vercel}/api/consultations/{cid}"
        d_status, _, d_body, d_ms, d_err = self.fetch(detail_url)
        detail_ok = False
        if d_status == 200:
            try:
                d_json = json.loads(d_body.decode("utf-8"))
                detail_ok = ("scan" in d_json and "patient" in d_json)
                self.record(f"Case Detail Review (/api/consultations/{cid[:8]}...)", detail_ok, d_ms, f"Patient: {patient_name}")
            except Exception as ex:
                self.record(f"Case Detail Review (/api/consultations/{cid[:8]}...)", False, d_ms, f"JSON error: {ex}")
        else:
            self.record(f"Case Detail Review (/api/consultations/{cid[:8]}...)", False, d_ms, f"HTTP {d_status} {d_err or ''}")

        # 3. Inspect scan image (Verifies image serving & fallback)
        img_url = f"{self.vercel}/api/scans/{sid}/image"
        i_status, i_headers, i_body, i_ms, i_err = self.fetch(img_url)
        c_type = i_headers.get("Content-Type", "") if hasattr(i_headers, "get") else ""
        img_ok = (i_status == 200 and "image" in c_type and len(i_body) > 1000)
        self.record(f"Scan Photo Serving (/api/scans/{sid[:8]}.../image)", img_ok, i_ms, f"type={c_type}, size={len(i_body)}B")

        # 4. Inspect multi-angle view photo
        view_url = f"{self.vercel}/api/scans/{sid}/views/left/image"
        v_status, v_headers, v_body, v_ms, v_err = self.fetch(view_url)
        vc_type = v_headers.get("Content-Type", "") if hasattr(v_headers, "get") else ""
        v_ok = (v_status == 200 and "image" in vc_type and len(v_body) > 1000)
        self.record(f"Angled View Photo (/views/left/image)", v_ok, v_ms, f"type={vc_type}, size={len(v_body)}B")

        return detail_ok and img_ok and v_ok

    def probe_products_catalog(self):
        url = f"{self.vercel}/api/products"
        status, _, body, ms, err = self.fetch(url)
        if status == 200:
            try:
                data = json.loads(body.decode("utf-8"))
                prods = data.get("products", [])
                ok = len(prods) > 0
                return self.record("Dr. Tanya Skincare Catalog (/api/products)", ok, ms, f"{len(prods)} active products loaded")
            except Exception as ex:
                return self.record("Dr. Tanya Skincare Catalog (/api/products)", False, ms, f"JSON error: {ex}")
        return self.record("Dr. Tanya Skincare Catalog (/api/products)", False, ms, f"HTTP {status} {err or ''}")

    def run_full_suite(self):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_header(f"Starting Clarity Live Pipeline Probe at {now_str}")
        print(f"Target Frontend: {BOLD}{self.vercel}{RESET}")
        print(f"Target Backend : {BOLD}{self.render}{RESET}")

        self.log_header("Phase 1: Cloud Infrastructure & Database")
        self.probe_backend_health()

        self.log_header("Phase 2: Frontend Client Page Availability")
        self.probe_frontend_routes()

        self.log_header("Phase 3: Clinician Authentication & Authorization")
        self.probe_doctor_login()

        self.log_header("Phase 4: Case Review, Images & Consultation Flow")
        self.probe_consultation_queue_and_images()

        self.log_header("Phase 5: Product Catalog & Regimen Data")
        self.probe_products_catalog()

        # Summary
        total = len(self.results)
        passed = sum(1 for r in self.results if r["success"])
        failed = total - passed

        self.log_header("Probe Summary")
        if failed == 0:
            print(f"{GREEN}{BOLD}ALL {passed}/{total} CHECKS PASSED PERFECTLY! Live demo is 100% operational.{RESET}\n")
            return True
        else:
            print(f"{RED}{BOLD}{failed}/{total} CHECKS FAILED! Please inspect errors above.{RESET}\n")
            return False


def main():
    parser = argparse.ArgumentParser(description="Clarity AI Face Engine Live Pipeline Probe")
    parser.add_argument("--watch", action="store_true", help="Run continuously on an interval to monitor & keep Render backend warm")
    parser.add_argument("--interval", type=int, default=600, help="Interval in seconds between probe runs in watch mode (default: 600s / 10m)")
    parser.add_argument("--vercel-url", default=VERCEL_BASE, help="Vercel URL")
    parser.add_argument("--render-url", default=RENDER_BASE, help="Render URL")
    args = parser.parse_args()

    runner = LiveProbeRunner(vercel_url=args.vercel_url, render_url=args.render_url)

    if not args.watch:
        success = runner.run_full_suite()
        sys.exit(0 if success else 1)

    print(f"{BOLD}Starting Live Keep-Alive & Monitoring daemon (probe interval: {args.interval}s)...{RESET}")
    while True:
        runner.results.clear()
        runner.run_full_suite()
        print(f"{CYAN}Sleeping for {args.interval} seconds until next heartbeat probe...{RESET}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
