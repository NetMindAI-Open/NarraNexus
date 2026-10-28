"""
@file_name: _version.py
@author: Bin Liang
@date: 2026-09-04
@description: The engine's version string (mirrors pyproject; ``narranexus.kernel.plugins.compat.host_version`` reads package metadata at runtime). ``narranexus`` itself is a PEP 420 namespace package since batch 6d — contracts and sdk are separate wheels sharing it.
"""
__version__ = "1.15.0"

__all__ = ["__version__"]
