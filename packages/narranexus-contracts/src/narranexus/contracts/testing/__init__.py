"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract test bases — the executable definition of each contract.

A plugin author subclasses the base for the kind they implement, points it at
their implementation, and inherits the same checks the builtin implementations
must pass. No pytest import here: the classes are plain and pytest discovers
their ``test_*`` methods when a test module subclasses them.
"""
