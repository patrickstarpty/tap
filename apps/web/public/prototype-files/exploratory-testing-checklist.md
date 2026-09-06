# Exploratory testing checklist

TAP sample file · Fictional application workflow

## Explore beyond the happy path

Follow the application from a saved draft to a reviewed decision. Change one assumption at a time and keep evidence of unexpected behavior.

## Identity and access

- Revoke a reviewer role while the review page is open.
- Open a saved link after signing in as a different user.
- Submit the same request from two browser tabs.

## Boundaries and recovery

- Try beneficiary allocations of 99%, 100% and 101%.
- Interrupt the network immediately after confirmation.
- Retry a timed-out submission and inspect the audit history.

## Capture the discovery

Record the starting state, action, expected result and observed result. Attach the application reference so another tester can reproduce the issue.
