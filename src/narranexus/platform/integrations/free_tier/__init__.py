"""Free-tier wallet client.

Lives in the agent package (not under ``backend/``) because BOTH sides need it:
the login path provisions wallets, and agent-side code (the transcription
resolver) asks whether a user still has free-tier balance. Per the one-way
dependency rule, backend may import this; the reverse is forbidden — so the
shared piece has to sit here.
"""
