"""Decode QR codes / barcodes on claim PDFs and match them to the patient.

Hospital lab reports (Felix, Apollo, Max, etc.) often stamp a QR that encodes a HIS
verification URL or patient identifiers. Indoor stickers carry 1-D barcodes.
Reviewers ask whether those codes belong to the claimed patient — this module
answers that deterministically. Remote URLs are decoded locally and never fetched.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse

import fitz
from PIL import Image

try:
    import zxingcpp

    _HAS_ZXING = True
except Exception:  # pragma: no cover - optional native wheel
    zxingcpp = None  # type: ignore
    _HAS_ZXING = False

try:
    import pytesseract

    _HAS_TESSERACT = True
except Exception:  # pragma: no cover
    pytesseract = None  # type: ignore
    _HAS_TESSERACT = False

SCAN_DPI = int(os.getenv("DOCUMENT_CODE_DPI", "200") or "200")
MAX_PAGES_PER_FILE = int(os.getenv("DOCUMENT_CODE_MAX_PAGES", "80") or "80")
MAX_OCR_UNIQUE = int(os.getenv("DOCUMENT_CODE_OCR_UNIQUE", "12") or "12")

_PRINTED_CODE_RE = re.compile(
    r"\b(?:BAR\s*C[DO]|BARCODE(?:\s*(?:NO\.?|NUMBER|#))?)\s*[:.\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]{5,})",
    re.I,
)
_UHID_RE = re.compile(
    r"\b(?:UHID(?:\s*NO)?|UMR(?:\s*NO)?|IP(?:\s*NO)?|IPD)\s*[:.\-/]?\s*"
    r"([A-Z]{0,6}\d[A-Z0-9\-/]{4,})",
    re.I,
)
_NAME_RE = re.compile(
    r"(?:Patient\s*Name|Name)\s*[:.\-]?\s*(?:Mr\.?|Mrs\.?|Ms\.?|Miss)?\s*"
    r"([A-Z][A-Za-z.'\s]{2,40})",
    re.I,
)
_HOSPITAL_STOP = {
    "hospital", "hospitals", "pvt", "ltd", "limited", "llp", "healthcare",
    "health", "clinic", "centre", "center", "medical", "institute", "nursing",
    "home", "the", "and", "of", "unit", "private", "super", "specialty",
    "multispeciality", "multi",
}
_TITLE_STOP = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt"}


def decoder_available() -> bool:
    return bool(_HAS_ZXING)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _tokens(s: str, stop: Optional[set] = None) -> List[str]:
    stop = stop or set()
    return [
        t for t in re.findall(r"[a-z0-9]+", _norm(s))
        if t not in stop and len(t) > 2
    ]


def parse_code_payload(payload: str) -> Dict[str, Any]:
    """Split a decoded QR/barcode string into identifiers without fetching URLs."""
    text = (payload or "").strip()
    out: Dict[str, Any] = {
        "payload": text,
        "kind": "plain",
        "host": "",
        "issuer_hint": "",
        "fields": {},
        "patient_name": "",
        "uhid": "",
        "bill_no": "",
    }
    if not text:
        return out

    if re.match(r"https?://", text, re.I):
        out["kind"] = "url"
        parsed = urlparse(text)
        host = (parsed.netloc or "").lower()
        out["host"] = host
        labels = [p for p in host.split(".") if p and p not in {"www", "api", "app", "his"}]
        if labels:
            # api.felixhospital.com → felixhospital → felix
            registrable = labels[-2] if len(labels) >= 2 else labels[0]
            out["issuer_hint"] = re.sub(r"hospital.*$", "", registrable) or registrable
        qs = {k.upper(): (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}
        out["fields"] = qs
        out["bill_no"] = qs.get("BILLNO") or qs.get("BILL_NO") or qs.get("BILL") or ""
        out["uhid"] = qs.get("UHID") or qs.get("UMR") or qs.get("IPNO") or qs.get("IP") or ""
        out["patient_name"] = qs.get("NAME") or qs.get("PATIENT") or qs.get("PATIENTNAME") or ""
        return out

    if text.startswith("{") and text.endswith("}"):
        import json

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            out["kind"] = "json"
            fields = {str(k).upper(): str(v) for k, v in data.items() if v not in (None, "")}
            out["fields"] = fields
            out["patient_name"] = (
                fields.get("NAME") or fields.get("PATIENTNAME") or fields.get("PATIENT") or ""
            )
            out["uhid"] = fields.get("UHID") or fields.get("UMR") or fields.get("IPNO") or ""
            out["bill_no"] = fields.get("BILLNO") or fields.get("BILL") or fields.get("SID") or ""
            return out

    m = re.search(r"\b([A-Z]{2,}\d{5,})\b", text)
    if m:
        out["bill_no"] = m.group(1)
    return out


def _decode_pil(img: Image.Image) -> List[Dict[str, str]]:
    if not _HAS_ZXING or img is None:
        return []
    hits: List[Dict[str, str]] = []
    try:
        results = zxingcpp.read_barcodes(
            img, try_rotate=True, try_invert=True, try_downscale=True
        )
    except Exception:
        return []
    seen = set()
    for item in results or []:
        payload = str(getattr(item, "text", "") or "").strip()
        fmt = str(getattr(item, "format", "") or "Unknown")
        if not payload or payload in seen:
            continue
        seen.add(payload)
        hits.append({"format": fmt, "payload": payload})
    return hits


def _page_image(page, dpi: int = SCAN_DPI) -> Optional[Image.Image]:
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    except Exception:
        return None


def _ocr_page(img: Image.Image) -> str:
    if not _HAS_TESSERACT or img is None:
        return ""
    try:
        gray = img.convert("L")
        return pytesseract.image_to_string(gray) or ""
    except Exception:
        return ""


def extract_printed_codes(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for rx in (_PRINTED_CODE_RE,):
        for m in rx.finditer(text or ""):
            val = m.group(1).strip().upper()
            if val and val not in seen:
                seen.add(val)
                found.append(val)
    return found


def _identifiers_from_ocr(ocr: str) -> Dict[str, str]:
    blob = ocr or ""
    name = ""
    m = _NAME_RE.search(blob)
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        name = re.split(r"\b(?:Age|Sex|UMR|UHID|IP|Bill|Doctor)\b", name, maxsplit=1)[0].strip()
    uhids = [m.group(1).strip().upper() for m in _UHID_RE.finditer(blob)]
    printed = extract_printed_codes(blob)
    return {
        "printed_name": name,
        "printed_uhid": uhids[0] if uhids else "",
        "printed_ids": ", ".join(dict.fromkeys(uhids)),
        "printed_bar_cd": printed[0] if printed else "",
        "ocr_excerpt": re.sub(r"\s+", " ", blob).strip()[:400],
    }


def scan_pdf(pdf_path: str, source_name: str = "") -> List[Dict[str, Any]]:
    """Render pages and decode every QR/barcode. OCR unique payloads for printed IDs."""
    hits: List[Dict[str, Any]] = []
    if not pdf_path or not os.path.isfile(pdf_path):
        return hits
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return hits
    unique_payload_page: Dict[str, Tuple[int, Any]] = {}
    try:
        n = min(len(doc), MAX_PAGES_PER_FILE)
        for i in range(n):
            img = _page_image(doc[i])
            if img is None:
                continue
            decoded = _decode_pil(img)
            for item in decoded:
                payload = item["payload"]
                parsed = parse_code_payload(payload)
                rec = {
                    "source": source_name or os.path.basename(pdf_path),
                    "page": i + 1,
                    "format": item["format"],
                    "payload": payload,
                    **parsed,
                    "printed_name": "",
                    "printed_uhid": "",
                    "printed_bar_cd": "",
                    "printed_ids": "",
                    "ocr_excerpt": "",
                    "related": "inconclusive",
                    "reasons": [],
                }
                hits.append(rec)
                if payload not in unique_payload_page:
                    unique_payload_page[payload] = (i + 1, img)
    finally:
        doc.close()

    ocr_budget = 0
    ocr_by_payload: Dict[str, Dict[str, str]] = {}
    for payload, (_page, img) in unique_payload_page.items():
        if ocr_budget >= MAX_OCR_UNIQUE:
            break
        ocr_budget += 1
        ocr_by_payload[payload] = _identifiers_from_ocr(_ocr_page(img))

    for rec in hits:
        extra = ocr_by_payload.get(rec["payload"]) or {}
        rec.update({k: extra[k] for k in extra if extra.get(k)})
    return hits


def scan_file_items(file_items: Sequence[Tuple[str, bytes]]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for name, data in file_items or []:
        if not data:
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                tmp.flush()
                tmp_path = tmp.name
            hits.extend(scan_pdf(tmp_path, source_name=name))
        except Exception as exc:
            print(f"⚠️ QR/barcode scan failed for {name}: {exc}", flush=True)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    return hits


def scan_pdf_paths(paths: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """paths: (pdf_path, source_name)."""
    hits: List[Dict[str, Any]] = []
    for pdf_path, name in paths or []:
        try:
            hits.extend(scan_pdf(pdf_path, source_name=name))
        except Exception as exc:
            print(f"⚠️ QR/barcode scan failed for {name}: {exc}", flush=True)
    return hits


def _names_related(patient_name: str, blob: str) -> bool:
    p_tokens = [t for t in _tokens(patient_name, _TITLE_STOP) if not t.isdigit()]
    if not p_tokens or not blob:
        return False
    b = _norm(blob)
    last = p_tokens[-1]
    if last not in b:
        return False
    if len(p_tokens) == 1:
        return True
    return any(t in b for t in p_tokens[:-1])


def _hospital_related(hospital: str, host: str, issuer_hint: str) -> Optional[bool]:
    if not hospital:
        return None
    h_tokens = _tokens(hospital, _HOSPITAL_STOP)
    if not h_tokens:
        return None
    blob = _norm(f"{host} {issuer_hint}")
    if not blob:
        return None
    if any(t in blob for t in h_tokens):
        return True
    # issuer "felix" vs hospital "Felix Hospital"
    if issuer_hint and issuer_hint in _norm(hospital):
        return True
    # clear mismatch only when issuer looks like a different hospital brand
    if issuer_hint and len(issuer_hint) >= 4 and issuer_hint not in _norm(hospital):
        if not any(t in issuer_hint for t in h_tokens):
            return False
    return None


def _id_in_text(*ids: str, text: str = "") -> bool:
    blob = (text or "").upper()
    for raw in ids:
        val = str(raw or "").strip().upper()
        if len(val) >= 6 and val in blob:
            return True
    return False


def match_hits_to_identity(
    hits: List[Dict[str, Any]],
    identity: Optional[Dict[str, Any]] = None,
    case_text: str = "",
) -> List[Dict[str, Any]]:
    identity = identity or {}
    patient = str(identity.get("patient_name") or identity.get("name") or "").strip()
    hospital = str(identity.get("hospital") or "").strip()
    uhid = str(identity.get("uhid") or identity.get("hospital_reg_no") or "").strip()
    corpus = case_text or ""
    identity_blob = " ".join(
        str(identity.get(k) or "")
        for k in ("patient_name", "name", "uhid", "hospital_reg_no", "ip_number", "bill_no")
    )
    match_blob = f"{corpus} {identity_blob}"

    for rec in hits:
        reasons: List[str] = []
        related: Optional[bool] = None
        page_blob = " ".join(
            str(rec.get(k) or "")
            for k in (
                "printed_name", "printed_uhid", "printed_ids", "printed_bar_cd",
                "ocr_excerpt", "payload", "patient_name", "uhid", "bill_no",
            )
        )
        if patient and _names_related(patient, page_blob):
            related = True
            reasons.append(
                f"Printed header next to the code names {rec.get('printed_name') or patient}"
            )
        payload_name = str(rec.get("patient_name") or "")
        if patient and payload_name and _names_related(patient, payload_name):
            related = True
            reasons.append("Decoded payload contains this patient's name")
        elif patient and payload_name and not _names_related(patient, payload_name):
            related = False
            reasons.append(
                f"Decoded payload name '{payload_name}' does not match claimed patient '{patient}'"
            )

        hosp_rel = _hospital_related(
            hospital, str(rec.get("host") or ""), str(rec.get("issuer_hint") or "")
        )
        if hosp_rel is True:
            if related is not False:
                related = True
            reasons.append(
                f"QR/barcode issuer ({rec.get('host') or rec.get('issuer_hint')}) "
                f"matches claimed hospital {hospital}"
            )
        elif hosp_rel is False:
            related = False
            reasons.append(
                f"QR/barcode issuer ({rec.get('host') or rec.get('issuer_hint')}) "
                f"does not match claimed hospital {hospital}"
            )

        bill = str(rec.get("bill_no") or rec.get("uhid") or "")
        if bill and _id_in_text(bill, uhid, rec.get("printed_uhid") or "", text=match_blob):
            if related is not False:
                related = True
            reasons.append(f"Decoded ID {bill} also appears in the case documents")

        rec["related"] = (
            "yes" if related is True else "no" if related is False else "inconclusive"
        )
        rec["reasons"] = reasons[:4]
    return hits


def identity_from_result(result: Optional[dict], case_text: str = "") -> Dict[str, str]:
    result = result or {}
    patient = result.get("patient_details") or {}
    claim = result.get("claim_details") or {}
    name = str(patient.get("name") or "").strip()
    hospital = str(claim.get("hospital") or "").strip()
    uhid = str(patient.get("hospital_reg_no") or patient.get("uhid") or "").strip()
    if not uhid:
        m = _UHID_RE.search(case_text or "")
        if m:
            uhid = m.group(1)
    return {
        "patient_name": name,
        "hospital": hospital,
        "uhid": uhid,
        "name": name,
    }


def build_document_codes_report(
    hits: List[Dict[str, Any]],
    *,
    identity: Optional[Dict[str, Any]] = None,
    case_text: str = "",
    scanned_files: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    hits = match_hits_to_identity(list(hits or []), identity, case_text)
    printed = extract_printed_codes(case_text or "")
    groups: Dict[str, Dict[str, Any]] = {}
    for rec in hits:
        key = rec.get("payload") or ""
        g = groups.setdefault(
            key,
            {
                "format": rec.get("format"),
                "payload": rec.get("payload"),
                "kind": rec.get("kind"),
                "host": rec.get("host"),
                "bill_no": rec.get("bill_no"),
                "related": rec.get("related"),
                "reasons": list(rec.get("reasons") or []),
                "printed_name": rec.get("printed_name") or "",
                "printed_uhid": rec.get("printed_uhid") or "",
                "printed_bar_cd": rec.get("printed_bar_cd") or "",
                "pages": [],
            },
        )
        loc = f"{rec.get('source')} p.{rec.get('page')}"
        if loc not in g["pages"]:
            g["pages"].append(loc)
        if rec.get("related") == "no":
            g["related"] = "no"
        elif rec.get("related") == "yes" and g["related"] != "no":
            g["related"] = "yes"

    yes = sum(1 for g in groups.values() if g.get("related") == "yes")
    no = sum(1 for g in groups.values() if g.get("related") == "no")
    if no:
        overall = "mismatch"
        related_label = "No — at least one code does not belong to this patient/hospital"
    elif yes:
        overall = "related"
        related_label = "Yes — codes belong to this patient / claimed hospital"
    elif groups:
        overall = "decoded_inconclusive"
        related_label = (
            "Decoded, but payload has no patient name — relatedness is based on "
            "hospital issuer and printed headers"
        )
    else:
        overall = "none_found"
        related_label = "No QR code or barcode could be decoded from the uploaded PDFs"

    files_with = sorted({h.get("source") for h in hits if h.get("source")})
    return {
        "decoder_available": decoder_available(),
        "scanned": True,
        "overall": overall,
        "related_to_patient": related_label,
        "decoded_count": len(hits),
        "unique_payloads": len(groups),
        "files_with_codes": files_with,
        "scanned_files": list(scanned_files or []),
        "printed_bar_codes": printed,
        "unique_codes": list(groups.values()),
        "hits": hits,
        "summary": _summary_sentence(overall, hits, groups, printed, files_with),
    }


def _summary_sentence(
    overall: str,
    hits: List[dict],
    groups: Dict[str, dict],
    printed: List[str],
    files_with: List[str],
) -> str:
    if not decoder_available():
        return (
            "QR/barcode decoder is not installed on this server, so document codes "
            "could not be scanned."
        )
    if overall == "none_found":
        extra = ""
        if printed:
            extra = (
                f" Printed BAR CD / barcode numbers were read from report text: "
                f"{', '.join(printed[:6])}."
            )
        return (
            "Pages were scanned; no machine-readable QR code or barcode was decoded."
            + extra
        )
    bill_nos = [g.get("bill_no") for g in groups.values() if g.get("bill_no")]
    hosts = [g.get("host") for g in groups.values() if g.get("host")]
    bits = [
        f"{len(hits)} code(s) decoded across {', '.join(files_with) or 'uploaded files'} "
        f"({len(groups)} unique payload(s))."
    ]
    if hosts:
        bits.append("Issuer: " + ", ".join(dict.fromkeys(hosts)))
    if bill_nos:
        bits.append("Decoded bill/service IDs: " + ", ".join(dict.fromkeys(bill_nos)))
    if printed:
        bits.append("Printed BAR CD: " + ", ".join(printed[:6]))
    return " ".join(bits)


def format_document_codes_block(report: Optional[dict]) -> str:
    if not report or not report.get("scanned"):
        return ""
    lines = [
        "=== DOCUMENT QR / BARCODE AUDIT (authoritative; decoded from page images) ===",
        "Remote verification URLs were decoded locally and were NOT fetched.",
        f"Related to this patient? {report.get('related_to_patient') or 'Unknown'}",
        f"Summary: {report.get('summary') or ''}",
    ]
    for g in (report.get("unique_codes") or [])[:12]:
        pages = ", ".join(g.get("pages") or [])
        reasons = "; ".join(g.get("reasons") or []) or "No extra identifiers in payload"
        lines.append(
            f"- {g.get('format')}: {g.get('payload')} "
            f"(pages: {pages}; related={g.get('related')}; {reasons})"
        )
        extra = []
        if g.get("printed_name"):
            extra.append(f"printed name {g['printed_name']}")
        if g.get("printed_uhid"):
            extra.append(f"printed UHID/IP {g['printed_uhid']}")
        if g.get("printed_bar_cd"):
            extra.append(f"printed BAR CD {g['printed_bar_cd']}")
        if extra:
            lines.append("  Header on same page: " + "; ".join(extra))
    printed = report.get("printed_bar_codes") or []
    if printed:
        lines.append("Printed BAR CD / barcode numbers from OCR text: " + ", ".join(printed[:8]))
    if not report.get("unique_codes"):
        lines.append(
            "No QR/barcode was decoded. Do not claim images were missing; the pages were scanned."
        )
    return "\n".join(lines)


def codes_to_fraud_findings(report: Optional[dict]) -> List[Dict[str, str]]:
    if not report or not report.get("scanned"):
        return []
    findings: List[Dict[str, str]] = []
    if report.get("overall") == "mismatch":
        mismatched = [
            g for g in (report.get("unique_codes") or []) if g.get("related") == "no"
        ]
        evidence = "; ".join(
            f"{g.get('payload')} ({', '.join(g.get('pages') or [])})"
            for g in mismatched[:4]
        )
        findings.append({
            "category": "documentation_abuse",
            "indicator": "QR / barcode identity mismatch",
            "evidence": evidence or report.get("summary") or "",
            "severity": "High",
            "recommendation": (
                "Hold the claim — decoded document codes do not match this patient or hospital. "
                "Query the hospital for authentic reports."
            ),
        })
    return findings


def apply_document_codes(
    result: dict,
    hits: List[Dict[str, Any]],
    *,
    case_text: str = "",
    scanned_files: Optional[Sequence[str]] = None,
) -> dict:
    """Attach document_codes to the audit result and merge FWA findings."""
    identity = identity_from_result(result, case_text)
    report = build_document_codes_report(
        hits, identity=identity, case_text=case_text, scanned_files=scanned_files
    )
    result["document_codes"] = {
        k: v for k, v in report.items() if k != "hits"
    }
    extras = codes_to_fraud_findings(report)
    if extras:
        fraud = result.setdefault("fraud_abuse", {})
        if not isinstance(fraud, dict):
            fraud = {"findings": [], "summary": "", "risk_level": ""}
            result["fraud_abuse"] = fraud
        findings = list(fraud.get("findings") or [])
        existing = {_norm(str(f.get("indicator") or "")) for f in findings if isinstance(f, dict)}
        for item in extras:
            if _norm(item["indicator"]) not in existing:
                findings.append(item)
        fraud["findings"] = findings
        result["fraud_abuse_findings"] = findings
        if any(_norm(f.get("severity")) == "high" for f in findings if isinstance(f, dict)):
            fraud["risk_level"] = "High"
    return result
