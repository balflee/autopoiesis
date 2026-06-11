"""Integration smoke tests — cross-module convergence checks.

These tests stitch multiple Track B modules together (engines +
decision + dashboard_bridge + runtime) under fake adapters. They are
distinct from unit tests under ``tests/agent/engines`` etc. in that
they exercise the orchestrator's full boot path rather than a single
class.
"""
