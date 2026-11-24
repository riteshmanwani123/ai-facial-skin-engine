# Clarity by Dr Tanya — AI Facial Skin Engine: Live Status & Demo Guide

**Live Deployment URL:** [https://ai-facial-skin-engine.vercel.app](https://ai-facial-skin-engine.vercel.app)  
**Status:** 🟢 **100% Operational & Verified Live**  
**Date:** November 23, 2026

---

## 1. Issues Resolved

1. **Safari Login Pattern Error ("The string did not match the expected pattern.")**
   * **Root Cause:** When an API request failed, the server responded with plain-text (`"Too Many Requests"`). In Safari (WebKit), calling `r.json()` on plain-text throws `SyntaxError: The string did not match the expected pattern.`.
   * **Fix:** Upgraded all client network calls (`login.js`, `signup-quiz.js`, `signup.js`, `scheduler.js`, `auth-guard.js`) to parse responses with `r.text()` and fallback JSON parsing, eliminating Safari crashes.

2. **HTTP 429 ("Could not create account" / Rate Limited)**
   * **Root Cause:** The external backend host on Render had entered an edge routing rate limit (`x-render-routing: hibernate-rate-limited`), returning HTTP 429 across all `/api/*` endpoints.
   * **Fix:** Built and deployed a zero-downtime Serverless API Gateway directly inside Next.js (`frontend/app/api/[...path]/route.ts`) on Vercel's global edge network.

---

## 2. 1-Click Demo Portals & Credentials

The live app at [https://ai-facial-skin-engine.vercel.app/login](https://ai-facial-skin-engine.vercel.app/login) features instant 1-click evaluation buttons:

| Portal | Role | Demo Email | Password | What You Can Test |
| :--- | :--- | :--- | :--- | :--- |
| **Doctor Portal** | `clinician` | `doctor@clarity.local` | `Doctor12345` | Patient queue, clinical case review, diagnosis verification, prescription approval, appointments |
| **Patient Portal** | `patient` | `amantadikademo@gmail.com` | `12345678` | Guided face scan, AI skin health score, Dr. Tanya personalized routines, dermatologist consultation booking |
| **Admin Console** | `admin` | `admin@clarity.local` | `Admin12345` | Doctor roster, schedule blocks, capacity planning, user management |
| **Clinic Dashboard** | `admin` | `admin@clarity.local` | `Admin12345` | Real-time clinical intake metrics, SLA tracking, AI diagnostic accuracy |

---

## 3. Client Onboarding Flow

1. **Patient Signup & Face Scan:**
   * URL: [https://ai-facial-skin-engine.vercel.app/signup](https://ai-facial-skin-engine.vercel.app/signup)
   * Client takes the quick 2-minute skin questionnaire.
   * Enters name, email, and password (works for any email address).
   * Clicks **"Start my face scan"** to open the patient camera/upload portal.
   * AI automatically applies eye privacy protection and generates an instant diagnostic report with custom Dr. Tanya products.
   * Client books a 20-minute doctor consultation appointment.

2. **Doctor Review & Case Approval:**
   * URL: [https://ai-facial-skin-engine.vercel.app/login](https://ai-facial-skin-engine.vercel.app/login)
   * Click **"🩺 Doctor Portal"** (or log in with `doctor@clarity.local` / `Doctor12345`).
   * Open the patient case in the queue to inspect biometrics, approve routine, and write clinical notes.
