"""Indian brand-name medication normalisation for the audit engine.

Why this exists: hospital records (especially handwritten prescriptions) reference
drugs by brand. A generic LLM may not connect "Zenoxa" with oxcarbazepine — and
miss the fact that oxcarbazepine is a guideline-first-line drug for trigeminal
neuralgia. We curate the brands that show up most often in Indian claim files
(neuro, cardio, onco, ortho, GI, endo) plus key indications, so the audit prompt
can reason against guideline expectations correctly.

Each entry:
    brand : (generic, class, line_of_therapy_note)

`line_of_therapy_note` is the audit-relevant clinical context (when this drug
counts as guideline-appropriate prior therapy, what condition it treats, etc.).
"""

import re
from typing import Dict, List, Tuple

# Brand → (generic, class, audit-relevant clinical note)
KNOWN_DRUG_BRANDS: Dict[str, Tuple[str, str, str]] = {
    # ── Trigeminal neuralgia / neuropathic pain / epilepsy ──
    "zenoxa": (
        "oxcarbazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia per AAN/EFNS/NICE. "
        "Often preferred over carbamazepine for tolerability. "
        "Documented trial counts as guideline-appropriate prior therapy before MVD.",
    ),
    "oxetol": (
        "oxcarbazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia. Documented failure justifies escalation to surgery.",
    ),
    "trileptal": (
        "oxcarbazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia.",
    ),
    "tegretol": (
        "carbamazepine",
        "sodium channel blocker (anticonvulsant)",
        "Historical first-line drug for trigeminal neuralgia per all major guidelines. "
        "Documented failure or intolerance justifies escalation.",
    ),
    "carbatol": (
        "carbamazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia.",
    ),
    "mazetol": (
        "carbamazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia.",
    ),
    "zeptol": (
        "carbamazepine",
        "sodium channel blocker (anticonvulsant)",
        "First-line drug for trigeminal neuralgia.",
    ),
    "lyrica": (
        "pregabalin",
        "alpha-2-delta ligand (anticonvulsant)",
        "Second-line for trigeminal neuralgia and neuropathic pain. Documented use shows "
        "medical management was attempted.",
    ),
    "pregaba": (
        "pregabalin",
        "alpha-2-delta ligand (anticonvulsant)",
        "Second-line for neuropathic pain / TN.",
    ),
    "gabantin": (
        "gabapentin",
        "alpha-2-delta ligand (anticonvulsant)",
        "Second-line for trigeminal neuralgia / neuropathic pain.",
    ),
    "gabapin": (
        "gabapentin",
        "alpha-2-delta ligand (anticonvulsant)",
        "Second-line for trigeminal neuralgia / neuropathic pain.",
    ),
    "lamictal": (
        "lamotrigine",
        "sodium channel blocker (anticonvulsant)",
        "Second-line for trigeminal neuralgia (add-on).",
    ),
    "baclof": (
        "baclofen",
        "GABA-B agonist (muscle relaxant)",
        "Adjuvant for trigeminal neuralgia per AAN; add-on to carbamazepine/oxcarbazepine.",
    ),
    # ── Analgesics / NSAIDs (often co-prescribed; NOT disease-modifying) ──
    "dolokind": (
        "paracetamol + nimesulide",
        "analgesic + NSAID combination",
        "Symptomatic analgesic only — NOT a guideline-recognised treatment for trigeminal "
        "neuralgia. Does not count as adequate prior therapy.",
    ),
    "dolo": (
        "paracetamol",
        "analgesic / antipyretic",
        "Symptomatic only; not a TN-specific therapy.",
    ),
    "crocin": (
        "paracetamol",
        "analgesic / antipyretic",
        "Symptomatic only.",
    ),
    "combiflam": (
        "ibuprofen + paracetamol",
        "NSAID + analgesic combination",
        "Symptomatic only; NSAIDs are not guideline-recommended for TN.",
    ),
    # ── Cardiology (frequent in audit files) ──
    "ecosprin": (
        "aspirin",
        "antiplatelet",
        "Standard antiplatelet for IHD / post-MI / post-PCI / post-CABG. "
        "Long-term use expected per ACC/AHA and CSI guidelines.",
    ),
    "clopilet": (
        "clopidogrel",
        "P2Y12 antiplatelet",
        "Dual antiplatelet therapy partner after PCI / ACS per guidelines.",
    ),
    "deplatt": (
        "clopidogrel",
        "P2Y12 antiplatelet",
        "Standard DAPT post-PCI / post-ACS.",
    ),
    "brilinta": (
        "ticagrelor",
        "P2Y12 antiplatelet",
        "Preferred over clopidogrel in ACS per ESC/AHA.",
    ),
    "atorva": (
        "atorvastatin",
        "HMG-CoA reductase inhibitor (statin)",
        "Standard secondary-prevention statin per ACC/AHA / NLA.",
    ),
    "rosuva": (
        "rosuvastatin",
        "HMG-CoA reductase inhibitor (statin)",
        "Standard high-intensity statin per ACC/AHA.",
    ),
    "telma": (
        "telmisartan",
        "ARB (angiotensin receptor blocker)",
        "First-line antihypertensive per JNC-8 / ESC; cardio-protective.",
    ),
    "amlong": (
        "amlodipine",
        "calcium channel blocker (dihydropyridine)",
        "First-line antihypertensive.",
    ),
    "concor": (
        "bisoprolol",
        "cardioselective beta-blocker",
        "Standard post-MI / heart-failure therapy per ESC/AHA.",
    ),
    "metolar": (
        "metoprolol",
        "cardioselective beta-blocker",
        "Standard post-MI / HF therapy.",
    ),
    "lasix": (
        "furosemide",
        "loop diuretic",
        "First-line diuretic for HF / fluid overload.",
    ),
    # ── Diabetes ──
    "glycomet": (
        "metformin",
        "biguanide",
        "First-line oral hypoglycaemic per ADA/RSSDI.",
    ),
    "janumet": (
        "sitagliptin + metformin",
        "DPP-4 inhibitor + biguanide",
        "Second-line add-on for T2DM.",
    ),
    "lantus": (
        "insulin glargine",
        "long-acting basal insulin",
        "Standard basal insulin for T1DM / advanced T2DM.",
    ),
    # ── PPIs / GI ──
    "pan": (
        "pantoprazole",
        "PPI",
        "Standard PPI for GERD / ulcer prophylaxis. Often a non-payable line item if used "
        "purely as ward-stay prophylaxis without indication.",
    ),
    "pantop": (
        "pantoprazole",
        "PPI",
        "Standard PPI; scrutinise indication for payability.",
    ),
    "rantac": (
        "ranitidine",
        "H2 receptor blocker",
        "H2 blocker; largely withdrawn globally — flag if billed.",
    ),
    # ── Antibiotics (frequent over-prescription in claims) ──
    "augmentin": (
        "amoxicillin + clavulanic acid",
        "beta-lactam + beta-lactamase inhibitor",
        "Broad-spectrum; audit for documented infection / culture-sensitivity justification.",
    ),
    "monocef": (
        "ceftriaxone",
        "3rd-gen cephalosporin",
        "IV antibiotic; audit indication and duration.",
    ),
    "meropenem": (
        "meropenem",
        "carbapenem",
        "Reserved antibiotic; requires culture justification per AMS guidelines.",
    ),
    "linezolid": (
        "linezolid",
        "oxazolidinone",
        "Reserve antibiotic; requires culture-confirmed MRSA / VRE per AMS.",
    ),
}


