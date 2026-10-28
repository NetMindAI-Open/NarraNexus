"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-08-19
@description: Onboarding guide-agent subpackage (backend-side per 铁律 #21 —
its only consumers are the login routes).

Kept import-free on purpose: consumers import `backend.onboarding.provisioning`
directly (the login hooks do), and the "onboarding" bootstrap profile is
registered as a side effect of THAT module's import block — one unambiguous
registration point on the production path, pinned by
tests/backend/onboarding/test_onboarding_provisioning.py::
test_importing_provisioning_registers_the_profile. Re-exports here would add
a second import path whose registration timing someone would eventually have
to reason about.
"""
