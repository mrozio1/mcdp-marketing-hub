"""Operations checklists filled in with a field visit (from Jeremy LaForest's Word forms, Nov 2026).

onsite:  Orthodontic Regional Manager · Onsite Office Visit Checklist. Items are marked P (pass), A (action needed)
         or N/A. Sections 01-06 are scored (passes ÷ items marked P or A); 07 is the closing and has no score.
recurring: Regional Orthodontic Manager · Weekly & Monthly Recurring To-Do List, plus the weekly OPS report and the
         month-end update (text boxes).
"""
from __future__ import annotations

import json

DEPT = "operations"          # only this department (plus Management and admins) sees and fills these in

ONSITE = {
    "key": "onsite", "title": "Onsite office visit checklist",
    "intro": "P = Pass · A = Action needed · N/A = Not applicable. Verify applicable items, explain N/A entries, and record gaps, "
             "owners and due dates in the action plan.",
    "sections": [
        {"key": "01", "title": "Pre-visit / digital access", "scored": True, "items": [
            "Call during and after office hours; confirm correct routing, voicemail, hours, and emergency instructions across the location/pod.",
            "Check Google and website listing, contact details, online appointment request/booking, and broken links.",
            "Review prior visit action items, open escalations/tickets, recent patient feedback, staffing and schedule trends.",
            "Review action plans and Asana for known tasks and goals."]},
        {"key": "02", "title": "First impression / facility", "scored": True, "items": [
            "Exterior, signage, entrance, parking and accessibility are safe, clean and presentable.",
            "Lobby, front desk, vestibule, restrooms and treatment rooms are clean, organized and appropriately stocked.",
            "Lighting, televisions, visible wiring, furniture and common equipment are in good working order; maintenance issues reported.",
            "Patient-facing materials and forms are current, legible and approved."]},
        {"key": "03", "title": "Team / patient experience", "scored": True, "items": [
            "Greet team; assess morale, professionalism, uniforms and staff/patient interactions.",
            "Observe timely check-in, privacy at reception, communication of paperwork, insurance and patient financial responsibility.",
            "Review complaints/surveys and service recovery follow-up; identify coaching needs.",
            "Confirm staffing and daily huddle support the clinical schedule; coverage and training gaps identified."]},
        {"key": "04", "title": "Orthodontic operations / growth", "scored": True, "items": [
            "Review exam and same-day start availability, scheduling template, provider capacity and new-patient follow-up.",
            "Check treatment coordinator handoffs, financial presentation/consents, and RPP offer and documentation.",
            "Spot check clinical supplies and inventory levels.",
            "Confirm lab, scan, retainer and appliance workflows, turnaround delays and escalation paths."]},
        {"key": "05", "title": "Infection prevention / clinical safety", "scored": True, "items": [
            "Hand hygiene supplies available at each point of care; hand hygiene practices observed.",
            "Appropriate gloves, masks, protective eyewear and gowns available and used.",
            "Operatories cleaned and disinfected between patients; barriers changed as needed.",
            "Surface disinfectants used with labeled contact times and manufacturer instructions.",
            "Sterilization area maintains clear separation of dirty and clean workflows.",
            "Instruments transported, cleaned and reprocessed according to written procedures.",
            "Sterilizer biological (spore) testing records reviewed; failures investigated.",
            "Sterilizer mechanical/cycle and chemical indicator records reviewed.",
            "Autoclave maintenance/service records current; equipment operating properly.",
            "Instrument packages properly sealed, labeled and free of damage.",
            "Sterile instruments stored appropriately; single-use items not reprocessed.",
            "Dental-unit waterline treatment procedure and testing records reviewed.",
            "Sharps containers labeled, accessible, secure and below fill line.",
            "Regulated medical waste properly segregated, stored and removed; records available.",
            "Emergency kits and supplies checked for availability and expiration."]},
        {"key": "06", "title": "Compliance / people / security", "scored": True, "items": [
            "Dentist and applicable staff licenses/permits current; required postings verified.",
            "X-ray equipment registration and required inspections/documentation current.",
            "Radiography operators meet applicable training/authorization requirements.",
            "Needlestick/sharps injury reporting and exposure follow-up procedures available.",
            "Required incident/sharps records maintained securely, without disclosing employee health information.",
            "Primary and secondary chemical containers correctly labeled.",
            "Required workplace notices and emergency contact information posted.",
            "Eyewash equipment available and maintained, logs are current.",
            "Fire extinguishers inspected; emergency exits and egress paths clear.",
            "Confidential patient information disposed of using approved secure processes."]},
        {"key": "07", "title": "Closing / accountability", "scored": False, "items": [
            "Confirm annual interior and exterior building photos are current in SharePoint; take and upload photos when due.",
            "Review scorecard and operational priorities with doctor and office lead; acknowledge wins and team achievements.",
            "Agree on 2–3 highest-impact improvements, accountable owners and target dates.",
            "Open/route tickets through established departmental workflows; escalate significant clinical disputes or scope/product "
            "changes to the Clinical Standards & Review Board workflow.",
            "Send concise recap to Director of Operations and Chief Operating Officer; verify closure of prior action items at next visit."]},
    ],
    "texts": [],
    "escalation": "Immediate safety concerns / escalations",
    "escalation_note": "Escalate urgent safety or compliance concerns promptly, regardless of the overall score.",
    "action_plan": True,
}

