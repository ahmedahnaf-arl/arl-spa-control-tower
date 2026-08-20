"""
Strategic Planning & Analysis — Control Tower builder.
Regenerates index.html daily from live DWH (T-1) + Google Drive (targets).

Revenue definition (LOCKED — arl-sbu-flash-report skill):
  GL 3010001 + 3010002 + 3010005 + 3010006, NET of returns (SUM(-numAmount),
  no numAmount<0 filter), SBU 0 -> ACCL(58), excluding recon entities
  (103, 116, 119, 122, 111).
"""
import os, json, calendar, datetime
from datetime import timedelta, date

import pymssql
from google.oauth2 import service_account
import google.auth.transport.requests as tr

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- DWH
# Credentials are injected via environment variables (never committed).
# Local runs:  $env:DWH_SERVER="..."; $env:DWH_USER="..."; $env:DWH_PASSWORD="..."
CONN = dict(server=os.environ.get("DWH_SERVER", ""), port=1433,
            user=os.environ.get("DWH_USER", ""),
            password=os.environ.get("DWH_PASSWORD", ""),
            database=os.environ.get("DWH_DB", "DWH"))

def q(sql):
    c = pymssql.connect(**CONN, timeout=60)
    cu = c.cursor(as_dict=True); cu.execute(sql); rows = cu.fetchall(); c.close()
    return rows

# ---------------------------------------------------------------- Google
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets.readonly"]
SHEET_ID = "1kf-2lI17nMgHx2bXAvwwx9PWboZ09qFmKP7NCR5hX3A"

def _token():
    sa_path = os.environ.get("SHEETS_SA_PATH", "C:/Users/Hp/.google/sheets-service-account.json")
    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
    r = tr.Request(); creds.refresh(r)
    return creds.token

def sheet_values(sheet, rng):
    import urllib.request, urllib.parse
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
           f"{urllib.parse.quote(sheet)}!{rng}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}"})
    return json.loads(urllib.request.urlopen(req).read().decode()).get("values", [])

# ---------------------------------------------------------------- maps
CODE = {19:"APFIL",36:"AEL",58:"ACCL",64:"ASLL",69:"ARMCL",72:"BTL_Coal",
        77:"DTL",79:"AOCN",80:"ASLL-1",81:"AMTL",82:"iBOS",83:"BPL",84:"AITL",
        86:"HRML",87:"FAL",88:"ASeLL",91:"NTL",98:"ABSL",99:"ACL",102:"AIL",
        109:"AAFL",110:"ASeLLC",114:"ALEL",115:"AAIL",118:"ATL",120:"AEFL",
        123:"AEL_Eng",124:"ABL",126:"Orca",128:"AMXL",129:"NJL",132:"AMPL"}

# target sheet name -> DWH SBU id (multiple names fold into one id)
TARGET_MAP = {"AIL":102,"ACCL":58,"AEL":36,"ARMCL":69,"Orca":126,"AAFL":109,
              "ACL":99,"ALEL":114,"NTL":91,"APFIL":19,"AITL":84,"BPL":83,
              "BTL_Coal":72,"DTL_Coal":77,"DTL_G2G":77,"AAIL":115,"AEFL":120,
              "ABL":124,"HRML":86,"Benzol":98,"ABSL_Asphalt":98}

GL = "('3010001','3010002','3010005','3010006')"
EXCL = "(103,116,119,122,111)"
T1 = "CAST(dteTransactionDate AS DATE) <= CAST(DATEADD(DAY,-1,GETDATE()) AS DATE)"

