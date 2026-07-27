# Medical Audit Platform — Architecture V2 (R&D)

**Audience:** Engineering + product leadership  
**Status:** Target architecture (phased migration from v5 monolith)  
**Goal:** Match or beat NotebookLM/Gemini-class audit quality while remaining production-ready for Indian insurance medical audits.

---

## 0. Executive verdict (opinionated)

| Decision | Recommendation | Why |
|---|---|---|
| Model strategy | **Hybrid**: Gemini/Claude/GPT-class for doc understanding + long context; smaller models for extraction/classification | NotebookLM quality comes from document grounding, not one mega-prompt |
| Context strategy | **Structured Case Graph + Hybrid RAG + selective long-context** | Pure long-context hallucinates less on structure but fails on guideline citation without retrieval |
| Orchestration | **Planner → specialist agents → verifier → report** | One LLM call is the root cause of missing fields and cross-case leakage |
| Data model | **Evidence-linked Medical Case Record (MCR)** | Every claim must cite `doc_id + page + span` |
| Migration | **Strangler pattern** over rewrite | Keep FastAPI/Streamlit; replace pipeline stages behind an orchestrator |

**What NotebookLM does well that we currently do not:**
1. Treats each upload as a first-class source with citations  
2. Reasons over a *corpus*, not a truncated 12k blob  
3. Separates “what the docs say” from “what the auditor concludes”  
4. Keeps page-level grounding visible to the user  

**What we can do better than a generic NotebookLM setup:**
1. Insurance-specific guideline RAG with ICD/specialty filters  
2. Deterministic claim/date extractors + fraud rules  
3. Human-in-the-loop claim workflow with editable findings  
4. Historical similar-claim retrieval and fraud pattern memory  

---

## 1. High-level architecture (ASCII)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                                     │
│  Streamlit / Future React  │  Reviewer Console  │  Admin Dashboard       │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ HTTPS / JWT
┌─────────────────────────────▼────────────────────────────────────────────┐
│                         API GATEWAY (FastAPI)                             │
│  Auth · Rate limits · Audit logging · Upload · Job status · HITL APIs    │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ enqueue
┌─────────────────────────────▼────────────────────────────────────────────┐
│                    ORCHESTRATOR / PLANNER (Celery/RQ)                    │
│  Plan stages → fan-out agents → merge → verify → render                  │
└──┬──────────┬──────────┬──────────┬──────────┬──────────┬───────────────┘
   │          │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼          ▼
 Doc AI    Entity     Timeline   Guideline   Audit      Fraud
 Ingest    Extract    Builder    Retriever   Reasoner   Detector
   │          │          │          │          │          │
   └──────────┴──────────┴────┬─────┴──────────┴──────────┘
                              ▼
                    ┌─────────────────────┐
                    │ Evidence Verifier   │
                    │ QA Agent            │
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Report Generator    │
                    │ (PDF + JSON + UI)   │
                    └──────────┬──────────┘
                               ▼
        ┌──────────────┬───────┴────────┬──────────────┐
        ▼              ▼                ▼              ▼
   PostgreSQL      Object Store     Vector DB      Observability
   (MCR, jobs)     (S3/PDF)         (guidelines)   (traces/metrics)
```

---

## 2. Detailed component diagram

```
[Upload Service]
   │ stores originals → S3
   │ creates AuditJob(status=queued)
   ▼
[Document Intelligence Service]
   ├── Layout parser (sections, tables, forms)
   ├── OCR / Vision (handwriting, stamps)
   ├── Doc classifier (discharge, bill, preauth, lab, radiology, query)
   └── emits: PageBlocks[{doc_id, page, type, text, bbox, confidence}]
   ▼
[Medical Entity Service]
   ├── NER: disease, drug, dose, lab, vitals, ICD, CPT, doctor
   ├── Normalizers (RxNorm-like local map, lab units)
   └── emits: Entities[{type, value, unit, evidence_span}]
   ▼
[Case Graph Builder]
   ├── Patient / Admission / Diagnosis / Procedure / Med / Inv / Bill nodes
   ├── Cross-doc conflict detection
   └── emits: MedicalCaseRecord (authoritative structured state)
   ▼