RECURRING = {
    "key": "recurring", "title": "Weekly / monthly to-do",
    "intro": "Manage office performance and follow-through between onsite visits. Document findings and follow through with the "
             "responsible team. Completion means the review and resulting follow-up have been documented; unresolved actions stay "
             "open in the office/pod action plan or Asana.",
    "sections": [
        {"key": "W", "title": "Weekly · operate, coach & report", "scored": False, "check": True, "items": [
            "Monitor how teams are working patient A/R. Review A/R aging and follow-up activity by pod; confirm overdue accounts are "
            "contacted and documented; spot-check accounts; partner with Karyn when collections issues need support.",
            "Monitor RPP presentations and enrollment. Confirm eligible patients are consistently offered the Retainer Protection "
            "Program with explanation and documentation; follow up on missed presentations and enrollment opportunities.",
            "Manage time clocks and labor efficiency. Review ADP time-and-attendance edits, missed punches and supervisor corrections; "
            "compare actual clocked hours with expected staff hours; investigate early clock-ins, late clock-outs, idle time and coverage mismatches.",
            "Manage and anticipate overtime. Review overtime incurred and projected through the workweek and before payroll closes; "
            "adjust schedules, staffing or coverage with office leads; document recurring exceptions and reasons.",
            "Monitor LeadSigma lead management. Review new leads, response times, contact attempts, follow-up status and unworked or "
            "aging leads by office; coach offices with gaps.",
            "Check orthodontic scheduling and conversion trends. Review consult-to-start performance, unscheduled treatment, "
            "observation/recall follow-up, cancellations, no-shows and overdue treatment visits; focus on exceptions.",
            "Follow up on open operational issues. Review Asana/action plans and open departmental tickets; confirm owners, deadlines "
            "and visible progress; use established channels rather than duplicate escalations.",
            "Prepare and deliver the weekly OPS meeting report: work completed, meaningful findings, matters requiring leadership "
            "attention, positive changes or wins, owners, next steps and decisions or support requested."]},
        {"key": "M", "title": "Monthly · validate, reconcile & improve", "scored": False, "check": True, "items": [
            "Reconcile start paperwork history with Karyn and Haleigh; identify missing, inconsistent or overdue documentation; assign "
            "correction owners and due dates; confirm closure.",
            "Review action plans and report progress or misses against milestones: completed, delayed or missed, why, and recovery steps.",
            "Audit A/R follow-through and recurring weaknesses: sample overdue accounts to confirm outreach and documentation; compare "
            "with prior weekly observations.",
            "Evaluate RPP presentation consistency across offices; identify missed offers; confirm prior coaching improved consistency.",
            "Review labor utilization and overtime patterns for the month: avoidable labor waste, repeated time-clock exceptions and "
            "staffing adjustments needing office leadership support.",
            "Evaluate LeadSigma follow-up trends and aged/unworked leads; identify recurring gaps in response or appointment "
            "conversion; set corrective expectations with site leaders.",
            "Close the loop on office improvements from weekly OPS reports and onsite visits: confirm corrective actions, recognize "
            "sustained improvements, flag persistent or systemic barriers."]},
    ],
    "texts": [
        ("ops_done", "Weekly OPS report · Actions completed / offices supported"),
        ("ops_issues", "Weekly OPS report · Issues or barriers needing attention / decisions"),
        ("ops_wins", "Weekly OPS report · Positive changes, improvements and team wins"),
        ("ops_open", "Weekly OPS report · Open commitments, owners and next steps"),
        ("me_paperwork", "Month-end · Start paperwork review with Karyn and Haleigh / remaining gaps"),
        ("me_done", "Month-end · Completed milestones and verified improvements"),
        ("me_missed", "Month-end · Missed or delayed items / reason / recovery plan"),
        ("me_trends", "Month-end · Recurring A/R, RPP, labor, overtime or LeadSigma trends"),
        ("me_next", "Month-end · Next-month priorities / accountable owners / due dates"),
    ],
    "escalation": "",
    "escalation_note": "",
    "action_plan": False,
}

