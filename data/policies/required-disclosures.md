---
doc_id: required-disclosures
title: Required Disclosures
product: all
version: "1.0"
effective_date: 2026-01-01
audience: internal
---
# Required Disclosures

The disclosure texts below are versioned script text (disclosure set version 1.0). They must be spoken
verbatim. Placeholders in braces are filled from configuration and the customer record. The same texts
are in `data/config/disclosures.yaml`; the call outcome records each one given as `key@version`.

## ai_recording (version 1.0)

> Hello, this is {agent_name}, the virtual assistant from {bank_name}. This call is recorded.

Required at the start of every call, inbound and outbound, before anything else is said.

## collections (version 1.0)

> This is an attempt to collect a debt, and any information obtained will be used for that purpose.

Required on every missed-payment call, after identity verification and before any amount owed is stated.

## third_party_neutral (version 1.0)

> This is {bank_name} calling for {first_name}. Please ask them to call us back at their convenience. Thank you.

Used when someone other than the customer answers. Nothing else about the account or the reason for the
call may be said.
