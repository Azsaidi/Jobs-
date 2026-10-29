"""ATS (applicant tracking system) keyword analysis.

ATS ranking is mostly literal: does the CV contain the same skill / tool / qualification
terms the job description uses? We extract those terms from the job description, then
measure how many the CV covers.
"""

from __future__ import annotations

import re
from collections import Counter

# A broad, profession-neutral vocabulary. Your own profile skills are always added on top.
VOCABULARY = """
SQL; Python; Java; JavaScript; TypeScript; C#; C++; Go; Golang; Kotlin; Swift; Scala; Ruby; PHP; Rust; HTML; CSS;
React; Angular; Vue; Node.js; Django; Flask; FastAPI; Spring; .NET; GraphQL; REST APIs; Microservices;
AWS; Azure; GCP; Google Cloud; Docker; Kubernetes; Terraform; CI/CD; Jenkins; GitHub Actions; Linux; Git;
PostgreSQL; MySQL; MongoDB; Redis; Kafka; Spark; Hadoop; Airflow; dbt; Snowflake; BigQuery; Databricks; ETL;
Data Warehouse; Data Modeling; Data Analysis; Data Visualization; Power BI; Tableau; Looker; Excel; VBA;
Machine Learning; Deep Learning; NLP; Computer Vision; LLM; Generative AI; TensorFlow; PyTorch; scikit-learn;
Statistics; A/B Testing; Forecasting; Pandas; R;
Agile; Scrum; Kanban; Jira; Confluence; Project Management; Program Management; Product Management;
Stakeholder Management; Change Management; Risk Management; Vendor Management; Budgeting; Business Analysis;
Requirements Gathering; Process Improvement; Lean; Six Sigma; PMP; PRINCE2; ITIL; TOGAF;
Cybersecurity; SIEM; SOC; ISO 27001; Penetration Testing; IAM; Network Security; Cloud Security;
Financial Analysis; Financial Modeling; Financial Reporting; FP&A; IFRS; GAAP; VAT; Corporate Tax; Audit;
Internal Audit; Accounts Payable; Accounts Receivable; Reconciliation; Treasury; Cash Flow; Valuation; M&A;
CFA; ACCA; CPA; CMA; SAP; Oracle; Oracle Fusion; Microsoft Dynamics; NetSuite; QuickBooks; ERP; CRM;
Salesforce; HubSpot; Compliance; AML; KYC; Regulatory Reporting; Credit Risk; Underwriting;
Digital Marketing; SEO; SEM; Google Analytics; Google Ads; Social Media; Content Marketing; Brand Management;
Performance Marketing; Email Marketing; Marketing Automation; Market Research; Copywriting; PR;
Sales; Business Development; Account Management; Key Account Management; Lead Generation; Negotiation;
B2B; B2C; SaaS; E-commerce; Retail; Customer Success; Customer Service; Customer Experience; Pipeline Management;
Recruitment; Talent Acquisition; HR; HRIS; Payroll; Employee Relations; Performance Management; Onboarding;
Learning and Development; Compensation and Benefits; CIPD; SHRM; Emiratisation; WPS; MOHRE;
Supply Chain; Logistics; Procurement; Inventory Management; Warehouse Management; Demand Planning;
Operations Management; Facilities Management; Health and Safety; HSE; NEBOSH; ISO 9001; Quality Assurance;
Quality Control; AutoCAD; Revit; BIM; Primavera; MS Project; Civil Engineering; MEP; Construction Management;
Contract Management; FIDIC; Estimation; Quantity Surveying;
Mechanical Engineering; Aerospace Engineering; Mechanical Design; Manufacturing Engineering; Process Engineering;
Materials Engineering; Quality Engineering; Systems Engineering; Requirements Management; Design for Manufacture;
DFM; DFMA; CAD; CAE; CATIA; Siemens NX; SolidWorks; Fusion 360; Creo; Inventor; Technical Drawings; GD&T; Tolerancing;
FEA; Finite Element Analysis; Stress Analysis; Structural Analysis; Thermal Analysis; Modal Analysis; Fatigue;
ANSYS; Abaqus; Nastran; Patran; HyperMesh; COMSOL; MATLAB; Simulink; CFD; ANSYS Fluent; STAR-CCM+; OpenFOAM;
Composites; Composite Materials; Carbon Fibre; Carbon Fiber; CFRP; Prepreg; Autoclave; Layup; Resin Infusion;
Thermoplastics; Adhesive Bonding; Welding; NDT; NDI; Ultrasonic Testing; Metrology; Tooling; Fixture Design; Jigs;
CNC; Machining; Additive Manufacturing; 3D Printing; Sheet Metal; Injection Moulding; Prototyping;
Aerodynamics; Avionics; Propulsion; Aerostructures; Airframe; UAV; Drones; Satellite; Spacecraft; Flight Testing;
MRO; Airworthiness; EASA; GCAA; FAA; Part 145; Part 21; AS9100; AS9102; FAI; FMEA; DFMEA; PFMEA; Root Cause Analysis;
8D; SPC; PPAP; APQP; Continuous Improvement; Kaizen; 5S; PLM; Teamcenter; Windchill;
Nursing; DHA; DOH; MOH; Patient Care; Clinical; Pharmacy; BLS; ACLS;
Teaching; Curriculum Development; KHDA; IB; IGCSE;
Hospitality; F&B; Front Office; Revenue Management; Event Management;
UX; UI; Figma; User Research; Wireframing; Prototyping; Adobe Creative Suite; Photoshop; Illustrator;
Leadership; Team Leadership; People Management; Mentoring; Strategic Planning; Strategy; P&L;
Communication; Presentation Skills; Problem Solving; Analytical Skills; Cross-functional;
Arabic; English; French; Hindi; Urdu; UAE Driving License
"""

