import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { apiErrorMessage } from "../api/client";
import "../landing.css";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
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
      await login(email, password);
      navigate("/today");
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
            <a href="/register" className="mobile-menu__link" onClick={(e) => { e.preventDefault(); closeMenu(); navigate("/register"); }}>
              Create account
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
              href="/register"
              className="nav__cta"
              onClick={(e) => { e.preventDefault(); navigate("/register"); }}
            >
              Create account
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

        {/* Right panel — login form */}
        <main className="hero__body">
          <div className="panel">
            <div className="panel__chip">[ Log in ]</div>
            <h1 className="panel__h1">EquityLens</h1>
            <p className="panel__tagline">Your edge in the NSE universe.</p>

            {error && (
              <div className="auth-error" role="alert">
                {error}
              </div>
            )}

            <form className="panel__form" noValidate onSubmit={handleSubmit}>
              <div className="form__field">
                <label htmlFor="login-email" className="visually-hidden">Email</label>
                <input
                  id="login-email"
                  type="email"
                  className="form__input"
                  placeholder="Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
              <div className="form__field" style={{ position: "relative" }}>
                <label htmlFor="login-password" className="visually-hidden">Password</label>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  className="form__input"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  className="form__eye"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword((v) => !v)}
                  tabIndex={-1}
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>

              <button type="submit" className="btn btn--solid" disabled={submitting}>
                {submitting ? "Logging in…" : "Log in"}
              </button>
            </form>

            <a
              href="/register"
              className="panel__referral"
              onClick={(e) => { e.preventDefault(); navigate("/register"); }}
            >
              New here? Create an account
            </a>
          </div>
        </main>

        {/* Legal footer */}
        <footer className="hero__legal">
          <p>
            By logging in you accept our{" "}
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