[Guideline RAG Service]
   ├── Hybrid BM25 + dense
   ├── Specialty / disease metadata filters
   ├── Parent-child chunks + reranker
   └── emits: GuidelineEvidence[{chunk_id, score, cite}]
   ▼
[Audit Reasoning Service]
   ├── Long-context over Case Graph + selected pages
   ├── Specialist prompts (clinical / billing / policy)
   └── emits: Findings[{claim, severity, evidence[], guideline_refs[]}]
   ▼
[Verifier + QA]
   ├── Drop unsupported claims
   ├── Check guideline ID exists
   ├── Contradiction scan
   └── confidence scoring
   ▼
[Report + HITL]
```

---

## 3. Agent interaction diagram

```
Planner
  │ assigns tasks with inputs/outputs contracts
  ├─► DocClassifierAgent ──► DocInventory
  ├─► LayoutOCRAgent ──────► PageBlocks
  ├─► EntityExtractionAgent ► Entities  ──┐
  ├─► TimelineAgent ────────► Timeline    ├─► CaseGraphBuilder ► MCR
  ├─► GuidelineAgent(MCR) ─► GuidelineHits┘
  ├─► AuditReasoningAgent(MCR, Guidelines) ► DraftFindings
  ├─► FraudAgent(MCR, DraftFindings) ──────► FraudFindings
  ├─► EvidenceVerifier(Draft+Fraud) ───────► VerifiedFindings
  ├─► ReportAgent(Verified) ───────────────► AuditReport
  └─► QAAgent(Report) ─────────────────────► Accept | Escalate
```

### Agent responsibilities

| Agent | Responsibility | Model tier |
|---|---|---|
| **Planner** | Decompose job; decide which docs need vision; set specialty route | Small / rules |
| **Doc Classification** | Label each PDF (discharge, bill, preauth…) | Small |
| **Layout / OCR** | Page blocks, tables, handwriting | Doc AI + vision |
| **Entity Extraction** | Structured medical objects with evidence spans | Small/medium |
| **Timeline** | Ordered clinical journey | Medium |
| **Guideline Retrieval** | Hybrid RAG + filters + rerank | Embeddings + reranker |
| **Audit Reasoning** | Clinical necessity, deviations, billing | Large |
| **Fraud** | Non-disclosure, upcoding, contradictions | Rules + medium |
| **Evidence Verifier** | Kill hallucinated claims | Medium |
| **Report Generator** | PDF/UI; never invent facts | Template + small |
| **QA** | Completeness gate before release | Rules + small |

---

## 4. Data flow diagram

```
PDF bytes
  → S3 (encrypted)
  → PageBlocks (JSON in Postgres + optional page images)
  → Entities (JSON)
  → MedicalCaseRecord (JSON, versioned)
  → GuidelineHits (IDs only + text cache)
  → Findings (each with evidence_span_ids[])
  → AuditReport JSON
  → PDF render (deterministic from JSON)
```

**Critical rule:** The PDF generator must never call an LLM. It only renders verified JSON.

---

## 5. Sequence diagram (happy path)

```
User → API: POST /audits (files, guidelines[])
API → Queue: AuditJob
Worker → Planner: plan(job)
Planner → DocAI: process_all
DocAI → Worker: PageBlocks
Worker → Entity: extract
Entity → Worker: Entities
Worker → Graph: merge → MCR
Worker → RAG: retrieve(MCR)
RAG → Worker: GuidelineHits
Worker → Auditor: reason(MCR, hits)
Auditor → Worker: DraftFindings
Worker → Verifier: verify
Verifier → Worker: VerifiedFindings
Worker → Report: render
Worker → DB: store report + citations
API → User: GET /audits/{id} (or Streamlit poll)
Reviewer → API: PATCH findings / approve
```

---

## 6. End-to-end processing flow

1. **Ingest** — dedupe uploads, virus scan, store S3, create job  
2. **Document intelligence** — layout + OCR + classify per file  
3. **Map step** — per-document structured extraction (already started in v5)  
4. **Merge** — Case Facts Ledger / MCR with source priority (discharge > preauth > clinical > query)  
5. **Completeness gate** — block if patient/diagnosis/admission missing without override  
6. **Timeline reconstruction**  
7. **Guideline retrieval** — specialty-filtered hybrid RAG  
8. **Multi-pass audit** — clinical, documentation, billing, fraud (separate prompts)  
9. **Evidence verification** — drop unsupported claims  
10. **Report generation** — include Document Analysis + citations  
11. **HITL** — medium/low confidence findings flagged for reviewer  

---

## 7. Suggested folder structure

```
backend/
  agents/                 # NEW — agent contracts + orchestrator
    schemas.py
    planner.py
    timeline_agent.py
    evidence_verifier.py
    orchestrator.py
  document_ai/            # FUTURE — layout, tables, forms
  entities/               # FUTURE — NER + normalizers
  case_graph/             # FUTURE — MCR builder
  rag/                    # IMPROVE — hybrid, rerank, parent-child
  services/
    audit_pipeline.py     # Thin wrapper → orchestrator
  workers/                # FUTURE — Celery tasks
