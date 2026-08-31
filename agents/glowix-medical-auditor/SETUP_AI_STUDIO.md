# Glowix Gemini Agent — what YOU must do

I **cannot** create the agent inside your Google account (that requires your login).  
I **did** create a complete agent pack in this repo that you paste/mount into Google AI Studio — the same style as your working POC.

## Pack location

```
agents/glowix-medical-auditor/
  .agents/AGENTS.md                          ← agent persona + report format
  .agents/skills/medical-audit/SKILL.md      ← audit workflow
  SETUP_AI_STUDIO.md                         ← this file
  knowledge/                                 ← put standing knowledge PDFs here (optional)
  STARTER_PROMPT.txt                         ← first message after upload
```

---

## Path A — Fastest (matches your AI Studio web-agent POC)

Use this if your POC was “upload PDFs in AI Studio chat / agent playground and get a perfect report”.

### Required from you

1. **Google account** with access to [Google AI Studio](https://aistudio.google.com/)
2. **Billing / API enabled** if you want higher limits (Pro models)
3. **Model**: prefer `gemini-3.6-flash` or `gemini-3.1-pro-preview` for deep audits
4. Open AI Studio → **Agents** (or Playground Agents tab)
5. Create agent / use blank template
6. Mount sources:
   - Inline or upload file → path `.agents/AGENTS.md` (copy contents from this repo)
   - Inline or upload → `.agents/skills/medical-audit/SKILL.md`
7. Optional standing knowledge (upload once into the agent environment / chat):
   - Glowix Expert Opinion sample (good NotebookLM DOCX → PDF)
   - Family Health Protector policy wording (if you always use IFFCO)
   - Common MOHFW guidelines you audit against
8. **Per case**: upload that case’s PDFs (Assessor + clinical + guideline for *this* case)
9. Paste `STARTER_PROMPT.txt` and run
10. Download / copy the Expert Opinion text → paste into Word/DOCX or your Glowix PDF template

### What you should see if it works
- Correct age / claim / policy / bill from Assessor
- Deep Q1–Q5 like your NotebookLM DOCX
- No “age 3 years” / “claim NA” / OCR-name High FWA nonsense

---

## Path B — Gemini Gem (gemini.google.com)

If you prefer consumer Gems:

1. gemini.google.com → Explore Gems → **New Gem**
2. Name: `Glowix Medical Auditor`
3. Paste contents of `.agents/AGENTS.md` into Instructions
4. Knowledge → Add files: `SKILL.md` + optional policy/guideline PDFs + 1 sample good report
5. Save
6. Each case: attach case PDFs in chat + starter prompt

Limitations: file size / count caps; less “agent sandbox” than AI Studio Agents.

---

## Path C — Wire agent into Glowix later (API)

When the Studio agent is validated:

1. Create a **Managed Agent** via Gemini API (`agents.create`) using the same `AGENTS.md` + skill files  
   Docs: https://ai.google.dev/gemini-api/docs/custom-agents
2. From Glowix backend: upload case files → call agent by ID → map text sections into `glowix_proforma_pdf`
3. Keep current FastAPI UI; replace brittle OCR→JSON pipeline with “agent report → PDF”

This is a **follow-up engineering step** after you confirm Studio quality.

---

## Checklist — send / prepare these

| Item | Why | Status |
|------|-----|--------|
| Google AI Studio login | Create agent | You |
| Confirm which POC UI you used (Agents tab vs plain chat vs Gem) | Same path | You tell me |
| Sample **good** report (NotebookLM DOCX / POC output) | Quality bar | You already have `MEDICAL AUDIT.docx` |
| Sample **bad** Glowix report (Bhagyashri) | Regression check | You have PDF |
| Case 193 zip (Assessor + clinical PDFs) | Live test | You have `recaseno_193` |
| Standing guidelines you always need | Mount as knowledge | You |
| Policy wording(s) | PED / waiting period depth | You |
| Auditor name/qualification if not Saharan | Letter footer | You |
| Decide: Studio-only ops vs later API into Glowix | Product direction | You |

---

## After you create it

Reply with:
1. Screenshot or “agent created” confirmation  
2. Whether case 193 output looks correct (age 58, claim `20260708000052`, bill ~79k, deep Q&A)  
3. Whether you want Path C (hook agent into Glowix website)

I can then help map the agent output into the existing Expert Opinion PDF generator — **without** rebuilding the broken OCR pipeline.