# Short terms (SQL, Go, R, PR, dbt) are matched case-sensitively so ordinary words don't count.
_CASE_SENSITIVE_MAX_LEN = 3
_ACRONYM_STOPWORDS = {
    "UAE", "THE", "AND", "OUR", "YOU", "FOR", "WE", "US", "CV", "JD", "LLC", "FZE", "FZ", "FZCO", "PJSC", "DMCC",
    "CEO", "CTO", "CFO", "COO", "HQ", "EMEA", "MENA", "GCC", "KSA", "USA", "UK", "EU", "AED", "USD", "N/A", "TBD",
    "ALL", "NOT", "ARE", "ANY", "NEW", "TO", "OF", "IN", "ON", "AT", "OR", "AN", "BE", "IS", "IT", "AS", "BY",
    "PO", "PM", "AM", "ID", "OK", "EOE", "FAQ", "DEI", "WHO", "WHAT", "WHY", "HOW", "JOIN", "ROLE", "ABOUT",
}

_vocab_cache: list[str] | None = None


def vocabulary() -> list[str]:
    global _vocab_cache
    if _vocab_cache is None:
        _vocab_cache = [t.strip() for t in VOCABULARY.replace("\n", " ").split(";") if t.strip()]
    return _vocab_cache


def _pattern(term: str) -> re.Pattern:
    flags = 0 if len(term) <= _CASE_SENSITIVE_MAX_LEN else re.I
    escaped = re.escape(term).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9+#])", flags)


def count_term(term: str, text: str) -> int:
    return len(_pattern(term).findall(text))


def extract_keywords(job_text: str, extra_terms: list[str] | None = None, title: str = "", limit: int = 30) -> list[str]:
    """Terms from the vocabulary (+ your skills) that appear in the job ad, most important first.

    Importance = frequency, boosted when the term is in the job title; unknown acronyms
    that appear repeatedly (e.g. 'IFRS 17', 'SCADA') are picked up too.
    """
    terms = {t.lower(): t for t in vocabulary()}
    for t in extra_terms or []:
        terms.setdefault(t.lower(), t)
    scores: Counter = Counter()
    for term in terms.values():
        n = count_term(term, job_text)
        if n:
            scores[term] = n + (3 if title and count_term(term, title) else 0)
    known_upper = {t.upper() for t in terms.values()}
    acronyms = Counter(re.findall(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9&]{1,7})(?![A-Za-z0-9])", job_text))
    for acr, n in acronyms.items():
        if n >= 2 and acr not in _ACRONYM_STOPWORDS and acr not in known_upper and not acr.isdigit():
            scores[acr] = n
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [t for t, _ in ranked[:limit]]


def coverage(text: str, keywords: list[str]) -> tuple[float, list[str], list[str]]:
    """Share of keywords present in `text` (0-100), matched list, missing list."""
    if not keywords:
        return 0.0, [], []
    matched = [k for k in keywords if count_term(k, text)]
    missing = [k for k in keywords if k not in matched]
    # earlier keywords matter more
    weights = [1.0 / (1 + i * 0.05) for i in range(len(keywords))]
    got = sum(w for k, w in zip(keywords, weights) if k in matched)
    return round(100 * got / sum(weights), 1), matched, missing


STANDARD_HEADINGS = ["Professional Summary", "Core Skills", "Professional Experience", "Education"]


def lint_cv_text(text: str, email: str, phone: str | None) -> list[str]:
    """Basic ATS-readability checks on the plain text extracted from a rendered CV."""
    issues = []
    if email and email.lower() not in text.lower():
        issues.append("email address not found in CV text")
    if phone and re.sub(r"\D", "", phone)[-7:] not in re.sub(r"\D", "", text):
        issues.append("phone number not found in CV text")
    for h in STANDARD_HEADINGS:
        if h.lower() not in text.lower():
            issues.append(f"missing standard heading '{h}'")
    words = len(text.split())
    if words < 250:
        issues.append(f"CV is short ({words} words) - ATS ranks thin CVs lower")
    if words > 1300:
        issues.append(f"CV is long ({words} words) - aim for 2 pages")
    return issues