def main():
    today = date.today()
    rd = today - timedelta(days=1)                 # report date = T-1
    month_start = rd.replace(day=1)
    days_elapsed = (rd - month_start).days + 1
    days_in_month = calendar.monthrange(rd.year, rd.month)[1]
    fy_start = date(rd.year if rd.month >= 7 else rd.year - 1, 7, 1)
    mts = rd.strftime("%b-%y")                      # Target_Input key e.g. "Aug-26"

    # Step 0 — sync health
    h = q("SELECT MAX(dteLastActionDateTime) AS latest FROM fin.tblAccountingJournalArc "
          "WHERE strGeneralLedgerCode='3010001'")[0]["latest"]
    health = "OK" if h and (datetime.datetime.now() - h).total_seconds() < 24*3600 else "STALLED"

    # group revenue (net)
    g_mtd = q(f"SELECT SUM(-numAmount)/1e7 AS v FROM fin.tblAccountingJournalArc "
              f"WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL} "
              f"AND dteTransactionDate>='{month_start}' AND {T1}")[0]["v"] or 0
    g_ytd = q(f"SELECT SUM(-numAmount)/1e7 AS v FROM fin.tblAccountingJournalArc "
              f"WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL} "
              f"AND dteTransactionDate>='{fy_start}' AND {T1}")[0]["v"] or 0
    g_mkt = q(f"SELECT SUM(ABS(numAmount))/1e7 AS v FROM fin.tblAccountingJournalArc "
              f"WHERE strGeneralLedgerCode='4210001' AND numAmount>0 AND isActive=1 "
              f"AND dteTransactionDate>='{fy_start}' AND {T1}")[0]["v"] or 0

    # monthly trend (14 months)
    months = q(f"SELECT FORMAT(dteTransactionDate,'yyyy-MM') AS m, SUM(-numAmount)/1e7 AS v "
               f"FROM fin.tblAccountingJournalArc "
               f"WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL} "
               f"AND dteTransactionDate>='{fy_start.year - 1}-07-01' AND {T1} "
               f"GROUP BY FORMAT(dteTransactionDate,'yyyy-MM') ORDER BY m")
    months = [{"m": x["m"], "rev": round(float(x["v"]), 2)} for x in months]

    # daily (report month)
    days = q(f"SELECT CONVERT(varchar,dteTransactionDate,23) AS d, SUM(-numAmount)/1e7 AS v "
             f"FROM fin.tblAccountingJournalArc "
             f"WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL} "
             f"AND dteTransactionDate>='{month_start}' AND {T1} "
             f"GROUP BY dteTransactionDate ORDER BY dteTransactionDate")
    days = {x["d"]: round(float(x["v"]), 2) for x in days}

    # per-SBU revenue (net, SBU 0 -> 58)
    sbu = q(f"""
        SELECT CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END AS id,
          SUM(CASE WHEN dteTransactionDate>='{month_start}' AND {T1} THEN -numAmount/1e7 ELSE 0 END) AS mtd,
          SUM(CASE WHEN dteTransactionDate>='{fy_start}' AND {T1} THEN -numAmount/1e7 ELSE 0 END) AS ytd
        FROM fin.tblAccountingJournalArc
        WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL}
        GROUP BY CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END
    """)
    sbu = {int(x["id"]): (float(x["mtd"] or 0), float(x["ytd"] or 0)) for x in sbu}

    # targets from Target_Input for report month
    target_rows = sheet_values("Target_Input", "A1:D1000")
    tgt_by_id = {}
    for r in target_rows[1:]:
        if len(r) >= 4 and r[0] and str(r[0]).strip() == mts and r[1]:
            name = str(r[1]).strip()
            sid = TARGET_MAP.get(name)
            if sid is None:
                continue
            try:
                val = float(str(r[3]).replace(",", "").strip())
            except Exception:
                val = 0.0
            tgt_by_id[sid] = tgt_by_id.get(sid, 0.0) + val

    # assemble sbus
    sbus = []
    for sid, code in CODE.items():
        mtd, ytd = sbu.get(sid, (0.0, 0.0))
        mo_tgt = tgt_by_id.get(sid)
        mtd_tgt = round(mo_tgt * days_elapsed / days_in_month, 2) if mo_tgt else None
        ach = round(mtd / mtd_tgt * 100, 1) if mtd_tgt else None
        risk = None
        if ach is None:
            risk = "No target"
        elif ach >= 90:
            risk = "Low"
        elif ach >= 70:
            risk = "Medium"
        else:
            risk = "High"
        sbus.append({"code": code, "name": code, "mtd": round(mtd, 2),
                     "ytd": round(ytd, 2), "monthlyTarget": mo_tgt,
                     "mtdTarget": mtd_tgt, "ach": ach, "risk": risk})
    sbus.sort(key=lambda x: -(x["mtd"] or 0))

    data = {
        "meta": {
            "title": "Strategic Planning & Analysis — Control Tower",
            "owner": "Strategic Planning & Analysis",
            "org": "AKIJ Resource Ltd — CBDO Office",
            "asOf": rd.strftime("%d-%b-%Y"),
            "fy": "FY 2026-27",
            "currency": "BDT", "unit": "Crore",
            "source": "DWH fin.tblAccountingJournalArc (GL 3010001+3010002+3010005+3010006, NET) + ARL_SBU_Performance_Tracker (Target_Input) + Revenue Review minutes",
            "note": (f"Revenue NET of returns (SUM(-numAmount), no ABS). SBU 0 remapped to ACCL. "
                     f"Excludes recon entities {EXCL}. DWH sync: {health} (last post {h:%d-%b %H:%M})."),
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        "group": {
            "mtd": {"rev": round(g_mtd, 2), "label": rd.strftime("%b %Y") + " MTD"},
            "ytd": {"rev": round(g_ytd, 2), "label": "FY 26-27 YTD"},
            "mkt": {"rev": round(g_mkt, 2), "label": "Mkt Spend YTD"},
            "daysElapsed": days_elapsed, "daysInMonth": days_in_month,
        },
        "months": months,
        "days": days,
        "sbus": sbus,
        "decisions": DECISIONS,
        "macro": MACRO,
        "exec": EXEC,
        "team": TEAM,
        "strategy": STRATEGY,
        "insights": INSIGHTS,
    }

    tmpl = open(os.path.join(BASE, "_template.html"), encoding="utf-8").read()
    html = tmpl.replace("__TOWER_JSON__", json.dumps(data, ensure_ascii=False))
    out = os.path.join(BASE, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"OK  {out}  ({len(html)} chars)  report_date={rd}  sync={health}  "
          f"MTD={round(g_mtd,2)} YTD={round(g_ytd,2)} SBUs={len(sbus)}")

DECISIONS = [
 {"sbu":"Nobayon","target":"45.75","mtd":"1.14","note":"No import since Jan; NOC pending since April; local stock 27.62 Cr sellable","decision":"Escalate NOC + rate-approval loop to MD with CXOs"},
 {"sbu":"Bongo (Coal)","target":"18.71","mtd":"","note":"Coal stock nil; procurement friction from approval framework","decision":"Urgent coal LC approval"},
 {"sbu":"AEL Trading","target":"50.00","mtd":"","note":"35 Cr sold in advance; target raised from 12 Cr","decision":"On track"},
 {"sbu":"ACL F&B","target":"1.90","mtd":"0.28","note":"Seasonal honey stock; BSTI doc pending; Sept LC 1.26 Cr not approved","decision":"Sept LC 4 Cr+ needs immediate attention"},
 {"sbu":"ABSL Benzol","target":"2.60","mtd":"0.80","note":"Prime product stock near zero; war crisis delays; B2B LC delay","decision":"Shortfall on horizon"},
 {"sbu":"AEFL (Electrofab)","target":"2.29","mtd":"","note":"1.1 Cr in delayed transit (cyclone); ETA 29 Aug; no ships (war+typhoon)","decision":"Product ETA end of month"},
 {"sbu":"BPL (Bluepill)","target":"6.00","mtd":"2.00","note":"15 KAM left; receivables backlog","decision":"Revised target 2 Cr"},
 {"sbu":"AITL (Infotech)","target":"3.85","mtd":"0.19","note":"5 KAM left; balance confirmation pending","decision":"As per plan"},
 {"sbu":"AAFL","target":"103.80","mtd":"","note":"As per budget","decision":"As per plan"},
 {"sbu":"ABL (Breeder)","target":"4.00","mtd":"","note":"Chick production/rate low","decision":"Revised 2.32 Cr; commitment 2.5 Cr"},
 {"sbu":"AAIL (Tyre)","target":"4.53","mtd":"","note":"As per plan","decision":"As per plan"},
 {"sbu":"AMPL (Mediplex)","target":"0.6671","mtd":"0.03","note":"No sales team","decision":"Commitment ~50%; revised 0.30 Cr"},
 {"sbu":"AEL Manufacturing","target":"150.00","mtd":"28.58","note":"Run rate 18.51%","decision":"Revised 130 Cr"},
 {"sbu":"APFIL (Polyfiber)","target":"22.17","mtd":"","note":"As per budget","decision":"As per plan"},
 {"sbu":"ALEL","target":"17.80","mtd":"","note":"As per budget","decision":"As per plan"},
 {"sbu":"ACCL","target":"173.00","mtd":"29.87","note":"160K MT","decision":"On track"},
 {"sbu":"AIL","target":"121.00","mtd":"","note":"13,000 MT","decision":"As per plan"},
 {"sbu":"ARMCL","target":"37.91","mtd":"","note":"13.30 lac CFT","decision":"As per plan"},
]

MACRO = {
    "budget": "৳9.38L Cr", "budgetStatus": "all-time high, BNP government", "budgetDelta": "+17.7%",
    "adp": "৳3.00L Cr", "adpDelta": "+30.4%", "interest": "৳1.42L Cr",
    "revenueTarget": "৳6.50L Cr", "revenueDelta": "+15%", "taxGdp": "7.3% (target 10%)",
    "theme": "Trillion Dollar March", "inflation": "8–9%+", "inflationNote": "16-month high",
    "fx": "122.75", "fxImpact": "A +5% FX move lifts landed cost +7–9%",
}

EXEC = {
    "name": "Md. Sabbir Ahmed",
    "designation": "Strategic Planning Manager",
    "function": "Strategic Planning & Analysis",
    "department": "Marketing",
    "organization": "AKIJ Resource Ltd",
    "orgUnit": "CBDO Office",
    "reporting": "CBDO / MD",
    "location": "Dhaka, Bangladesh",
    "email": "sabbir.ahmed@akijresource.com",
    "initials": "SA",
}

TEAM = [
    {"name": "Md. Sabbir Ahmed", "designation": "Strategic Planning Manager", "role": "Function Lead",
     "responsibility": "Function strategy, target setting, performance governance, management reporting and the Control Tower.",
     "projects": ["SPA Control Tower v3.0", "FY 2026-27 Target Architecture", "Revenue Review cadence"],
     "kpi": "Group revenue achievement, report SLA, decision closure rate",
     "achievement": 88, "pending": 3, "status": "On track",
     "feedback": "Owns the function scorecard and escalation cadence. Priority: unblock the Nobayon NOC / rate-approval loop and lock September LC pipeline before the next revenue review."},
    {"name": "Ahmed Ahnaf", "designation": "Manager, Marketing P&L Analyst", "role": "Marketing P&L / BI",
     "responsibility": "Marketing P&L analysis, campaign & ROMI tracking, SBU-level revenue/expense consolidation and cross-functional gap analyses.",
     "projects": ["Marketing Campaign Command Center", "SBU Sales Flash", "Cross-functional gap analyses"],
     "kpi": "Flash/report SLA, ROMI tracking coverage, data accuracy",
     "achievement": 92, "pending": 2, "status": "On track",
     "feedback": "Strongest SLA discipline on the team. Keep the flash report and campaign command centre fresh; escalate ACL F&B and Coal LC blockers to keep September targets visible."},
    {"name": "MD. Abir Ul Islam", "designation": "Sr. Officer", "role": "Strategic Planning & Analysis",
     "responsibility": "To be configured — populate from Drive / MCP source.",
     "projects": ["To be configured"],
     "kpi": "To be configured",
     "achievement": None, "pending": None, "status": "Active",
     "feedback": "Designation confirmed (Sr. Officer). Awaiting responsibility & KPI data to enable scoring."},
    {"name": "Zobaeer Mahmood", "designation": "Sr. Officer", "role": "Strategic Planning & Analysis",
     "responsibility": "To be configured — populate from Drive / MCP source.",
     "projects": ["To be configured"],
     "kpi": "To be configured",
     "achievement": None, "pending": None, "status": "Active",
     "feedback": "Designation confirmed (Sr. Officer). Awaiting responsibility & KPI data to enable scoring."},
]

STRATEGY = [
    {"pillar": "Performance Governance",
     "objective": "Single source of truth for group revenue (daily / MTD / YTD vs target)",
     "initiatives": [
         {"name": "Daily SBU Sales Flash (T-1)", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 95, "status": "On track"},
         {"name": "Monthly Performance Snapshot", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 90, "status": "On track"},
         {"name": "Revenue Review Meeting cadence", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 85, "status": "On track"},
     ]},
    {"pillar": "Marketing P&L Intelligence",
     "objective": "Consolidated marketing spend, budget vs actual and ROMI across SBUs",
     "initiatives": [
         {"name": "Marketing Campaign Command Center", "owner": "Ahmed Ahnaf", "timeline": "FY26-27", "completion": 90, "status": "On track"},
         {"name": "ATL/BTL budget & PO/GRN/SRN tracking", "owner": "Ahmed Ahnaf", "timeline": "FY26-27", "completion": 85, "status": "On track"},
     ]},
    {"pillar": "Portfolio & Gap Analysis",
     "objective": "Cross-functional demand-supply and SBU diagnostic capability",
     "initiatives": [
         {"name": "SBU gap analysis / post-mortems", "owner": "Ahmed Ahnaf", "timeline": "FY26-27", "completion": 75, "status": "In progress"},
         {"name": "Demand vs supply gap diagnosis", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 70, "status": "In progress"},
     ]},
    {"pillar": "AI Command Center",
     "objective": "AI-driven control tower, insight engine and visitor intelligence",
     "initiatives": [
         {"name": "Control Tower v3.0", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 80, "status": "In progress"},
         {"name": "AI Insight Engine", "owner": "Sabbir Ahmed", "timeline": "FY26-27", "completion": 70, "status": "In progress"},
     ]},
]

INSIGHTS = [
    {"what": "Nobayon import remains frozen — no import since January and NOC still pending since April.",
     "why": "Government NOC approval loop and a rate-approval model that raises false flags are blocking procurement.",
     "impact": "August target of 45.75 Cr (imported 18.13 Cr not in hand) is at risk; only local stock (27.62 Cr sellable) can move.",
     "action": "Escalate the NOC + rate-approval loop to the MD with each CXO; fast-track the running 30 Cr deal.",
     "owner": "Sabbir Ahmed / CXOs", "timeline": "This week", "sev": "high"},
    {"what": "Bongo coal target 18.71 Cr with stock at nil.",
     "why": "Procurement friction from the approval framework; no LC opened yet.",
     "impact": "Next-month coal sales require immediate import booking — any delay breaks the revenue line.",
     "action": "Urgent coal LC approval, escalated to MD.",
     "owner": "Procurement + MD", "timeline": "Immediate", "sev": "high"},
    {"what": "ACL F&B September target (3+ Cr) has no LC approved yet (1.26 Cr open; 4 Cr+ needed).",
     "why": "LC approval process is delayed; BSTI doc pending for port stock.",
     "impact": "September target will be short if LCs are not approved now.",
     "action": "Fast-track September LC pipeline and push receivables collection.",
     "owner": "ACL SCM / Ahmed Ahnaf (tracking)", "timeline": "Before month-end", "sev": "high"},
    {"what": "AEFL (Electrofab) revenue suppressed — 1.1 Cr in delayed transit, no ships (war + typhoon).",
     "why": "Supplier-side shipment disruption; no local procurement margin.",
     "impact": "August target 2.29 Cr unlikely; product ETA only 29 Aug.",
     "action": "Track shipment ETA daily and pre-plan September delivery.",
     "owner": "AEFL SCM", "timeline": "Monitor daily", "sev": "medium"},
    {"what": "AEL Manufacturing run rate at 18.51% of the 150 Cr target.",
     "why": "Q1 ramp-up lag across consumer/rice/export/tender segments.",
     "impact": "Full-year target revised to 130 Cr; Q1 gap must be filled by year-end.",
     "action": "Monthly variance review; confirm revised 130 Cr commitment is tracked.",
     "owner": "Sabbir Ahmed", "timeline": "Monthly", "sev": "medium"},
    {"what": "Group MTD revenue tracking on plan; high-risk SBUs concentrated in import-dependent lines.",
     "why": "War-related inflation, LC delays and NOC gaps are the common root cause.",
     "impact": "Portfolio risk is clustered — resolution unlocks multiple SBUs at once.",
     "action": "Treat NOC + LC approval as a single group-level blocker and track to closure weekly.",
     "owner": "Strategic Planning & Analysis", "timeline": "Weekly", "sev": "medium"},
]

if __name__ == "__main__":
    main()
