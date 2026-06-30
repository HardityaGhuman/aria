---
department: it
access_tier: manager
region: global
doc_type: procedure
version: 2026.1
effective_date: 2026-01-01
title: Incident Response Playbook
---

# Incident Response Playbook — GSVH Corp

This document defines how GSVH Corp detects, classifies, responds to, and learns from technology incidents. It applies to the IT team, engineering managers, on-call engineers, and any manager whose team owns a production system. Employees who discover or suspect a security incident should follow `it/acceptable-use-and-security.md`; this playbook covers the response process after the incident is declared.

## 1. Incident Severity Classification

All incidents are classified into one of four severity levels at the time of declaration. The severity determines the response team, escalation path, and communication cadence.

| Severity | Label | Definition | RTO | RPO |
|----------|-------|-----------|-----|-----|
| **SEV1** | Critical | Complete service outage or data breach affecting all users or a critical system; material financial or reputational impact. | **4 hours** | **1 hour** |
| **SEV2** | High | Major feature unavailable or significant performance degradation affecting a substantial portion of users; or a confirmed security compromise with contained blast radius. | 8 hours | 4 hours |
| **SEV3** | Medium | Non-critical feature unavailable; degraded experience for a subset of users; or a security vulnerability identified but not yet exploited. | Next business day | 24 hours |
| **SEV4** | Low | Minor issue with minimal user impact; cosmetic bug in production; informational security finding. | Within 5 business days | N/A |

**RTO (Recovery Time Objective)** is the target time from incident declaration to service restoration. **RPO (Recovery Point Objective)** is the maximum acceptable data loss window — the system must be restorable to a state no older than this.

SEV levels may be downgraded as more information is gathered but should not be upgraded in reverse; when in doubt, declare the higher severity and downgrade once the scope is clearer.

## 2. On-Call Rotation

GSVH Corp maintains an on-call rotation across the engineering and IT infrastructure teams. The on-call engineer is the first responder for SEV1 and SEV2 incidents outside business hours.

- The on-call schedule is published in PagerDuty at least 2 weeks ahead.
- On-call shifts run in weekly blocks.
- Each on-call engineer has a named escalation contact (the on-call manager or engineering lead) who is paged automatically if the primary responder does not acknowledge a SEV1 within 10 minutes.
- On-call engineers are expected to acknowledge a SEV1 page within **10 minutes** and a SEV2 page within **30 minutes**. Unacknowledged pages auto-escalate.
- Compensation for on-call and on-call incidents outside working hours is governed by the compensation policy and the applicable employment contract.

## 3. Incident Declaration and Initial Steps

Any employee can declare an incident by paging the on-call engineer via PagerDuty or by posting in the #incidents Slack channel. IT or engineering managers may also declare directly. Once declared:

1. **Incident commander is assigned** — the on-call engineer assumes the IC role unless they designate a more senior engineer. The IC owns the incident from declaration to resolution.
2. **Incident channel opened** — a dedicated Slack channel is created (`#inc-YYYY-MM-DD-short-description`) for all incident communication.
3. **Severity assessed** — the IC assesses SEV level within 5 minutes and begins the appropriate escalation.
4. **Initial stakeholder notification** — for SEV1 and SEV2, the IC notifies the engineering VP and IT Director within 15 minutes of declaration, even if the root cause is unknown.
5. **Status page updated** — for SEV1 and SEV2, IT updates the GSVH Corp status page within 30 minutes of declaration with a brief description (no speculation on cause).

## 4. SEV1 Response Protocol

SEV1 incidents demand immediate, sustained attention. The following steps run in parallel, not sequentially, with the IC coordinating.

**Mitigation before diagnosis.** The first priority is to reduce user impact — roll back a recent deployment, reroute traffic, restore from backup, or take a subsystem offline — even before the root cause is understood. A rollback that reduces impact in 10 minutes is preferable to a correct fix that takes 2 hours.

