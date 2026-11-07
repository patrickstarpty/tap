"""Compatibility import for the production task-local OpenCode budget gate."""

import http.client

from tap.platform.coding.gateway import BudgetGate, CallRecord, DenialRecord

__all__ = ["BudgetGate", "CallRecord", "DenialRecord"]
