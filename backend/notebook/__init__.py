"""Case Notebook — NotebookLM-style grounded corpus for medical audits.

Every uploaded PDF becomes a first-class source with page-level chunks and
citations. Assessor FWA tables, contradictions, and claim/policy validators
feed the Glowix Expert Opinion so reports match forensic NotebookLM depth.
"""

from backend.notebook.builder import build_case_notebook, apply_notebook_to_result
from backend.notebook.deep_narrative import deepen_observations
from backend.notebook.identity_seal import apply_identity_seal, build_identity_seal
from backend.notebook.models import CaseNotebook, NotebookChunk, Citation

__all__ = [
    "CaseNotebook",
    "NotebookChunk",
    "Citation",
    "build_case_notebook",
    "apply_notebook_to_result",
    "build_identity_seal",
    "apply_identity_seal",
    "deepen_observations",
]