docs/
  ARCHITECTURE_V2_MEDICAL_AUDIT.md
```

---

## 8. Microservice breakdown (target)

| Service | Owns | Scale independently? |
|---|---|---|
| `api` | Auth, upload, HITL | Yes |
| `doc-ai` | OCR/layout/vision | Yes (GPU/CPU heavy) |
| `extraction` | Entities / map LLM | Yes |
| `rag` | Indexes + retrieve | Yes |
| `audit-worker` | Reasoning + verify | Yes |
| `report` | PDF render | Optional |
| `postgres` | System of record | Managed |
| `vector` | Guidelines (+ later claims) | Managed |

**Phase 0–1:** keep monolith modules, extract services only when queue latency / OCR load demands it.

---

## 9. API design (target)

```
POST   /v2/audits
       multipart: files[], guideline_ids[], options{hitl:bool}
       → {audit_id, job_id}

GET    /v2/audits/{id}
       → status, progress, report_json, citations

GET    /v2/audits/{id}/documents/{doc_id}/pages/{n}
       → page text + bbox highlights for a finding

GET    /v2/audits/{id}/evidence/{span_id}
       → quoted span + surrounding context

PATCH  /v2/audits/{id}/findings/{finding_id}
       body: {status: accepted|rejected|edited, note}

