import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { apiErrorMessage } from "../api/client";
import "../landing.css";

const RISK_OPTIONS = [
  { value: "low",      label: "Low — preserve capital" },
  { value: "moderate", label: "Moderate — balanced growth" },
  { value: "high",     label: "High — maximum opportunity" },
];

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [riskProfile, setRiskProfile] = useState("moderate");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef(null);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 901px)");
    const handler = (e) => { if (e.matches) closeMenu(); };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape" && menuOpen) closeMenu(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [menuOpen]);

  useEffect(() => {
    document.body.classList.toggle("menu-open", menuOpen);
    return () => document.body.classList.remove("menu-open");
  }, [menuOpen]);

  function closeMenu() {
    setMenuOpen(false);
    toggleRef.current?.focus();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await register(name, email, password, riskProfile);
      navigate("/home");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {/* Mobile Menu */}
      <nav
        id="mobileMenu"
        className={"mobile-menu" + (menuOpen ? " mobile-menu--open" : "")}
        role="dialog"
        aria-modal="true"
        aria-label="Site menu"
        aria-hidden={!menuOpen}
        {...(!menuOpen ? { inert: "" } : {})}
      >
        <div className="mobile-menu__backdrop" onClick={closeMenu} aria-hidden="true" />
        <ul className="mobile-menu__links">
          <li style={{ "--i": 0 }}>
            <a href="/login" className="mobile-menu__link" onClick={(e) => { e.preventDefault(); closeMenu(); navigate("/login"); }}>
              Log in
            </a>
          </li>
        </ul>
      </nav>

      <section className="hero">
        {/* Background video */}
        <div className="hero__media">
          <video
            autoPlay muted loop playsInline preload="auto"
            poster="https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260806_132328_5f9029c8-218f-4489-82b6-29ff2849920e.png"
            aria-hidden="true"
          >
            <source
              src="https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260806_133255_956f653f-5d80-4b06-abd5-0f46c98b60fa.mp4"
              type="video/mp4"
            />
          </video>
          <div className="hero__scrim" aria-hidden="true" />
        </div>

        {/* Navbar */}
        <header className="hero__nav">
          <a href="/" className="nav__logo" aria-label="EquityLens home" onClick={(e) => { e.preventDefault(); navigate("/"); }}>
            EquityLens
          </a>
          <div className="nav__cluster">
            <a
              href="/login"
              className="nav__cta"
              onClick={(e) => { e.preventDefault(); navigate("/login"); }}
            >
              Log in
            </a>
            <button
              ref={toggleRef}
              className={"hamburger" + (menuOpen ? " hamburger--open" : "")}
              aria-expanded={menuOpen}
              aria-controls="mobileMenu"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span className="hamburger__bar" />
              <span className="hamburger__bar" />
              <span className="hamburger__bar" />
            </button>
          </div>
        </header>

        {/* Right panel — register form */}
        <main className="hero__body">
          <div className="panel panel--register">
            <div className="panel__chip">[ Create account ]</div>
            <h1 className="panel__h1">EquityLens</h1>
            <p className="panel__tagline">Research-grade NSE analysis, on paper.</p>

            {error && (
              <div className="auth-error" role="alert">
                {error}
              </div>
            )}

            <form className="panel__form" onSubmit={handleSubmit}>
              <div className="form__field">
                <label htmlFor="reg-name" className="visually-hidden">Name</label>
                <input
                  id="reg-name"
                  type="text"
                  className="form__input"
                  placeholder="Name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  required
                />
              </div>
              <div className="form__field">
                <label htmlFor="reg-email" className="visually-hidden">Email</label>
                <input
                  id="reg-email"
                  type="email"
                  className="form__input"
                  placeholder="Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
              <div className="form__field">
                <label htmlFor="reg-password" className="visually-hidden">Password</label>
                <input
                  id="reg-password"
                  type="password"
                  className="form__input"
                  placeholder="Password (min 8 chars)"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                  required
                />
              </div>

              {/* Risk profile — styled select */}
              <div className="form__field form__field--select">
                <label htmlFor="reg-risk" className="form__select-label">Risk profile</label>
                <div className="form__select-wrap">
                  <select
                    id="reg-risk"
                    className="form__select"
                    value={riskProfile}
                    onChange={(e) => setRiskProfile(e.target.value)}
                  >
                    {RISK_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <svg className="form__select-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </div>
              </div>

              <button type="submit" className="btn btn--solid" disabled={submitting}>
                {submitting ? "Creating account…" : "Create account"}
              </button>
            </form>

            <a
              href="/login"
              className="panel__referral"
              onClick={(e) => { e.preventDefault(); navigate("/login"); }}
            >
              Already have an account
            </a>
          </div>
        </main>

        {/* Legal footer */}
        <footer className="hero__legal">
          <p>
            Creating an EquityLens account means you accept our{" "}
            <a href="#privacy-notice" className="legal__link">Privacy Notice</a>{" "}
            and{" "}
            <a href="#terms" className="legal__link">Terms of Use</a>.
            This platform is a research tool only — not investment advice.
          </p>
        </footer>
      </section>
    </>
  );
}