def _word_boundary_pattern(brand: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(brand) + r"\b", re.IGNORECASE)


def find_brands_in_text(text: str) -> List[str]:
    """Return the unique brand keys found in `text`, in order of first appearance."""
    if not text:
        return []
    found_order: List[str] = []
    seen: set = set()
    lowered_text = text  # use case-insensitive search via regex flags
    for brand in KNOWN_DRUG_BRANDS:
        if brand in seen:
            continue
        if _word_boundary_pattern(brand).search(lowered_text):
            found_order.append(brand)
            seen.add(brand)
    return found_order


def build_medication_evidence_section(text: str) -> str:
    """Produce an audit-context section listing branded drugs and their clinical meaning."""
    brands = find_brands_in_text(text)
    if not brands:
        return ""
    lines = ["=== MEDICATION EVIDENCE FROM RECORDS (brand → generic, class, audit relevance) ==="]
    for brand in brands:
        generic, drug_class, note = KNOWN_DRUG_BRANDS[brand]
        display_brand = brand.capitalize()
        lines.append(f"- {display_brand} → {generic} ({drug_class}). {note}")
    lines.append(
        "Use this when judging whether documented prior medical therapy meets the guideline "
        "bar for escalation to surgery/intervention. Do NOT claim a drug class was 'never tried' "
        "if a brand in that class is listed here."
    )
    return "\n".join(lines)