POST   /v2/audits/{id}/approve
POST   /v2/audits/{id}/reprocess
GET    /v2/audits/{id}/similar-claims   # bonus
```

---

## 10. Database schema suggestions

```sql
audits(id, user_id, status, specialty, created_at, completed_at, mcr_json, report_json)
audit_documents(id, audit_id, filename, s3_key, doc_type, page_count, sha256)
page_blocks(id, document_id, page, block_type, text, bbox_json, confidence)
evidence_spans(id, document_id, page, start_char, end_char, quote)
entities(id, audit_id, type, value, unit, evidence_span_id, normalized_value)
findings(id, audit_id, category, claim, severity, confidence, guideline_ref, status)
finding_evidence(finding_id, evidence_span_id)
review_events(id, audit_id, user_id, action, payload_json, created_at)
guideline_versions(id, name, version, s3_key, indexed_at)
```

---

## 11. Vector database schema

**Guidelines collection**
```
id, text, embedding,
metadata: {
  guideline_name, version, specialty, disease_topics[],
  section_title, parent_id, page_start, page_end, chunk_level
}
```

**Optional claims memory collection** (bonus)
```
id, text_summary, embedding,
metadata: {diagnosis, hospital, outcome, fraud_tags[], audit_id}
```

**Indexing strategy**
- Parent chunks: 1200–1800 tokens (section)  
- Child chunks: 250–400 tokens, 40–60 overlap  
- Retrieve children → expand to parent for LLM context  
- Hybrid: BM25 (keyword ICD/drug names) + dense (semantic) → RRF → cross-encoder rerank top 50 → top 8–12  

**Embeddings:** `text-embedding-3-large` or Gemini embedding; keep one model per index version.

**Cache:** guideline index by `(name, content_hash)`; retrieval cache keyed by `(guideline_hash, query_hash)` TTL 24h.

---

## 12. Prompt strategy

| Stage | Prompt style | Output |
|---|---|---|
| Classification | Short enum JSON | `doc_type` |
| Extraction | Schema-strict JSON, “this file only” | Entities |
| Timeline | Ordered events from MCR | Timeline |
| Audit clinical | Adversarial auditor; cite span IDs | Findings |
| Audit billing | Policy annexure rules | Findings |
| Verify | “Delete if no span supports claim” | Findings |
| Report summary | Bullets only from verified findings | Summary |

**Hard rules**
- Never put Zenoxa/example drugs in prompts for unrelated specialties  
- Ledger/MCR is authoritative for identity fields  
- Report agent cannot invent labs/meds  

---

## 13. Retrieval strategy (recommended hybrid)

**Compare approaches**

| Approach | Accuracy | Cost | Latency | Verdict |
|---|---|---|---|---|
| Aggressive chunking + one RAG dump | Medium | Low | Low | Current; insufficient |
| Pure long-context (all pages) | High narrative | High | Medium | Good for small cases; weak guideline cite |
| **Hybrid: MCR + selected pages + hybrid RAG** | **Highest** | Medium | Medium | **Recommended** |

**Design:**  
1. Build MCR (structured)  
2. Attach full text of *critical* docs (discharge, bill, preauth) in long context  
3. Retrieve guideline parents via hybrid RAG  
4. Reasoner gets MCR + critical docs + guideline parents + citation IDs  

---

## 14. Evaluation framework

| Metric | Target (v2) | How |
|---|---|---|
| Entity extraction F1 | ≥ 0.85 | Gold set of 50 cases |
| Citation accuracy | ≥ 0.90 | Span must contain claim tokens |
| Hallucination rate | ≤ 5% findings | Human + verifier |
| Audit accuracy (reviewer accept) | ≥ 80% | HITL |
| Retrieval Precision@10 | ≥ 0.70 | Guideline gold queries |
| Recall@10 | ≥ 0.75 | Same |
| p95 latency | ≤ 8 min / case | Job metrics |
| Cost per audit | Track ₹/$ | Token + OCR |
| Reviewer acceptance rate | Rising week-over-week | Dashboard |

Golden set should include: hepatitis (Case 169), TN, ACS/CABG, hypoglycemia, alcohol/detox mismatch.

---

## 15. Document intelligence (beyond OCR)

**Recommended stack (phased)**
1. **Now:** PyMuPDF text + vision OCR (existing) + per-doc map  
2. **Next:** Layout-aware — Docling / Unstructured / Azure Document Intelligence / Google Document AI  
3. **Tables:** dedicated table extraction for bills & labs  
4. **Forms:** key-value for preauth (patient, policy, proposed date)  
5. **Handwriting:** Gemini/GPT vision on consult pages only (cost gate)  
6. **Section detection:** Discharge Summary → Diagnosis / Course / Advice  

Do **not** send every page to a frontier multimodal model. Classify first; route hard pages only.

---

## 16. Why structured medical representation beats raw text

Raw text prompting fails because:
- Truncation drops the discharge summary  
- Query-letter “asthma” overrides discharge “hepatitis”  
- Model invents drugs from prompt examples  
- No machine-checkable evidence  

Structured MCR enables:
- Priority merge rules  
- Completeness gates  
- Deterministic PDF fields  
- Verifier that checks `evidence_span_id` exists  

---

## 17. Timeline generation

Timeline agent builds:

```
Consult → Admission → Key labs/imaging → Procedures → Med changes → Discharge → Bill finalization
```

**Audit value:** LOS appropriateness, delayed investigations, post-op complications, billing vs clinical chronology.

---

## 18. Evidence-based auditing (NotebookLM-like)

Every finding must include:
```json
{
  "claim": "...",
  "evidence": [{"doc": "discharge.pdf", "page": 2, "quote": "..."}],
  "guideline_ref": {"name": "Viral Hepatitis...", "section": "..."},
  "confidence": 0.82,
  "explanation": "..."
}
```

UI: click finding → highlight page quote.

---

## 19. Human-in-the-loop

| Confidence | Action |
|---|---|
| ≥ 0.85 | Auto-include |
| 0.55–0.85 | Flag for reviewer |
| < 0.55 | Hold / escalate |
| High fraud | Always escalate |

Reviewer can edit finding text, change severity, attach note; feedback stored for eval.

---

## 20. Scalability

- Redis/SQS queue + Celery workers  
- OCR pool separate from LLM pool  
- Concurrent audits: N workers × semaphore on OpenAI/Gemini RPM  
- FAISS → Qdrant/pgvector when multi-tenant  
- Cache guideline indexes; cache map-step results by file SHA256  

---

## 21. Cost optimisation

| Spend most | Spend less |
|---|---|
| Audit reasoning (large model) | Doc type classification (small) |
| Hard handwritten pages (vision) | Typed PDF text (free) |
| Reranker on top-50 | Embedding all pages every time |
| Verifier on draft findings | Re-running full audit for typos |

Estimate: **60%** tokens on reasoning, **25%** map/extract, **10%** verify, **5%** classify/report.

---

## 22. Security & compliance

- Encrypt S3 at rest; TLS in transit  
- PHI minimization in logs (no full OCR dumps)  
- Role-based access (user/reviewer/admin)  
- Immutable audit trail of report versions + reviewer edits  
- Retention policy + purge job  
- Region: align with IRDAI / DPDP Act (India); HIPAA if US clients  

---

## 23. Suggested technology stack

| Layer | Recommendation |
|---|---|
| Frontend | Streamlit short-term; React reviewer console mid-term |
| Backend | FastAPI |
| Orchestration | Celery + Redis (or Temporal for complex HITL) |
| Document AI | Google Document AI / Azure DI + Gemini vision for handwriting |
| LLM | Extraction: gpt-4o-mini / Gemini Flash; Reasoning: GPT-4o / Gemini 2.5 Pro |
| Embeddings | text-embedding-3-large or Gemini |
| Vector | FAISS → Qdrant |
| DB | PostgreSQL |
| Blob | S3 |
| Queue | Redis / SQS |
| Monitoring | Prometheus + Grafana |
| Tracing | OpenTelemetry |
| Eval | Promptfoo / custom golden harness |
| Deploy | systemd/Docker on EC2 → ECS later |
| CI/CD | GitHub Actions |

---

## 24. Migration roadmap (practical)

| Phase | Outcome | Effort |
|---|---|---|
| **P0 (this PR)** | Architecture doc + agent schemas + planner + timeline + evidence verifier wired | 1–2 weeks |
| **P1** | Completeness gate; citation objects on findings; hybrid BM25 | 2–3 weeks |
| **P2** | Layout/table Doc AI; separate clinical vs billing audit passes | 4–6 weeks |
| **P3** | Queue workers; HITL console; eval harness | 6–8 weeks |
| **P4** | Similar claims memory; fraud graph; multi-modal page explorer | backlog |

---

## 25. Bonus — NotebookLM-inspired features

1. **Knowledge graph** of patient episode across docs  
2. **Interactive evidence explorer** (click → page highlight)  
3. **Cross-document Q&A** chat over one audit corpus  
4. **Historical similar claims** retrieval  
5. **Policy-specific reasoning packs** per insurer  
6. **Claim-vs-claim comparison** (resubmission)  
7. **Fraud pattern library** (room upcoding, PPI false flags, etc.)  
8. **Automatic executive summary** from verified findings only  
9. **Memory of reviewer corrections** → reduce repeat mistakes  

---

## 26. Trade-off summary

| Choice | Upside | Downside |
|---|---|---|
| Multi-agent | Quality, debuggability | Latency, cost |
| Structured MCR | Stable identity/facts | Extra extraction errors to handle |
| Hybrid RAG + long context | Best of both | More engineering |
| Gemini Doc AI | Strong layout | Vendor lock / data residency |
| Stay on OpenAI only | Less ops | May lag NotebookLM doc quality |

**Final recommendation:** Do **not** replace the stack with “upload to NotebookLM.” Rebuild **document grounding + structured case graph + multi-pass agents + verifier**, optionally using Gemini for document understanding where it wins on handwriting/layout.
