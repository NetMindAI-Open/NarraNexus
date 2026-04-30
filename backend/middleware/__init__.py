"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-04-28
@description: HTTP middleware package.

Each middleware lives in its own module so it can be picked up or
omitted independently. Importers should pull the callable directly
from the submodule, not from this package's namespace, to keep the
dependency graph explicit.
"""
