"""The package version. Single source: pyproject reads it from here.

Independent of the service's wire contract on purpose — the bundled
``contract/versions.json`` says which wire version this build was generated
against, and :func:`dsail.contract.versions` reads it.
"""

__version__ = "0.1.0"
