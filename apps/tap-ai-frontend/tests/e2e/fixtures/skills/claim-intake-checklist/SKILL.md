---
name: claim-intake-checklist
description: Checks a claim submission for required fields before a reviewer looks at it.
---

# Claim intake checklist

Confirm the claimant name, policy number and incident date are present on
every submitted claim before routing it for manual review. See the field
definitions used by this checklist in [the glossary](references/glossary.md).

A companion field-presence helper is bundled as `scripts/check_fields.py`
for reference only; it is never executed by the product. A blank
submission template is bundled as `assets/template.txt`.

[e2e-skill:claim-intake]
