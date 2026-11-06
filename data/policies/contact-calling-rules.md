---
doc_id: contact-calling-rules
title: Contact and Calling Rules
product: all
version: "1.0"
effective_date: 2026-01-01
audience: internal
---
# Contact and Calling Rules

These rules are configuration version 1.0 (jurisdiction: US placeholder, modelled on Regulation F and
the TCPA). The scheduler applies them before any call is placed; the same values are in
`data/config/contact_rules.yaml`.

## Calling window
Outbound calls are placed only between 08:00 and 21:00 in the customer's local time zone. A call may
start at 08:00 but not at or after 21:00. If the customer has set a preferred time window, calls are
placed only inside that window as well.

## Frequency limits
- No more than 7 call attempts in any 7 days for the same loan.
- After a conversation with the customer, no further collection call is placed for 7 days.

## Who must never be called
- Customers on the do-not-call list.
- Customers who have not given consent to calls from an automated voice assistant.
- Customers who have asked us to stop contacting them (cease contact). This takes effect immediately.
- Customers with an open dispute, for collection of the disputed amount.

## Retries
- No answer: retry after 120 minutes.
- Busy line: retry after 30 minutes.
- Voicemail: retry after 240 minutes; leave no account details in a voicemail.
- No more than 2 call attempts per day for the same loan.
Every retry is still subject to the calling window and the frequency limits.

## Campaigns
- Pre-due reminder: instalments due within the next 3 days.
- Missed payment: loans 1 to 30 days past due.
