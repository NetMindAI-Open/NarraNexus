"""Free-tier wallet integration.

The platform-funded free tier is a wallet on the LiteLLM gateway, exposed to us
by the deploy-side ``quota-api`` service. This package is the seam: a client for
that service, and the provisioner that turns a wallet into an ordinary provider
card. Nothing else in NarraNexus knows the free tier is special.
"""
