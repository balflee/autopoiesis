"""Top-level conftest.

Empty placeholder. Its presence is what causes pytest to treat this
directory as the rootdir for sys.path resolution; combined with the
``[tool.pytest.ini_options].pythonpath = ["."]`` setting in
``pyproject.toml`` and ``--import-mode=importlib`` in ``addopts``, this
makes ``from sim.economy import ...`` resolve cleanly from
``tests/sim/`` and from the framework's ``.dev/integration_tests/`` alike.
"""
