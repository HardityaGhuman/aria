---
department: it
access_tier: all
region: global
doc_type: policy
version: 2026.1
effective_date: 2026-01-01
title: Software Access and License Management
---

# Software Access and License Management — GSVH Corp

This document defines how employees request, use, and return software access and licenses at GSVH Corp. It covers the core toolset, how to request additional access, license compliance obligations, and the deprovisioning process when someone leaves. It applies to all employees and contractors who use GSVH Corp systems. Hardware and physical devices are covered in `it/equipment-and-devices.md`.

## 1. Core Tool Stack

Every employee at GSVH Corp is provisioned with the following standard tools as part of onboarding. IT sets these up before your start date as part of the 3-business-day SLA described in `hr/new-joiner-onboarding.md`.

| Tool | Purpose | Provisioned By |
|------|---------|----------------|
| **Okta** | Single sign-on (SSO) for all GSVH Corp applications | IT Provisioning |
| **Google Workspace** | Email (Gmail), calendar, Docs, Drive | IT Provisioning |
| **Slack** | Team messaging and collaboration | IT Provisioning |
| **Workday** | HR self-service (payslips, PTO, onboarding plan) | IT Provisioning |
| **1Password** | Company password manager — mandatory for all employees | IT Provisioning |
| **Jamf** | Mac device management and security enforcement (macOS devices) | IT Provisioning (auto-installed) |

Engineering employees additionally receive:
- **GitHub** (org membership, team assignment by engineering manager)
- **AWS / GCP console access** at the role-appropriate permission level (requires separate request — see Section 3)

Managers receive Workday manager-role access at the time of their appointment.

## 2. Accessing Tools via Okta

All GSVH Corp applications that support SSO are federated through **Okta**. Log in to the Okta dashboard at `gsvhcorp.okta.com` to access your approved applications. You should never need to create a separate username/password for a company application that is in the Okta catalogue.

MFA is mandatory on all Okta accounts. Approve MFA prompts only when you initiated the sign-in. If you receive an MFA push you did not initiate, deny it immediately and contact the IT service desk — it likely means your credentials have been compromised.

**1Password** is integrated with Okta for applications that do not support SSO. Use 1Password to store and auto-fill credentials; do not save passwords in your browser. The company 1Password vault is provisioned at onboarding; your personal vault within 1Password is private and not visible to IT.

## 3. Requesting Additional Software Access

All software access requests that are not part of the standard onboarding kit must be submitted via an **IT service desk ticket** at `help.gsvhcorp.com`. Ad-hoc requests to colleagues (e.g., "can you just add me to this repo") are not the approved process and will not result in auditable access records.

When submitting an access request, include:
- The tool or system you need access to.
- The specific permission level required (read-only, contributor, admin).
- The business justification (one sentence is sufficient for standard requests).
- Your manager's name (IT will seek manager approval via Workday Approvals for non-standard access).

Standard access requests are fulfilled within **2 business days** of manager approval. Complex or privileged access requests (production database access, admin roles, access to restricted data repositories) require a secondary review by the IT Security team and may take up to 5 business days.

**License cost:** Before requesting a new paid software tool, check whether GSVH Corp already has a license for a similar tool. The IT software catalogue (accessible via the IT service desk portal) lists all licensed tools and their allocated seats. Requesting access to a redundant tool when an approved alternative exists will be redirected by IT to the existing tool.

## 4. License Compliance

GSVH Corp takes software license compliance seriously. Using software beyond the licensed scope — for example, sharing a single-seat license between two users, installing software beyond the licensed device count, or using a personal license for commercial work — exposes the company to legal liability.

Employees must:
- Use only software that has been procured through the IT software catalogue or the procurement process in `finance/procurement-and-vendors.md`.
- Not install unlicensed software on company devices.
- Not use personal subscriptions (e.g., a personal GitHub Copilot subscription) for GSVH Corp work without IT approval, because the license terms may not permit commercial use.
- Report any software use that appears to exceed the licensed scope to the IT service desk.

Jamf enforces software inventory on all managed macOS devices. IT reviews the inventory quarterly; unauthorized software identified during a review will be removed without notice.

## 5. Offboarding and Deprovisioning

When an employee separates from GSVH Corp — whether through resignation, termination, or contract end — **all software access must be revoked within 24 hours of the last working day**. IT initiates deprovisioning automatically upon receiving an offboarding trigger from Workday; managers must ensure the Workday offboarding workflow is initiated on or before the last working day.

The 24-hour deprovisioning SLA covers:
- Okta account deactivation (which cascades to all SSO-connected applications).
- GitHub org removal (for engineering employees).
- Google Workspace account suspension and data export to the manager.
- Workday and Slack access revocation.
- 1Password company vault access revocation (personal vault is not affected).

Cloud console access (AWS, GCP) and any privileged access accounts are revoked as part of the Okta deactivation or via a separate IT Security step where the account is not Okta-federated. IT Security confirms privileged access revocation in the offboarding ticket within 24 hours.

For involuntary terminations, the IT deprovisioning may occur simultaneously with the notification conversation, coordinated between HR and IT in advance, to prevent data exfiltration. Managers must not pre-brief IT to revoke access before HR has conducted the notification meeting.

## 6. Shared and Service Accounts

Shared accounts (where multiple people log in using the same credentials) are prohibited except for specific operational use cases approved in advance by IT Security. Where a shared account is operationally necessary (e.g., a social media management account), it must be managed through 1Password's shared vault feature with individual employee attribution, and access must be listed on the account's IT record.

Service accounts (non-human accounts used by applications) are provisioned by IT Engineering on request, follow the principle of least privilege, and must be reviewed annually. Service account credentials are stored in the company secrets manager, not in code repositories or configuration files.

## 7. Reporting Lost Access or Security Incidents

If you lose access to a tool unexpectedly, your first step is to check the Okta dashboard — if your Okta account is active, the issue is likely an application-level problem, not an account compromise. Submit an IT service desk ticket for access restoration.

If you suspect your Okta account or any company account has been compromised — for example, you received an unexpected MFA prompt, or you notice activity you did not initiate — contact the IT service desk immediately by phone (number on the IT intranet page) rather than by ticket, and do not wait. Speed is critical; access compromise is an incident under the incident response playbook (`it/incident-response-playbook.md`).
