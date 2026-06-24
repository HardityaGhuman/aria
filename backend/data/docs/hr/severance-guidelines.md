---
department: hr
access_tier: hr_only
region: us
doc_type: procedure
version: 2026.1
effective_date: 2026-01-01
title: Severance Guidelines (US)
---

# Severance Guidelines — GSVH Corp (US)

Internal HR procedure. Restricted to HR and executives. Defines severance eligibility, the calculation formula, and approvals for involuntary separations at GSVH Corp's US entity. Not employee-facing.

## 1. Eligibility

Severance applies to involuntary, non-cause separations — role elimination, reduction in force (RIF), or company-initiated restructuring. It does not apply to terminations for cause, voluntary resignations, mutual separations where cause is a factor, or separations during the 90-day probation period.

To qualify, the employee must have completed the 90-day probation period and must sign a separation and release agreement prepared by Legal. No severance payment is made until the signed agreement is received and the revocation window (where applicable) has passed.

Contractors, temporary workers, and part-time employees working fewer than 20 hours per week are not eligible for severance under this procedure; their terms are governed by the applicable services agreement or offer letter.

## 2. Formula

Base severance is **2 weeks of base pay per completed year of service**, with a floor of 4 weeks and a cap of 26 weeks. Partial years of service do not count; only fully completed calendar years from the employee's adjusted service date are included.

Use the current base salary from `finance/salary-bands.csv` for the employee's level and band. The per-level schedule showing the minimum and maximum severance by level is tabulated in `hr/severance-schedule.csv`; this document is the authoritative prose explanation, and the CSV is the lookup table for HR during separations.

**Example:** An L4 Senior Engineer (US base $165,000 → $3,173/week based on 52 weeks) with 5 completed years receives 10 weeks = $31,730. An L6 Principal Engineer with 14 completed years would be capped at 26 weeks of their base pay regardless of the formula result.

The floor ensures that even a one-year employee receives at least 4 weeks of pay. If the formula for a given tenure falls below 4 weeks, 4 weeks is paid.

## 3. Benefits Continuation

GSVH Corp subsidizes COBRA premiums for the lesser of the severance period or 3 months. The HR Benefits team provides the separation packet with COBRA election forms; the employee must elect COBRA within the statutory 60-day window to receive the subsidy. After the subsidy period, the employee may continue COBRA at their own expense for the remainder of the eligibility window.

Employer 401(k) contributions stop accruing as of the last day of employment. Vested 401(k) balances are the employee's own; the plan administrator sends instructions for rollover. Unvested employer matches are forfeited per the plan schedule.

Equity: unvested options and RSUs are forfeited as of the last day of employment unless the separation agreement specifies accelerated vesting, which requires CFO approval. The 90-day post-termination exercise window for vested options begins on the last working day; see `benefits/equity-and-esop.md` for the full exercise procedure.

## 4. Non-Disparagement and Confidentiality

The separation agreement includes a mutual non-disparagement clause and a reminder that the employee's confidentiality obligations under their employment agreement survive termination. Trade secrets, client lists, and unreleased product information remain confidential indefinitely. The employee must return all company equipment, access credentials, and confidential materials by the last working day; see the IT offboarding checklist.

The company does not provide positive references that go beyond confirming dates of employment and job title without HR approval. Employees may request a reference letter from HR.

## 5. Approvals

All severance packages within the formula and the 26-week cap require:
- HR Business Partner sign-off on the calculation and eligibility determination.
- Direct manager and department head notification (they do not approve, but must be informed before the conversation with the employee).
- Employment attorney review if the employee is in a protected category, has an active accommodation, or has filed a complaint in the past 12 months.

Any package above the 26-week cap, any equity acceleration, or any other deviation from the formula requires **CFO + CHRO joint sign-off**, documented in the personnel file with a written business justification. Deviations for RIF situations affecting more than 5 employees simultaneously require a board-level review before HR notifies affected employees.

No HR Business Partner or manager may commit to a severance figure verbally before approvals are in place. The employee notification conversation must happen only after the package is approved.

## 6. Outplacement and Transition Support

GSVH Corp provides access to an outplacement service for all eligible separated employees. Standard service includes resume review, job search coaching, and LinkedIn profile guidance, for a period matching the severance duration up to a maximum of 3 months. HR provides the enrollment link in the separation packet.

L5 and above may receive an enhanced outplacement engagement — 6 months of executive-level coaching — subject to CHRO approval.

## 7. Process Checklist

HR must complete the following steps in sequence for every involuntary separation:

1. Confirm eligibility, calculate formula, obtain approvals.
2. Coordinate with IT on access revocation timing (access cut at end of last working day, not before notification).
3. Brief the manager on what they may and may not say before and during the notification meeting.
4. Issue the separation agreement and cover letter via DocuSign.
5. Record the separation in the HRIS with termination reason code RIF or ROLE_ELIMINATION.
6. Initiate COBRA subsidy with the benefits carrier.
7. Confirm equipment return with IT and close the IT offboarding ticket before releasing final pay.
8. File signed separation agreement in the personnel file.

Any deviation from this sequence must be documented and approved by the HR Director.
