"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixModule — first IM channel module implementation

Enables Agents to communicate with each other via the NexusMatrix Server
using the Matrix protocol.
"""

from .matrix_module import MatrixModule

__all__ = ["MatrixModule"]
