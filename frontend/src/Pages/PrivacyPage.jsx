import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";
import { Notice } from "../Components/Notice";

export default function PrivacyPage({ terms = false }) {
  const [details, setDetails] = useState(null);
  const [error, setError] = useState("");
  const { hash } = useLocation();
  useEffect(() => {
    if (details && hash === "#contact")
      document.getElementById("contact")?.scrollIntoView?.();
  }, [details, hash]);
  useEffect(() => {
    api("/privacy")
      .then(setDetails)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <main className="legal-page" id="main">
      <Link to="/" className="back-link">
        ← Trackify
      </Link>
      <h1>{terms ? "Terms of use" : "Privacy"}</h1>
      <Notice error>{error}</Notice>
      {details && (
        <>
          <p className="muted">Updated {details.updated}</p>
          <p id="contact">
            Trackify is operated by{" "}
            {details.operator === details.contact ? (
              <a href={`mailto:${details.contact}`}>{details.contact}</a>
            ) : (
              details.operator
            )}
            .
            {details.contact && details.operator !== details.contact && (
              <>
                {" "}
                Contact:{" "}
                <a href={`mailto:${details.contact}`}>{details.contact}</a>.
              </>
            )}{" "}
            Contact us for privacy, account or security help.
          </p>
          {terms ? (
            <>
              <h2>Using the service</h2>
              <p>
                Trackify saves links and reading progress for your personal use.
                Keep your credentials private and use a password you do not use
                elsewhere. The service is not intended for children under 16.
              </p>
              <h2>Sources and acceptable use</h2>
              <p>
                Only submit public sources you are permitted to access. Respect
                source terms, copyright, API conditions and request limits.
                Links do not grant rights to the content they point to. Do not
                submit credentials, private document URLs or sensitive personal
                information.
              </p>
              <p>
                Do not attempt to access other accounts or private networks, run
                code on the server, bypass quotas, exploit vulnerabilities, or
                overload the service or its sources. Access may be restricted to
                address abuse. Report suspected vulnerabilities privately to the
                contact above without accessing other users’ data.
              </p>
              <h2>Availability and leaving</h2>
              <p>
                Sources can block access or change, and lists may be incomplete
                or have inaccurate dates. Availability and continued free
                hosting are not guaranteed. You can export your data and delete
                your account in Settings. Nothing here limits mandatory consumer
                or privacy rights.
              </p>
              <p>
                Material changes will be announced on the site before taking
                effect. <Link to="/privacy">Read the privacy notice</Link>.
              </p>
            </>
          ) : (
            <>
              <h2>Information we collect</h2>
              <p>
                We save your username, a protected password hash, and the items,
                links, reading progress and settings in your account. If you add
                a recovery email, we use it only to verify your address and help
                you recover your account, including password-reset notices.
              </p>
              <p>
                Files and text you import are processed on our server. We save
                the extracted links and details you choose to keep, not the
                original upload. We also process IP addresses and keep temporary
                security records to limit abuse and troubleshoot failures.
              </p>
              <h2>How we use and share information</h2>
              <p>
                We use this information to run and secure Trackify, save your
                progress, and find and search content. Our models run on our
                server; we do not send your library or imports to an external AI
                service. We do not sell personal information or share it for
                targeted advertising.
              </p>
              <p>
                Hosting: {details.hosting}. The operator and hosting provider
                can access data as needed to operate the service. If you use
                email recovery, our email provider processes your address and
                recovery messages to deliver them. We may also disclose
                information when required by law or necessary to investigate
                abuse or protect users.
              </p>
              <p>
                Scans send the requested URL and our server’s IP address to the
                source website or API. Opening a saved link connects your
                browser to that website. Its own privacy policy applies. YouTube
                sources are also subject to{" "}
                <a href="https://www.youtube.com/t/terms">YouTube’s terms</a>{" "}
                and{" "}
                <a href="https://policies.google.com/privacy">
                  Google’s privacy policy
                </a>
                .
              </p>
              <h2>Cookies and tracking</h2>
              <p>
                We use an essential cookie to keep you signed in for up to seven
                days, and a short-lived cookie during password recovery. We do
                not use advertising or analytics cookies or track you across
                other websites. We do not change these practices in response to
                a browser’s Do Not Track signal.
              </p>
              <h2>How long we keep information</h2>
              <p>
                Your account and saved library, including Trash, remain until
                you remove them. Temporary scan caches expire within seven days.
                Short-lived abuse limits expire automatically; diagnostic logs
                rotate to limit retention.
              </p>
              <p>
                Deleting your account immediately disables sign-in and starts
                removal of its data. Failed cleanup is retried. Managed backups
                are cleaned at the next daily backup run and normally expire
                within seven days; outages can delay cleanup. We keep a minimal
                record of the deleted account’s random ID and deletion dates to
                prevent it from being restored. Copies you download remain under
                your control.
              </p>
              <h2>Your choices</h2>
              <p>
                In <Link to="/settings#account">Settings → Account</Link>, you
                can manage your recovery email, change your password, download
                your data, sign out other sessions, or delete your account. You
                can edit items and reading progress in your library.
              </p>
              <p>
                To request access, correction or deletion, or if you cannot sign
                in, contact us above. We may need to verify your identity; never
                send us your password. We respond as required by applicable U.S.
                privacy law.
              </p>
              <h2>Children and policy updates</h2>
              <p>
                Trackify is not intended for children under 16. Contact us if
                you believe a child has provided personal information. We will
                post changes to this notice here, update the date, and announce
                material changes on the site before they take effect.
              </p>
            </>
          )}
        </>
      )}
    </main>
  );
}
