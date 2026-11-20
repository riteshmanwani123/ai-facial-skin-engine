"use client";

import Script from "next/script";

export default function SignupPage() {
  return (
    <>
      <link rel="stylesheet" href="/css/style.css" />
      <link rel="stylesheet" href="/css/portal.css" />
      <link rel="stylesheet" href="/css/theme.css?v=20261014" />
      <link rel="stylesheet" href="/css/quiz.css?v=20261013" />

      <header className="app-header">
        <div className="header-container">
          <div className="brand">
            <img src="/images/clarity_logo.jpg" alt="CLARITY" className="brand-logo-img" />
            <div className="brand-text">
              <span className="brand-name">Clarity <span className="highlight">by Dr Tanya</span></span>
              <span className="brand-tag">Skin insight. Doctor-led care.</span>
            </div>
          </div>
          <div className="header-actions">
            <a className="header-link" href="/login">Sign in</a>
          </div>
        </div>
      </header>

      <main className="quiz-main">
        <div className="quiz-intro">
          <div className="q-brandline">Your personalised skin plan</div>
          <div className="q-stars">Takes about 2 minutes · Free to start · Reviewed by a dermatologist</div>
        </div>
        <div id="quiz-root"></div>
      </main>

      <Script src="/js/signup-quiz.js?v=20261013" strategy="afterInteractive" />
    </>
  );
}
