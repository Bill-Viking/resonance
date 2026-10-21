# Resonance

**See what you have said online and how it may land. Fix it in your own voice. Privately, on your own computer.**

Resonance brings personal brand identity, social accounts, Gmail and Outlook into one private workspace. Its first-use setup guides account access, voice and audience preferences, boundaries and the daily brief. Selected automatic replies are a future capability; current replies are drafts for review.

A calm place to understand your online presence and decide what to do next.

**Scope of 0.1, the early preview:** a local web app for one person, on their own computer, depending on no other system. Personal brand identity and general communication are central. Link available social and email accounts; start email collection with recent mail, not complete lifetime history. Support Gmail and Outlook. Prepare local replies for review first; selected automatic replies require a later, specifically authorized rule system.

## Account boundaries

Ask for a daring level for each account: careful, balanced or bold. Ask whether it is tied to the user's real name or used as an alias. Named accounts must always remain SFW, and possible violations must be flagged for the user's review. Daring never overrides this rule. Unclassified accounts stay conservative until configured. Alias accounts may explicitly allow more provocative material; an alias is not a guarantee of anonymity.

Show the specific excerpt, why it may violate that account's boundary, and a link to the original. The user decides what to change or remove. Keep is a local decision. Done by you records an explicit user attestation with verification pending. Only a verified provider outcome establishes the recorded external result. Changing the saved text, account boundary or material evidence brings relevant concerns back for a fresh review.

Local wording checks only cover available text excerpts. They cannot certify an account SFW, inspect video/images or replace contextual judgment. The product must keep unreviewed accounts and missing media coverage visible.

## Product promise

Give people a clear picture of their communication, understandable feedback on how their words may land, and a short list of useful next actions. Help them express a consistent personal identity while adapting to each audience. Support multiple identities without mixing them. Preserve the user's voice and decisions. Brand preferences are supplied and edited by the user, not inferred personality traits.

Resonance is an independent product with its own source, runtime, storage and design. It reads no other system's files. Familiarity comes from an overview-first layout, quiet dark surfaces and purposeful attention color.

## First experience

Today answers: what can Resonance reach, what deserves a look, and what is the next useful move? It shows the actual latest report date. Every account has a tile with access state, freshness and content coverage. Setup guides Accounts, Your identity and Daily brief. Mail shows collected previews and a persistent local reply editor. Posts groups findings about the same precise source on the same account, with one shared action state and retained evidence/history. Fix now and Ask assistant make the two action paths explicit. Draft studio lets someone check wording before they post. Completing a local task never silently changes a social account or sends an email.

Avoid score theater. A private review of a small sample cannot establish someone's overall reputation or how their audience actually reacted. Keep reported findings, sample coverage and measured engagement separate. Unknown is visible. A strength is useful guidance, not merely the absence of warnings.

## Cross-platform approach

One responsive web interface for modern browsers, with a web app manifest and a cached application shell. Desktop and phone navigation adapt to their space. The current local edition requires a Python host on a computer and keeps its data there. A phone-sized browser preview is not proof of installation or operation on a physical phone.

Actual anywhere access needs an HTTPS deployment with account authentication and per-user storage. This first build deliberately listens only on the local computer. Native app store packages are a later distribution choice, not a prerequisite for testing the experience.

## Data boundaries

Social scope is own posts, comments and replies; social DMs remain excluded. Email is separately authorized: start with recent inbox previews from each connected mailbox, recording the bound and any remaining history. Incoming email is never treated as the user's authored public writing or fed into public-post policy flags. Distinguish original writing from quoted text. Keep source links, dates and review origin. Do not infer personality, medical state or sensitive personal traits. A language observation describes how text may read, not what kind of person wrote it.

No credentials belong in project files, Git, backups, app responses or logs. Clearly identified authentication-code and sign-in-link messages retain metadata and generic wording only; the bounded detector is not a comprehensive secret scanner. Browser sessions in another application are not an integration. A stored profile is not a connection. A human access-check timestamp is a historical snapshot, not a continuing claim.

## What 0.1 delivers

- Local persisted accounts, evidence-backed review observations, actions and preferences.
- Guided account setup, explicit access evidence and personal brand preferences.
- An account tile map, recent Gmail/Outlook snapshots, and local saved reply drafts.
- Responsive overview, account detail, review filtering and draft wording checks.
- Backup import/export in Resonance's own format and a readable brief export.
- Empty-state onboarding usable by any person.
- Offline shell without caching private API responses.

The local draft checks are deterministic English-language writing heuristics. They do not interpret full context, verify claims or substitute for a model-assisted review. An external daily assistant collects authorized evidence and handles separately authorized social actions; the local app displays the results. Local mail drafts are never sent automatically. Direct provider integrations and selected reply automation remain later work.

## Next product milestones

Optional per-alias voice overrides and provider APIs remain later work.

1. Validate the main experience with users on physical Android, iPhone, Mac and Windows devices, including screen readers, touch and offline/reconnect behavior.
2. Add authenticated HTTPS hosting and isolated per-user data, recovery, encrypted backups and data deletion. Select the host and operating budget before deployment.
3. Prove one authorized read-only provider connection end to end. Store provider tokens server-side and scope access narrowly. Add background jobs, freshness, revocation and failure states only after collection actually works.
4. Add contextual model reviews with opt-in external processing and evaluations for helpfulness, false positives, sensitive-trait inference and preservation of user intent.
5. Expand providers according to demonstrated access rather than claiming universal coverage. Add source-specific export adapters where APIs cannot supply the user's authored history.
