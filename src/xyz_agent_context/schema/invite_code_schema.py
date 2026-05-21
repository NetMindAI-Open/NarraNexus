"""
@file_name: invite_code_schema.py
@author: NarraNexus
@date: 2026-05-14
@description: InviteCode Pydantic model

Backs the cloud-mode registration gate. One row = one unique, single-use
code issued to one email.

status:
  - "issued"      code is live, email delivered (or attempted), not yet used
  - "used"        consumed by a successful /api/auth/register
  - "waitlisted"  generated but withheld — the Mode-B auto-issue cap was hit;
                  an admin can promote it to "issued" later
  - "revoked"     killed by an admin; can never be used
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class InviteCode(BaseModel):
    id: Optional[int] = None
    code: str
    email: str
    status: str = "issued"
    source: str = "website"
    email_sent: bool = False
    created_at: Optional[datetime] = None
    issued_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    used_by_user_id: Optional[str] = None
