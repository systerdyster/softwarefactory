---
name: review-security
description: "Security review rulebook (OWASP ASVS Level 2, GDPR/PII): what an attacker can now reach and what the change decided to trust. Use when reviewing a change for security, by hand or as a software factory Mage's rulebook."
model: opus
---

# Review: security

What to look for in a security review. As a factory Mage, the review contract says how to report it.

**The bar is OWASP ASVS Level 2**, and correct handling of personal data (GDPR) is assumed, not
requested. Apply the standard; where the project's rules are stricter, they win.

## The question

Not "does this code look dangerous" — dangerous-looking code gets caught. Ask **what did this change
decide to trust, and what did it newly expose?** Almost every real finding is a boundary that stopped
checking, or a surface that grew.

## First, find the boundaries the diff crosses

From the code, not the hunks:

1. **Where untrusted input enters** — request bodies, query strings, route values, headers, cookies,
   uploads, webhooks, queues, and any CMS or CRM field an editor can type into. Editors are trusted
   with content, not with markup.
2. **Where authorisation is decided**, and whether the changed code sits before or after it.
3. **What the process runs as** — service account, connection string, key vault, managed identity.
4. **What crosses out** — logs, analytics, error responses, third-party calls, emails.

A diff that touches none of these usually has no security finding; saying so, with what you checked,
is a result.

## Checks

**Input and output**
- Untrusted value reaching an interpreter without parameterisation — SQL, GraphQL, LDAP, shell, file
  path, redirect target, deserialiser. Concatenation into any of these is BLOCKING regardless of how
  the value looks today.
- Raw HTML rendering (`Html.Raw`, `innerHTML`, `v-html`, `dangerously*`, `{{{ }}}`, Solid's
  `innerHTML` prop): where did the string come from? User- or editor-supplied parts need sanitising at
  render, not at storage.
- Redirects and file paths built from input — open redirect, path traversal. Allow-list, never
  deny-list.
- Upload accepted on the client-declared type or extension. Content type is a claim.

**Authorisation**
- A new endpoint, page, job or handler with no authorisation decision at all — the most common real
  finding, and it never looks like a bug.
- Authorisation decided in the UI only; a hidden button is not a permission.
- An object reference from input loaded without an ownership check (IDOR). A GUID is not access
  control.
- A state-changing endpoint without anti-forgery protection, unless it is genuinely stateless and
  token-authenticated — say which.
- Authorisation logic added outside the place the project centralises it.

**Secrets and configuration**
- Any credential, key, token or connection string in the diff — tests, fixtures, appsettings, docker
  files and comments included. BLOCKING, and it needs rotating: it is in the history once committed.
- A control weakened by configuration — CORS widened, cookie flags dropped, TLS validation off, a
  security header removed, a framework protection disabled. A local-development need is solved with an
  environment-scoped setting, not a global one.

**Personal data**
- PII in logs, error messages, query strings, analytics or third-party payloads.
- A new field or table holding personal data: minimal? retention? does deletion delete it?
- Error responses that leak internals — stack traces, SQL, hostnames, versions, whether an account
  exists.

**Dependencies**
- A new dependency on a security-relevant surface (crypto, parsing, auth, serialisation, templating).
  Prefer the platform's primitive. Hand-rolled cryptography is BLOCKING on sight.
- A pinned version moved by a change that had no reason to move it.

## Not findings

- A theoretical weakness with no reachable path — state the path, or make it a NOTE and say the path
  is unproven.
- A risk the project has deliberately accepted and recorded. An unrecorded absence is the finding.

Every finding names the **reachable path**: where input enters, what it reaches, what an attacker
gets. A regression against this bar is BLOCKING — that is the bar, not your judgement call.