**Communication cadence.** The IC posts an update in the incident channel every 20 minutes. The engineering VP or designated communications lead posts an external status page update every 30 minutes. No stakeholder should go more than 30 minutes without a status update during an active SEV1.

**Escalation.** If the SEV1 is not mitigated within 2 hours, the IC escalates to the CTO and CHRO (for people-impacting issues) or CFO (for financial-system issues). External notification to affected customers is evaluated by the VP of Engineering in consultation with Legal.

**RTO target: 4 hours. RPO target: 1 hour.** If the incident is not resolved within the RTO, the IC and engineering VP jointly decide whether to invoke the business continuity plan.

## 5. SEV2 and SEV3 Response

SEV2 incidents follow the same channel and stakeholder notification process as SEV1 but with a longer update cadence (every 60 minutes) and without the automatic CTO escalation unless the RTO is breached.

SEV3 incidents are handled during business hours by the relevant engineering team. An incident channel is still opened, but the updates are less frequent (at the start and end of each business day until resolved). SEV3 incidents that remain unresolved after 5 business days are reviewed by the engineering manager and may be re-classified.

SEV4 issues are tracked as standard engineering tickets. No incident channel is required; no status page update unless the IC judges it necessary.

## 6. Postmortem Process

A **blameless postmortem is required for every SEV1 and SEV2 incident**, completed within **5 business days** of the incident being resolved. SEV3 postmortems are optional but encouraged for recurring or instructive incidents.

The postmortem is owned by the incident commander and must include:
- **Timeline** — a precise, chronological record of what happened, when, and who took what action.
- **Impact** — duration, number of affected users, data loss (measured against RPO), financial impact if calculable.
- **Root cause(s)** — the underlying technical or process failure(s), not the proximate trigger. Use the "five whys" technique.
- **Contributing factors** — circumstances that made the incident worse or harder to detect.
- **Action items** — specific, assigned, time-bound follow-up tasks to prevent recurrence. Every action item must have a named owner and a target completion date.
- **What went well** — things the team did correctly during the response that should be preserved.

The postmortem document is shared with the engineering team and reviewed in the next weekly engineering all-hands. Action items are tracked in the engineering backlog and reviewed monthly by the engineering VP. Postmortems are stored in the engineering wiki and are accessible to all engineering staff.

The blameless principle: the postmortem asks "what failed?" not "who failed?" Individuals are not named in the root cause; systems, processes, and tooling are the subjects. This principle protects the psychological safety needed for honest postmortems.

## 7. Security Incident Handling

Incidents involving a confirmed or suspected security breach (unauthorized access, data exfiltration, ransomware, credential compromise) follow the same severity and communication structure above, but with these additions:

- **Legal and CISO are looped in within 1 hour** of any confirmed security incident, regardless of severity level.
- The incident channel is restricted to IC, IT Security, CISO, Legal, and the engineering VP. No wider access.
- Evidence preservation takes priority over immediate remediation — do not wipe or rebuild affected systems without IT Security authorization.
- Customer notification obligations under GDPR, CCPA, or other applicable law are assessed by Legal within 24 hours of a confirmed breach; legal notification deadlines (e.g., 72 hours under GDPR) are tracked by Legal from the moment the breach is confirmed.

## 8. Communication Templates

**Initial SEV1 status page post (within 30 minutes of declaration):**
> We are aware of an issue affecting [service]. Our team is investigating. We will provide an update within 30 minutes.

**SEV1 30-minute update:**
> We are continuing to investigate the issue affecting [service]. [Brief factual description of what is known.] Our team is working on [mitigation step]. Next update in 30 minutes.

**Resolution post:**
> The issue affecting [service] has been resolved as of [time UTC]. [One-sentence description of root cause if known.] A detailed postmortem will be published within 5 business days.

Do not speculate on cause in external communications. Do not use the word "breach" in external communications before Legal has reviewed the communication.
