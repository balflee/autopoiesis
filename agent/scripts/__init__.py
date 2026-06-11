"""Track B agent scripts package.

Sprint-closer evidence-capture utilities run from the repo root via
``python -m agent.scripts.<name>``. Each script is a thin driver over
the production agent runtime modules with deterministic fakes wired in
— the dependency surface stays inside ``agent/`` so the scripts run
without external services (Polymarket / Polygon / Gemini).
"""
