import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Notice } from "../Components/Notice";

export default function PrivacyPage({ terms = false }) {
  const [details, setDetails] = useState(null);
  const [error, setError] = useState("");
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
          <p>
            Operator: {details.operator}.{" "}
            {details.contact ? (
              <>
                Privacy, account and security requests:{" "}
                <a href={`mailto:${details.contact}`}>{details.contact}</a>.
              </>
            ) : (
              "The operator has not configured a public contact yet."
            )}
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
              <h2>What we store and why</h2>
              <p>
                We store your username, an Argon2id password hash, account
                creation time, hashed session tokens and their expiry, your
                source URLs, saved link metadata, reading progress, favorites,
                muted items, Trash, settings and temporary scan previews.
                Passwords and raw session tokens are not stored in the database.
              </p>
              <p>
                File and pasted-text imports store the source name and extracted
                links, titles, dates and nearby details in your private library.
                Files and pasted text are processed in memory on the Trackify
                server; the original input is not retained as a file. Imports do
                not visit their links or send content to an external AI service.
              </p>
              <p>
                We use this data to provide the tracker you request, relying on
                performance of the service agreement where GDPR applies. We use
                short-lived abuse counters and security records for our
                legitimate interest in protecting the service and its users. We
                do not sell account data, show targeted advertising or use
                advertising/analytics cookies.
              </p>
              <h2>Fetching and suggestions</h2>
              <p>
                Scans contact the public sites or APIs you select, which receive
                the server’s IP address and requested URL. Opening a saved link
                contacts that site directly from your browser and is subject to
                its privacy policy. The tracker does not send your login
                password to sources.
              </p>
              <p>
                Local models classify source and imported link metadata and rank
                suggestions from your library. This does not make decisions with
                legal or similarly significant effects. Your account and library
                are not sent to an external AI service for these features.
              </p>
              <p>
                YouTube sources are also subject to{" "}
                <a href="https://www.youtube.com/t/terms">YouTube’s terms</a>{" "}
                and{" "}
                <a href="https://policies.google.com/privacy">
                  Google’s privacy policy
                </a>
                .
              </p>
              <h2>Storage and recipients</h2>
              <p>
                Hosting: {details.hosting}. The operator and hosting provider
                may access data as needed to run, secure or recover the service.
                Requested source/API providers process their own request logs.
                Hosting outside your country can involve international
                transfers; contact the operator for applicable provider
                agreements and transfer safeguards.
              </p>
              <h2>Retention and deletion</h2>
              <p>
                Account data and saved library content, including Trash, remain
                until you delete your account or ask the operator to remove
                them. Sessions expire after seven days. Authentication attempt
                records are removed after 15 minutes by maintenance. Public
                signup counters retain a hashed IP address and creation time for
                up to one hour, with cleanup on the next maintenance run; other
                rate limits are held temporarily in memory. Shared scan caches
                expire within seven days and are cleared on account deletion.
                Application request-access logging is disabled in the hosted
                deployment.
              </p>
              <p>
                Account deletion immediately revokes sessions and removes the
                account login. Active library cleanup normally completes in the
                same request; failed cleanup is retried every five minutes.
                Managed backups are scrubbed at the next daily backup run and
                retained for at most seven days under normal operation. A
                minimal deletion ledger containing a random account ID and
                deletion dates is retained to prevent restoration. It contains
                no username, password or library content. Independent copies you
                download are under your control.
              </p>
              <h2>Your controls and rights</h2>
              <p>
                Settings → Account lets you download your data, sign out all
                sessions, or permanently delete your account after confirming
                your password. You can update saved items and reading progress
                directly.
              </p>
              <p>
                You can request access, correction (including your username),
                erasure, restriction of processing, portability, or object to
                processing based on legitimate interests by contacting the
                operator. If you cannot sign in, use the same contact; identity
                may need to be verified without asking you to send your
                password. Requests are normally answered within one month,
                subject to applicable law. You may complain to your local data
                protection authority.
              </p>
              <h2>Essential browser storage</h2>
              <p>
                The secure, HttpOnly session cookie keeps you signed in for up
                to seven days. It is essential for accounts. No optional
                tracking cookies are currently used.{" "}
                <Link to="/terms">Terms of use</Link>.
              </p>
            </>
          )}
        </>
      )}
    </main>
  );
}