FORMS = {"onsite": ONSITE, "recurring": RECURRING}
MARKS = ("P", "A", "NA")


def item_id(form, sec, i):
    return f"{form['key']}_{sec['key']}_{i}"


def from_form(kind, form):
    """The posted checklist fields -> stored data: {marks: {id: P|A|NA|done}, notes: {id: text}, texts: {}, escalation, plan: []}."""
    f = FORMS.get(kind)
    if not f:
        return None
    marks, notes = {}, {}
    for s in f["sections"]:
        for i, _ in enumerate(s["items"]):
            k = item_id(f, s, i)
            v = str(form.get("ck_" + k) or "")
            if s.get("check"):
                if v:
                    marks[k] = "done"
            elif v in MARKS:
                marks[k] = v
            n = str(form.get("ckn_" + k) or "").strip()[:1000]
            if n:
                notes[k] = n
    texts = {k: str(form.get("ckt_" + k) or "").strip()[:4000] for k, _ in f["texts"]}
    plan = []
    if f["action_plan"]:
        cols = ("gap", "action", "goal", "owner", "due")
        lists = {c: form.getlist("ap_" + c) if hasattr(form, "getlist") else [] for c in cols}
        n = max((len(v) for v in lists.values()), default=0)
        for i in range(n):
            row = {c: str(lists[c][i] if i < len(lists[c]) else "").strip()[:500] for c in cols}
            if row["gap"] or row["action"]:
                plan.append(row)
    return {"marks": marks, "notes": notes, "texts": {k: v for k, v in texts.items() if v},
            "escalation": str(form.get("ck_escalation") or "").strip()[:2000], "plan": plan[:20]}


def score(kind, data):
    """{sections: [{key, title, answered, total, pct}], combined, answered, total, actions} for a stored checklist."""
    f = FORMS.get(kind)
    data = data or {}
    marks = data.get("marks", {})
    out, p_all, pa_all, answered, total, actions = [], 0, 0, 0, 0, 0
    for s in f["sections"]:
        ids = [item_id(f, s, i) for i in range(len(s["items"]))]
        got = [marks.get(i) for i in ids]
        ans = sum(1 for g in got if g)
        p, a = got.count("P"), got.count("A")
        actions += a
        answered += ans
        total += len(ids)
        pct = round(p / (p + a) * 100) if s["scored"] and (p + a) else None
        if s["scored"]:
            p_all, pa_all = p_all + p, pa_all + p + a
        out.append({"key": s["key"], "title": s["title"], "answered": ans, "total": len(ids), "pct": pct, "scored": s["scored"]})
    return {"sections": out, "combined": round(p_all / pa_all * 100) if pa_all else None, "answered": answered, "total": total,
            "actions": actions, "has_scores": any(s["scored"] for s in f["sections"])}


def dumps(data):
    return json.dumps(data, separators=(",", ":"))


def loads(raw):
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}
