import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import "../landing.css";

export default function LandingPage() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [email, setEmail] = useState("");
  const toggleRef = useRef(null);
  const navigate = useNavigate();

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
    if (menuOpen) {
      document.body.classList.add("menu-open");
    } else {
      document.body.classList.remove("menu-open");
    }
    return () => document.body.classList.remove("menu-open");
  }, [menuOpen]);

  function closeMenu() {
    setMenuOpen(false);
    toggleRef.current?.focus();
  }

  function handleEmailSubmit(e) {
    e.preventDefault();
    if (email.trim()) {
      navigate("/register?email=" + encodeURIComponent(email.trim()));
    } else {
      navigate("/register");
    }
  }

  function handleAccess(e) {
    e.preventDefault();
    navigate("/login");
  }

  return (
    <>
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
          {["Platform", "Scores", "Backtest", "Markets"].map((label, i) => (
            <li key={label} style={{ "--i": i }}>
              <a href={"#" + label.toLowerCase()} className="mobile-menu__link" onClick={closeMenu}>
                {label}
              </a>
            </li>
          ))}
          <li style={{ "--i": 4 }}>
            <a
              href="/register"
              className="mobile-menu__cta"
              onClick={(e) => { e.preventDefault(); closeMenu(); navigate("/register"); }}
            >
              Get started
            </a>
          </li>
        </ul>
      </nav>

      <section className="hero">
        <div className="hero__media">
          <video
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
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

        <header className="hero__nav">
          <a href="/" className="nav__logo" aria-label="EquityLens home">
            EquityLens
          </a>
          <div className="nav__cluster">
            <nav className="nav__links" aria-label="Primary navigation">
              {["Platform", "Scores", "Backtest", "Markets"].map((label) => (
                <a key={label} href={"#" + label.toLowerCase()} className="nav__link">
                  {label}
                </a>
              ))}
            </nav>
            <a
              href="/register"
              className="nav__cta"
              onClick={(e) => { e.preventDefault(); navigate("/register"); }}
            >
              Get started
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

        <main className="hero__body">
          <div className="panel">
            <div className="panel__chip">[ EQUITY RESEARCH ]</div>
            <h1 className="panel__h1">EquityLens</h1>
            <p className="panel__tagline">Your edge in the NSE universe.</p>

            <form className="panel__form" action="#" method="post" noValidate onSubmit={handleEmailSubmit}>
              <div className="form__field">
                <label htmlFor="hero-email" className="visually-hidden">Email</label>
                <input
                  id="hero-email"
                  type="email"
                  className="form__input"
                  placeholder="Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                />
              </div>
              <button type="submit" className="btn btn--ghost">Continue with email</button>
              <button type="button" className="btn btn--solid" onClick={handleAccess}>Sign in</button>
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
