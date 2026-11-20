"use client";

import Script from "next/script";

export default function LoginPage() {
  return (
    <>
      <link rel="stylesheet" href="/css/style.css" />
      <link rel="stylesheet" href="/css/portal.css" />
      <link rel="stylesheet" href="/css/theme.css?v=20261014" />

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
            <a className="header-link" href="/signup">Create account</a>
          </div>
        </div>
      </header>

      <main className="portal-main auth-main">
        <section className="card auth-card">
          <div className="card-header">
            <div className="eyebrow">Welcome back</div>
            <h2>Sign in to your journey.</h2>
            <p id="auth-context-note">Enter your email and password to continue.</p>
          </div>
          <form id="login-form" className="auth-form">
            <label>Email<input type="email" name="email" required autoComplete="email" /></label>
            <label>Password<input type="password" name="password" required autoComplete="current-password" /></label>
            <div id="login-error" className="form-error"></div>
            <button type="submit" className="btn btn-primary full">Sign in</button>
          </form>
          <p className="auth-switch">Don&apos;t have an account? <a href="/signup">Create one</a></p>
        </section>
      </main>

      <Script src="/js/login.js?v=20261120" strategy="afterInteractive" />
    </>
  );
}
