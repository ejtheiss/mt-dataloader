"""Canonical MT sandbox behavior constants and helpers.

Source: https://docs.moderntreasury.com/payments/docs/test-counterparties

Account numbers recognized by the MT Sandbox:
  - 123456789 → successful ACH, Wire, and RTP payment orders
  - 100XX (e.g. 10001) → ACH return with code RXX
  - 11111111X...X (≥9 digits, e.g. 1111111110) → fail ACH, Wire, and RTP POs
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

SANDBOX_SUCCESS_ACCOUNT = "123456789"
SANDBOX_FAILURE_PREFIX = "11111111"
SANDBOX_RETURN_PREFIX = "100"
SANDBOX_ROUTING_NUMBER = "121141822"


class SandboxBehavior(BaseModel):
    """Expected sandbox behavior for a counterparty account."""

    model_config = ConfigDict(extra="forbid")

    behavior: Literal["success", "failure", "return"]
    return_code: str | None = None

    @property
    def account_number(self) -> str:
        if self.behavior == "success":
            return SANDBOX_SUCCESS_ACCOUNT
        if self.behavior == "failure":
            return f"{SANDBOX_FAILURE_PREFIX}10"
        if self.behavior == "return":
            code = (self.return_code or "R01").upper()
            digits = code.lstrip("R")
            return f"{SANDBOX_RETURN_PREFIX}{digits.zfill(2)}"
        raise ValueError(f"Unknown behavior: {self.behavior}")


def detect_sandbox_behavior(account_number: str) -> SandboxBehavior | None:
    """Detect sandbox behavior from an account number, or None."""
    if account_number == SANDBOX_SUCCESS_ACCOUNT:
        return SandboxBehavior(behavior="success")
    if account_number.startswith(SANDBOX_RETURN_PREFIX) and len(account_number) == 5:
        digits = account_number[3:]
        return SandboxBehavior(behavior="return", return_code=f"R{digits}")
    if account_number.startswith(SANDBOX_FAILURE_PREFIX):
        return SandboxBehavior(behavior="failure")
    return None
