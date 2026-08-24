"""
Strategic Planning & Analysis — Control Tower builder.
Regenerates index.html daily from live DWH (T-1) + Google Drive (targets).

Revenue definition (LOCKED — arl-sbu-flash-report skill):
  GL 3010001 + 3010002 + 3010005 + 3010006, NET of returns (SUM(-numAmount),
  no numAmount<0 filter), SBU 0 -> ACCL(58), excluding recon entities
  (103, 116, 119, 122, 111).
"""
import os, json, calendar, datetime
import urllib.request
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

    reports = build_reports(rd, g_mtd, g_ytd, months, days, sbus, days_in_month)

    five_year = build_strategy_detail(sbus, target_rows, g_ytd)

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
        "systems": check_systems(),
        "reports": reports,
        "five_year": five_year,
        "five_year_tabs": FIVE_YEAR_TABS,
        "mcp_tools": MCP_TOOLS,
        "mcp_intel": build_mcp_intelligence(sbus),
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

SYSTEMS = [
    {"key": "pms", "name": "AKIJ PMS", "label": "Procurement Management",
     "type": "Remote MCP", "endpoint": "https://pms-mcp.vercel.app/api/mcp",
     "tools": ["Sourcing", "Draft RFQ", "Comparative statement", "Price triggers",
               "Material intelligence", "Freight intelligence", "Materials to order"],
     "status": None},
    {"key": "ims", "name": "AKIJ IMS", "label": "Inventory Management (DWH wms)",
     "type": "Local MCP", "endpoint": "mcp-servers/akij-ims/server.py -> DWH wms schema",
     "tools": ["Plants", "Inventory summary", "Stock movements", "Gate passes"],
     "status": None},
    {"key": "finance", "name": "AKIJ Finance", "label": "Financial Statements",
     "type": "Remote MCP", "endpoint": "https://akij-finance-app.vercel.app/api/mcp",
     "tools": ["Income statement", "Balance sheet", "Cash flow", "Working capital",
               "Coal LC & projection", "Profit centers"],
     "status": None},
]

def check_systems():
    import urllib.request, json
    from urllib.error import HTTPError
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "spa-tower", "version": "1.0"}}})
    for s in SYSTEMS:
        if s["key"] == "ims":
            p = os.environ.get("IMS_MCP_PATH",
                               r"C:\Users\Hp\Documents\Default Project\mcp-servers\akij-ims\server.py")
            s["status"] = "Online" if os.path.exists(p) else "Not installed"
            continue
        try:
            req = urllib.request.Request(s["endpoint"], data=init.encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Accept": "application/json, text/event-stream"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=25)
            s["status"] = "Online"
        except HTTPError:
            s["status"] = "Online"      # server responded (even 4xx/5xx) = reachable
        except Exception:
            s["status"] = "Offline"
    return SYSTEMS

def build_reports(rd, g_mtd, g_ytd, months, days, sbus, days_in_month):
    """Reporting System: Daily / Weekly / Monthly / Yearly strategic reports
    generated from live DWH + targets. See _template.html 'reports' tab."""
    group_mo_tgt = sum((s["monthlyTarget"] or 0) for s in sbus)
    group_mtd_tgt = sum((s["mtdTarget"] or 0) for s in sbus)
    ach = (g_mtd / group_mtd_tgt * 100) if group_mtd_tgt else None
    high = [s for s in sbus if s["risk"] == "High"]
    med = [s for s in sbus if s["risk"] == "Medium"]
    low = [s for s in sbus if s["risk"] == "Low"]

    day_keys = sorted(days.keys())
    last = day_keys[-1] if day_keys else None
    last_rev = days.get(last, 0) if last else 0
    prev = day_keys[-2] if len(day_keys) > 1 else None
    prev_rev = days.get(prev, 0) if prev else 0
    dod = ((last_rev - prev_rev) / prev_rev * 100) if prev_rev else None

    last7 = day_keys[-7:]
    weekly_actual = sum(days[k] for k in last7)
    weekly_tgt = (group_mo_tgt * 7 / days_in_month) if group_mo_tgt else None
    weekly_ach = (weekly_actual / weekly_tgt * 100) if weekly_tgt else None

    fy25 = sum(m["rev"] for m in months if "2025-07" <= m["m"] <= "2026-06")
    strat_vals = [i["completion"] for p in STRATEGY for i in p["initiatives"]]
    strat_avg = (sum(strat_vals) / len(strat_vals)) if strat_vals else 0
    gap = (g_mtd - group_mtd_tgt) if group_mtd_tgt else None

    def cr(v): return round(v, 2)
    def fmt_ach(s): return (f"{s['ach']:.0f}% ach" if s["ach"] is not None else "no target")

    return [
        {"id": "daily", "title": "Daily Strategic Report",
         "period": "T-1 · " + rd.strftime("%d %b %Y"),
         "kpis": [
             {"l": "Daily Achievement", "v": f"{cr(last_rev)} Cr",
              "note": f"{dod:+.1f}% DoD" if dod is not None else "single day"},
             {"l": "MTD Achievement", "v": f"{ach:.1f}%" if ach is not None else "—",
              "note": "vs paced target"},
             {"l": "Gap", "v": f"{gap:+.2f} Cr" if gap is not None else "—",
              "note": "MTD vs paced target"},
             {"l": "High-Risk SBUs", "v": str(len(high)), "note": f"of {len(sbus)} tracked"},
         ],
         "blocks": [
             {"heading": "Risk",
              "items": [f"{s['code']} — {fmt_ach(s)}" for s in high[:8]] or ["No high-risk SBUs"]},
             {"heading": "Required Action",
              "items": [i["action"] for i in INSIGHTS if i["sev"] == "high"][:5] or ["No critical actions"]},
         ]},
        {"id": "weekly", "title": "Weekly Strategic Report",
         "period": f"Last {len(last7)} days",
         "kpis": [
             {"l": "Week Actual", "v": f"{cr(weekly_actual)} Cr", "note": "sum last 7 days"},
             {"l": "Week Target", "v": f"{cr(weekly_tgt)} Cr" if weekly_tgt else "—",
              "note": "monthly / days prorated"},
             {"l": "Week Achievement", "v": f"{weekly_ach:.1f}%" if weekly_ach is not None else "—",
              "note": "actual vs target"},
             {"l": "Latest DoD", "v": f"{dod:+.1f}%" if dod is not None else "—",
              "note": "day-over-day"},
         ],
         "blocks": [
             {"heading": "Performance Trend (last 7 days)",
              "items": [f"{k[8:10]}-{k[5:7]}: {days[k]:.2f} Cr" for k in last7]},
             {"heading": "Risk Forecast",
              "items": [f"{s['code']} — {s['risk']}" for s in (high + med)[:10]] or ["Low risk outlook"]},
             {"heading": "Recovery Plan",
              "items": [i["action"] for i in INSIGHTS if i["sev"] in ("high", "medium")][:6] or ["No recovery actions"]},
         ]},
        {"id": "monthly", "title": "Monthly Strategic Report",
         "period": rd.strftime("%b %Y"),
         "kpis": [
             {"l": "KPI Achievement", "v": f"{ach:.1f}%" if ach is not None else "—",
              "note": "group MTD vs paced"},
             {"l": "MTD Revenue", "v": f"{cr(g_mtd)} Cr", "note": "group (net)"},
             {"l": "On Track", "v": str(len(low)), "note": "SBUs ≥ 90% ach"},
             {"l": "At Risk", "v": str(len(high)), "note": "SBUs < 70% ach"},
         ],
         "blocks": [
             {"heading": "Achievement Status",
              "items": [f"Low risk: {len(low)} · Medium: {len(med)} · High: {len(high)} SBUs",
                        f"Group achievement {ach:.1f}% of paced target" if ach is not None else "No targets set"]},
             {"heading": "Corrective Action",
              "items": [f"{d['sbu']}: {d['decision']}" for d in DECISIONS][:8] or ["No decisions"]},
         ]},
        {"id": "yearly", "title": "Yearly Strategic Review",
         "period": "FY 2026-27",
         "kpis": [
             {"l": "FY YTD Revenue", "v": f"{cr(g_ytd)} Cr", "note": "Jul 1 → T-1"},
             {"l": "FY25 Revenue", "v": f"{cr(fy25)} Cr", "note": "prior full year"},
             {"l": "Strategic Execution", "v": f"{strat_avg:.0f}%", "note": "initiative completion"},
             {"l": "Strategic Pillars", "v": str(len(STRATEGY)), "note": "active pillars"},
         ],
         "blocks": [
             {"heading": "Annual Achievement",
              "items": [f"FY 26-27 YTD {cr(g_ytd)} Cr vs FY25 full-year {cr(fy25)} Cr"]},
             {"heading": "Strategic Milestones",
              "items": [f"{p['pillar']} — {sum(i['completion'] for i in p['initiatives'])/len(p['initiatives']):.0f}%" for p in STRATEGY]},
             {"heading": "5-Year Progress",
              "items": ["FY25 (14-mo history) baseline established; multi-year trajectory now tracked against strategic pillars.",
                        f"Strategic execution at {strat_avg:.0f}% — the leading indicator for 5-year ambition"]},
         ]},
    ]


FIVE_YEAR_TABS = ["Dashboard", "QSA", "SWOT+TOWS", "Marketing Led", "PESTEL", "Porter's 5",
                  "CPM", "Product BCG", "SJA+SOAR", "VRIO", "CVP+BMC", "Contradictions",
                  "FY25-26 GAP", "Business Plan", "BSC+GAP", "5-Year Plan", "Way Forward",
                  "Execution Plan", "Risk & Mitigation", "Playbook"]

# SBU -> 5-year strategy drive folder (built incrementally under "AR Strategy 05 Years")
STRATEGY_DOCS = {
    "AAFL": "https://drive.google.com/drive/folders/1wUImfOEgJahcBEzvV3nhgkTBMfT-9nlO",
    "AAIL": "https://drive.google.com/drive/folders/12WQ16E4S7e1eiyVzr90oC4f2iisGLrxP",
    "ACCL": "https://drive.google.com/drive/folders/1nA-K0ymRhl0k_ZSqqmWsiadcZraVI5ca",
    "AEL":  "https://drive.google.com/drive/folders/1I-Q-gB5JvT_txGxIR_35zVOVzd_n7roh",
}


CODE_REV = {v: k for k, v in CODE.items()}
MONTH_MAP = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
             "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}


def norm_month(s):
    s = str(s).strip()
    if "-" not in s:
        return s
    mmm, yy = s.split("-", 1)
    y = int(yy.strip())
    if y < 100:
        y += 2000
    return "%d-%s" % (y, MONTH_MAP.get(mmm.strip()[:3].upper(), "00"))


def activity_bucket(subgl):
    s = (subgl or "").strip().lower()
    if "g2g" in s:
        return "Sales G2G"
    if "foreign" in s:
        return "Sales Foreign"
    if "freight" in s or "charter" in s or "demurrage" in s:
        return "Freight & Shipping"
    if "agency" in s or "commission" in s:
        return "Agency Commission"
    if "subsidy" in s or "grant" in s:
        return "Subsidy"
    if "flight" in s or "holiday" in s or "visa" in s or "management fee" in s:
        return "Services"
    if "local" in s:
        return "Sales Local"
    return "Other"


def build_strategy_detail(sbus, target_rows, group_ytd):
    """Per-SBU deep strategy: monthly target-vs-actual gaps, activity-wise revenue,
    BCG quadrant, action plans + owners."""
    rows = q(f"""
        SELECT CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END AS sbu,
          FORMAT(dteTransactionDate,'yyyy-MM') AS m, SUM(-numAmount)/1e7 AS v
        FROM fin.tblAccountingJournalArc
        WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL}
          AND dteTransactionDate>='{fy_start_prev()}-07-01' AND {T1}
        GROUP BY CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END, FORMAT(dteTransactionDate,'yyyy-MM')
    """)
    sbu_monthly = {}
    for r in rows:
        sbu_monthly.setdefault(int(r["sbu"]), {})[r["m"]] = round(float(r["v"] or 0), 2)

    sbu_targets = {}
    for r in target_rows[1:]:
        if len(r) >= 4 and r[0] and r[1]:
            m = norm_month(r[0])
            sid = TARGET_MAP.get(str(r[1]).strip())
            if sid is None:
                continue
            try:
                val = float(str(r[3]).replace(",", "").strip())
            except Exception:
                val = 0.0
            sbu_targets.setdefault(m, {}).setdefault(sid, 0.0)
            sbu_targets[m][sid] += val

    rows2 = q(f"""
        SELECT CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END AS sbu, strSubGLName,
          SUM(-numAmount)/1e7 AS v
        FROM fin.tblAccountingJournalArc
        WHERE strGeneralLedgerCode IN {GL} AND isActive=1 AND intSBUId NOT IN {EXCL}
          AND dteTransactionDate>='{fy_start_prev()}-07-01' AND {T1}
        GROUP BY CASE WHEN intSBUId=0 THEN 58 ELSE intSBUId END, strSubGLName
    """)
    sbu_activity = {}
    for r in rows2:
        b = activity_bucket(r["strSubGLName"])
        sbu_activity.setdefault(int(r["sbu"]), {}).setdefault(b, 0.0)
        sbu_activity[int(r["sbu"])][b] += float(r["v"] or 0)

    today = date.today()
    fy = today.year if today.month >= 7 else today.year - 1
    cur_m = ["%d-07" % fy, "%d-08" % fy, "%d-09" % fy, "%d-10" % fy, "%d-11" % fy, "%d-12" % fy,
             "%d-01" % (fy + 1), "%d-02" % (fy + 1), "%d-03" % (fy + 1), "%d-04" % (fy + 1),
             "%d-05" % (fy + 1), "%d-06" % (fy + 1)]
    prev_m = ["%d-07" % (fy - 1), "%d-08" % (fy - 1), "%d-09" % (fy - 1), "%d-10" % (fy - 1),
              "%d-11" % (fy - 1), "%d-12" % (fy - 1), "%d-01" % fy, "%d-02" % fy, "%d-03" % fy,
              "%d-04" % fy, "%d-05" % fy, "%d-06" % fy]
    med_share = 1.0 / len(sbus)

    out = []
    for s in sbus:
        code = s["code"]
        sid = CODE_REV.get(code)
        # monthly gap rows (last 14 months)
        all_m = sorted(set(sbu_targets.keys()) | set(sbu_monthly.get(sid, {}).keys()))
        monthly = []
        for m in all_m[-14:]:
            tgt = sbu_targets.get(m, {}).get(sid)
            act = sbu_monthly.get(sid, {}).get(m)
            gap = round(act - tgt, 2) if (act is not None and tgt) else None
            ach = round(act / tgt * 100, 1) if (act is not None and tgt) else None
            monthly.append({"m": m, "t": tgt, "a": act, "gap": gap, "ach": ach})
        # activity-wise
        act_map = sbu_activity.get(sid, {})
        activities = [{"k": k, "v": round(v, 2)} for k, v in act_map.items()]
        activities.sort(key=lambda x: -x["v"])
        activities = activities[:6]
        # BCG
        share = (s["ytd"] / group_ytd * 100) if group_ytd else 0
        cur = sum(sbu_monthly.get(sid, {}).get(m, 0) for m in cur_m)
        prev = sum(sbu_monthly.get(sid, {}).get(m, 0) for m in prev_m)
        growth = round((cur - prev) / prev * 100, 1) if prev else None
        hi_share = share >= med_share * 100
        hi_growth = growth is not None and growth > 0
        if hi_growth and hi_share:
            quad = "Star"
        elif not hi_growth and hi_share:
            quad = "Cash Cow"
        elif hi_growth and not hi_share:
            quad = "Question Mark"
        else:
            quad = "Dog"
        # actions + owners
        actions = []
        for d in DECISIONS:
            if code.lower() in d["sbu"].lower() or d["sbu"].lower().startswith(code.lower()):
                actions.append({"what": d["decision"], "owner": "CXO / MD", "note": d["note"]})
        for i in INSIGHTS:
            if code.lower() in (i["what"] + " " + i["owner"]).lower():
                actions.append({"what": i["action"], "owner": i["owner"], "note": i["impact"]})
        if not actions:
            actions.append({"what": "Monitor monthly achievement; escalate if below 90% of target.",
                            "owner": "SBU Head", "note": "Auto-generated"})
        doc = STRATEGY_DOCS.get(code)
        base_val = round(s["monthlyTarget"] * 12, 2) if s["monthlyTarget"] else round((s["ytd"] or 0) * 2, 2)
        ga = 0.10
        proj = {}
        pv = base_val
        for yr in range(27, 32):
            proj["FY%d" % yr] = round(pv, 2)
            pv *= (1 + ga)
        entry = {
            "code": code, "base": base_val, "projection": proj, "cagr": ga * 100,
            "monthly": monthly, "activities": activities,
            "share": round(share, 2), "growth": growth, "quadrant": quad,
            "actions": actions[:6], "doc": doc,
            "status": "Documented" if doc else "In progress",
            "mtd": s["mtd"], "ytd": s["ytd"], "monthlyTarget": s["monthlyTarget"],
            "ach": s["ach"], "risk": s["risk"],
        }
        entry["tabs20"] = build_20tab(entry)
        out.append(entry)
    return out


def fy_start_prev():
    y = date.today().year if date.today().month >= 7 else date.today().year - 1
    return y - 1


def _blk(heading, items):
    return {"heading": heading, "items": [str(i) for i in items if i]}


def build_20tab(d):
    """Generate the complete 20-tab ACCL narrative for one SBU — detailed, data-informed from DWH."""
    code = d["code"]; ach = d["ach"]; risk = d["risk"] or "—"
    growth = d["growth"]; share = d["share"]; quad = d["quadrant"]
    mtd = d["mtd"]; ytd = d["ytd"]; mtgt = d["monthlyTarget"]
    acts = d["activities"]; mon = d["monthly"]; actions = d["actions"]
    base = d["base"]; proj = d["projection"]; doc = d["doc"]

    def cr(v): return ("—" if v is None else round(v, 2))
    def gs():
        if growth is None:
            return "—"
        return ("+" if growth >= 0 else "") + str(growth) + "%"

    def _blk(heading, items):
        return {"heading": heading, "items": [str(i) for i in items if i]}

    def _tbl(heading, headers, rows):
        return {"heading": heading, "table": {"headers": [str(h) for h in headers],
                                              "rows": [[str(c) for c in r] for r in rows]}}

    # ---- derived ----
    under = [x for x in mon if x["gap"] is not None and x["gap"] < 0]
    over = [x for x in mon if x["gap"] is not None and x["gap"] >= 0]
    total_gap = sum(x["gap"] for x in mon if x["gap"] is not None)
    ach_vals = [x["ach"] for x in mon if x["ach"] is not None]
    avg_ach = (sum(ach_vals) / len(ach_vals)) if ach_vals else None
    total_act = sum(a["v"] for a in acts) or 1.0
    fy31 = proj.get("FY31", 0)
    cagr = d["cagr"]

    # ---- SWOT ----
    S, W, O, T = [], [], [], []
    if growth is not None and growth > 0: S.append(f"Revenue growing {gs()} YoY")
    if share >= 3: S.append(f"{share}% group portfolio share — a material revenue contributor")
    if ach is not None and ach >= 90: S.append(f"On or above target at {ach}% achievement")
    if ach is not None and ach < 90: W.append(f"Below paced target at {ach}% achievement")
    if risk == "High": W.append("Classified high risk (achievement < 70% of paced target)")
    if growth is not None and growth < 0: W.append(f"Revenue contracting {gs()} YoY")
    if under: W.append(f"{len(under)} of last {len(mon)} months under target")
    O.append(f"5-year trajectory to {cr(fy31)} Cr at {cagr:.0f}% CAGR")
    O.append("Close monthly gaps through execution of the action plans below")
    O.append("Scale the highest-revenue activity stream(s)")
    T.append(f"Target-gap risk — current classification {risk}")
    T.append("Macro headwinds: inflation 8–9%+, USD/BDT 122.75 (import-cost pressure)")
    if growth is not None and growth < 0: T.append("Structural revenue decline if not reversed")

    # ---- activity BCG ----
    act_bcg_rows = []
    for a in acts:
        sa = a["v"] / total_act * 100
        if growth is not None and growth > 0 and sa >= 50: q = "Star"
        elif (growth is None or growth <= 0) and sa >= 50: q = "Cash Cow"
        elif growth is not None and growth > 0 and sa < 50: q = "Question Mark"
        else: q = "Dog"
        act_bcg_rows.append([a["k"], f"{cr(a['v'])} Cr", f"{sa:.0f}%", q])

    tabs = [
      {"id": "dashboard", "title": "Dashboard", "blocks": [
        _tbl("Key Performance Snapshot", ["Metric", "Value"],
             [["MTD Revenue (net)", f"{cr(mtd)} Cr"],
              ["YTD Revenue (net)", f"{cr(ytd)} Cr"],
              ["Monthly Target", f"{cr(mtgt)} Cr"],
              ["Achievement (vs paced)", f"{ach if ach is not None else '—'}%"],
              ["Portfolio Share", f"{share}%"],
              ["YoY Growth", gs()],
              ["BCG Quadrant", quad]]),
        _tbl("5-Year Revenue Projection (STRATEGIC PROJECTION)", ["Year", "Revenue (Cr)"],
             [[f"FY{yr}", cr(proj[f"FY{yr}"])] for yr in range(27, 32)] + [["CAGR", f"{cagr:.0f}%"]]),
        _blk("Projection basis", [f"Base year FY27 = {cr(base)} Cr (annualized monthly target / YTD run-rate).",
                                  f"Applied a flat {cagr:.0f}% YoY growth assumption — a strategic projection, not an approved target."]),
      ]},
      {"id": "qsa", "title": "QSA", "blocks": [
        _blk("Quick Situation Analysis", [
            f"Business stage: {'high-risk / turnaround' if risk == 'High' else ('growing' if (growth or 0) > 0 else 'stable')}",
            f"Group footprint: {share}% of total group revenue",
            f"Revenue streams: {', '.join(a['k'] for a in acts[:4]) or '—'}",
            f"Latest achievement: {ach if ach is not None else '—'}% of paced target",
            f"Average achievement (last {len(mon)} months): {('%.1f%%' % avg_ach) if avg_ach is not None else '—'}"]),
        _blk("Strengths", S or ["None derived from DWH — see strategy document"]),
        _blk("Weaknesses / Blocks", W or ["None derived from DWH"]),
        _blk("Future Opportunity", O),
      ]},
      {"id": "swot", "title": "SWOT+TOWS", "blocks": [
        _blk("Strengths", S or ["Data gap — see strategy document"]),
        _blk("Weaknesses", W or ["None derived from DWH"]),
        _blk("Opportunities", O),
        _blk("Threats", T),
        _blk("TOWS Strategies (derived from the SWOT)", [
            "SO — leverage growth + share to scale the leading revenue stream",
            "WO — execute gap-closing actions to lift achievement above 90%",
            "ST — hedge FX/import exposure on import-dependent lines",
            "WT — run recovery plans on high-risk revenue lines to avoid further decline"]),
      ]},
      {"id": "marketing", "title": "Marketing Led", "blocks": [
        _blk("Market Position & Gaps", [
            f"Current position: {share}% group share, {gs()} growth",
            "Marketing gap: spend is tracked at group level (GL 4210001) — SBU-level budget requires the campaign command center",
            "Brand/customer gap: data gap — see strategy document",
            "Channel gap: data gap — see route-to-market / sales analysis"]),
        _blk("Marketing-led Revenue Opportunity", [
            f"Revenue base {cr(base)} Cr → FY31 {cr(fy31)} Cr implies sustained demand generation",
            "5-year drivers: brand growth, channel expansion, campaign ROI (ROMI)",
            "Roadmap: align campaign/activation calendar to close the monthly target gap",
            "ROMI: data gap — see Marketing Campaign Command Center"]),
      ]},
      {"id": "pestel", "title": "PESTEL", "blocks": [
        _blk("Political", ["BNP government; 'Trillion Dollar March' growth theme",
                           "Import/NOC/LC approval loops are recurring operational blockers"]),
        _blk("Economic", ["FY26-27 budget ৳9.38L Cr (+17.7%); ADP ৳3.00L Cr (+30.4%)",
                          "Inflation 8–9%+ (16-month high) — pricing & margin pressure",
                          "FX USD/BDT 122.75 — import cost + landed-cost risk"]),
        _blk("Social", ["Consumer demand shifts tracked via monthly SBU sales data",
                        "Demographic growth supports long-run volume"]),
        _blk("Technological", ["DWH/MCP-driven real-time performance monitoring",
                               "AI-assisted strategic reporting (this control tower)"]),
        _blk("Environmental", ["Sustainability/compliance requirements per SBU (data gap)"]),
        _blk("Legal", ["NOC approval (e.g. Nobayon), LC approval, and rate-approval friction",
                       "Government subsidy schemes where applicable"]),
      ]},
      {"id": "porter", "title": "Porter's 5", "blocks": [
        _blk("Supplier Power", ["Import-dependent lines exposed to FX and LC delays (elevated supplier power)",
                                "Local sourcing where margin is thin (see revenue review minutes)"]),
        _blk("Buyer Power", ["B2B/G2G concentration raises buyer power in wholesale lines",
                             "Consumer lines: brand + distribution lowers buyer power"]),
        _blk("Threat of New Entrants", ["Moderate — capital/capex and distribution are entry barriers"]),
        _blk("Threat of Substitutes", ["Commodity/price sensitivity — substitute risk in undifferentiated lines"]),
        _blk("Competitive Rivalry", [f"Portfolio position: {quad} ({share}% share, {gs()} growth)"]),
      ]},
      {"id": "cpm", "title": "CPM", "blocks": [
        _blk("Competitive Profile Matrix", [
            "Critical Success Factors, weights and competitor scores live in the SBU strategy document (data gap).",
            "The framework compares: cost, quality, distribution, brand, and customer relationships."]),
        _tbl("SBU Relative Position (DWH-derived)", ["Dimension", "Value"],
             [["Portfolio share", f"{share}%"],
              ["YoY growth", gs()],
              ["Achievement vs target", f"{ach if ach is not None else '—'}%"],
              ["BCG quadrant", quad]]),
        _blk("Note", ["Weights must sum to 1.00; competitor names/scores are sourced from the strategy doc, never copied from other SBUs."]),
      ]},
      {"id": "bcg", "title": "Product BCG", "blocks": [
        _tbl("Activity / Revenue-stream BCG (growth vs share proxy)", ["Activity", "Revenue", "Share", "Quadrant"],
             act_bcg_rows),
        _blk("Portfolio logic", [
            "Star = high growth + high share → invest",
            "Cash Cow = low growth + high share → harvest & fund growth",
            "Question Mark = high growth + low share → selective investment",
            "Dog = low growth + low share → turnaround or exit"]),
      ]},
      {"id": "sja", "title": "SJA+SOAR", "blocks": [
        _blk("Strategic Juncture Analysis", [
            f"Quadrant: {quad} — {'scale aggressively' if quad in ('Star', 'Question Mark') else ('defend & harvest' if quad == 'Cash Cow' else 'turnaround/exit')}",
            "Opportunity window: FY27 foundation → FY31 leadership (5-year runway)",
            "Bold risk: failing to close the monthly target gap erodes the 5-year trajectory"]),
        _blk("SOAR", [
            f"Strengths → {', '.join(S[:2]) if S else 'build on current position'}",
            f"Opportunities → capture FY31 {cr(fy31)} Cr ambition",
            f"Aspirations → sustain {cagr:.0f}% CAGR and close gaps",
            f"Results → measured via the monthly gap table (target vs actual)"]),
      ]},
      {"id": "vrio", "title": "VRIO", "blocks": [
        _blk("Resource & Capability Assessment", [
            "Data/DWH access — Valuable + Organized (competitive parity)",
            "SBU-specific resources/capabilities — data gap, see strategy document",
            "Prioritize underleveraged competitive advantages identified in the doc"]),
        _tbl("VRIO framework", ["Resource", "V", "R", "I", "O", "Implication"],
             [["DWH/MCP performance data", "Y", "N", "N", "Y", "Parity"],
              ["SBU-specific assets", "?", "?", "?", "?", "Data gap"]]),
      ]},
      {"id": "cvp", "title": "CVP+BMC", "blocks": [
        _blk("Customer Value Proposition", [
            f"Segments: {', '.join(a['k'] for a in acts[:3]) or '—'}",
            "Customer jobs/needs: reliable supply, competitive pricing, consistent quality",
            "SBU solution: on-time delivery + service via existing network",
            "Customer benefit: lower risk / better value",
            "Company benefit: recurring revenue + margin"]),
        _blk("Business Model Canvas", [
            "Key partners: suppliers, distributors, logistics (data gap for specifics)",
            "Key activities: sourcing, sales, delivery, collections",
            "Key resources: people, working capital, DWH/data",
            "Value proposition: reliable supply + competitive pricing",
            "Customer relationships: account management (B2B) / brand (B2C)",
            "Channels: direct + dealer/distributor network",
            f"Customer segments: {', '.join(a['k'] for a in acts[:3]) or '—'}",
            "Cost structure: COGS + import/FX + operating + financing",
            f"Revenue streams: {', '.join(a['k'] for a in acts[:4]) or '—'}"]),
      ]},
      {"id": "contradictions", "title": "Contradictions", "blocks": [
        _blk("Internal Contradictions / Barriers", [
            f"Growth ambition (FY31 {cr(fy31)} Cr) vs current achievement ({ach if ach is not None else '—'}%)",
            f"Monthly target {cr(mtgt)} Cr vs MTD actual {cr(mtd)} Cr",
            "Import-driven growth vs FX/LC/NOC constraints",
            "Strategy vs execution capability — bridged via the action plan",
            "Portfolio share ambition vs working-capital constraints (where applicable)"]),
        _blk("Benchmark", ["Compare against the group BCG matrix to validate the contradiction is not systemic"]),
      ]},
      {"id": "gap", "title": "FY25-26 GAP", "blocks": [
        _tbl("Monthly Target vs Achievement Gap", ["Month", "Target (Cr)", "Actual (Cr)", "Gap (Cr)", "Ach %"],
             [[x["m"], cr(x["t"]), cr(x["a"]), (("+" if x["gap"] >= 0 else "") + str(cr(x["gap"]))), (x["ach"] if x["ach"] is not None else "—")]
              for x in mon if x["gap"] is not None]),
        _blk("Gap Summary", [
            f"Months under target: {len(under)} · Months on/above: {len(over)}",
            f"Cumulative gap: {'+' if total_gap >= 0 else ''}{cr(total_gap)} Cr",
            f"Average achievement: {('%.1f%%' % avg_ach) if avg_ach is not None else '—'}"]),
        _blk("Root Cause & Corrective Action", [
            "Root cause: recorded in revenue-review minutes (import/NOC/LC, demand, or execution).",
            "Corrective action: gap-closing initiatives in the Way Forward / Execution Plan."]),
      ]},
      {"id": "bplan", "title": "Business Plan", "blocks": [
        _blk("Next-Year Operating Plan (FY27)", [
            f"Revenue target (annualized): {cr(base)} Cr",
            f"5-year ambition: FY31 {cr(fy31)} Cr",
            "Volume/units, margin, channel and footprint detail: data gap — see strategy document",
            "Strategic priorities: close monthly gap · scale growth streams · de-risk high-risk lines",
            "Key commercial actions: per the Way Forward / Execution Plan below"]),
      ]},
      {"id": "bsc", "title": "BSC+GAP", "blocks": [
        _tbl("Balanced Scorecard", ["Perspective", "KPI", "Latest", "FY31 Target", "Gap"],
             [["Financial", "Revenue (Cr)", cr(ytd), cr(fy31), cr(fy31 - (ytd or 0))],
              ["Customer", "Portfolio share %", share, "≥ group average", "—"],
              ["Internal", "Achievement %", (ach if ach is not None else "—"), "≥ 90%", ("—" if ach is None else cr(90 - ach))],
              ["Learning & Growth", "Data-driven decisions", "Active", "Mature", "—"]]),
      ]},
      {"id": "fiveplan", "title": "5-Year Plan", "blocks": [
        _tbl("Phased Roadmap", ["Phase", "Year", "Revenue (Cr)", "Focus"],
             [["Foundation", "FY27", cr(proj["FY27"]), "Stabilise & close gaps"],
              ["Expansion", "FY28", cr(proj["FY28"]), "Scale growth streams"],
              ["Acceleration", "FY29", cr(proj["FY29"]), "Deepen market position"],
              ["Scale-Up", "FY30", cr(proj["FY30"]), "Scale footprint & capability"],
              ["Leadership", "FY31", cr(proj["FY31"]), "Category/segment leadership"]]),
        _blk("Capability progression", ["Digital, people and footprint milestones per strategy document (data gap)"]),
      ]},
      {"id": "wayforward", "title": "Way Forward", "blocks": [
        _blk("Prioritised Recommendations (next 90 days)", [
            f"{a['what']} — Owner: {a['owner']} — Timeline: next 90 days — Output: measurable target lift"
            for a in actions]),
        _blk("Priority", ["Critical: urgent LC/NOC/rate-approval escalations · High: gap-closing · Medium: capability · Lower: long-term"]),
      ]},
      {"id": "execution", "title": "Execution Plan", "blocks": [
        _tbl("Initiatives", ["Initiative", "Owner", "KPI", "Timeline"],
             [[a["what"], a["owner"], "target achievement", "next 90 days"] for a in actions]),
        _blk("Reconciliation", ["Initiatives reconcile with Business Plan, BSC, 5-Year Plan and Risk Register."]),
      ]},
      {"id": "risk", "title": "Risk & Mitigation", "blocks": [
        _tbl("Risk Register", ["Risk", "Severity", "Likelihood", "Mitigation", "Owner"],
             [[f"Target gap ({risk})", "High" if risk == "High" else "Med", "High" if under else "Med", "Recovery actions + monthly review", "SBU Head"],
              ["FX / import cost", "Med", "Med", "Hedge + landed-cost watch", "Finance"],
              ["LC / NOC approval delay", "High", "Med", "Escalate to MD/CXO", "Procurement + CXOs"],
              ["Demand / market decline", "Med", "Med", "Marketing-led demand + channel expansion", "Marketing"]]),
      ]},
      {"id": "playbook", "title": "Playbook", "blocks": [
        _blk("Strategic Playbook (football formation)", [
            "Governance/Control — monthly performance review & escalation",
            "Defence — risk & stability (FX, LC, working capital)",
            "Midfield — people, process & technology (DWH/MCP, automation)",
            "Attack — growth, customer & innovation (scale growth streams)",
            f"5-year goal: {cr(fy31)} Cr by FY31",
            "Substitutions/contingency: recovery plans + portfolio reallocation per BCG quadrant"]),
      ]},
    ]
    return tabs


PMS_URL = "https://pms-mcp.vercel.app/api/mcp"
FIN_URL = "https://akij-finance-app.vercel.app/api/mcp"

MCP_TOOLS = {
    "pms": ["list_business_units", "pms_get_sourcing", "pms_materials_to_order", "pms_draft_rfq",
            "pms_comparative_statement", "pms_price_trigger", "pms_material_intelligence",
            "pms_freight_intelligence", "pms_run"],
    "ims": ["list_plants", "inventory_summary", "list_inventory_transactions", "list_gate_passes"],
    "finance": ["list_units", "list_profit_centers", "get_income_statement", "get_balance_sheet",
                "get_working_capital", "get_cash_flow", "get_units_summary",
                "get_coal_lc", "get_coal_projection"],
}


def _mcp_post(url, method, params=None, timeout=15):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    r = urllib.request.urlopen(req, timeout=timeout)
    txt = r.read().decode()
    if "data: " in txt:
        txt = txt.split("data: ", 1)[1]
    return json.loads(txt)


def _mcp_tool(url, name, args, timeout=15):
    r = _mcp_post(url, "tools/call", {"name": name, "arguments": args}, timeout)
    content = r.get("result", {}).get("content", [])
    if not content:
        return None
    txt = content[0].get("text", "")
    try:
        return json.loads(txt)
    except Exception:
        return txt


def build_mcp_intelligence(sbus):
    """Query PMS / IMS / Finance MCPs and combine with tower revenue for cross-MCP signals."""
    import urllib.request

    intel = {
        "pms": {"name": "AKIJ PMS", "label": "Procurement Management", "type": "Remote MCP",
                "endpoint": PMS_URL, "status": "Online", "units": [], "materials": {}},
        "ims": {"name": "AKIJ IMS", "label": "Inventory & Supply Chain", "type": "Local MCP",
                "endpoint": "DWH wms schema", "status": "Online", "inventory": []},
        "finance": {"name": "AKIJ Finance", "label": "Financial Statements", "type": "Remote MCP",
                    "endpoint": FIN_URL, "status": "Online", "summary": None},
        "signals": [],
    }

    # ---- PMS ----
    try:
        units = _mcp_tool(PMS_URL, "list_business_units", {}, timeout=20)
        if isinstance(units, list):
            intel["pms"]["units"] = units
    except Exception as e:
        intel["pms"]["status"] = "Offline (%s)" % str(e)[:50]

    for s in sbus[:8]:
        code = s["code"]
        try:
            mat = _mcp_tool(PMS_URL, "pms_materials_to_order", {"business_unit_code": code}, timeout=20)
            if isinstance(mat, list) and mat:
                intel["pms"]["materials"][code] = mat
        except Exception:
            pass

    # ---- IMS (DWH wms) ----
    try:
        rows = q("SELECT TOP 8 strSBUName, COUNT(*) AS txn_count "
                 "FROM wms.tblInventoryTransactionHeaderArc WHERE isActive=1 "
                 "GROUP BY strSBUName ORDER BY txn_count DESC")
        intel["ims"]["inventory"] = [{"sbu": r["strSBUName"], "txn": r["txn_count"]} for r in rows]
    except Exception as e:
        intel["ims"]["status"] = "Offline (DWH %s)" % str(e)[:50]

    # ---- Finance ----
    try:
        fin = _mcp_tool(FIN_URL, "get_units_summary", {}, timeout=25)
        intel["finance"]["summary"] = fin
    except Exception as e:
        intel["finance"]["status"] = "Unreachable (%s)" % str(e)[:50]

    # ---- Cross-MCP signals: procurement risk vs revenue risk ----
    for s in sbus:
        mat = intel["pms"]["materials"].get(s["code"], [])
        if not mat:
            continue
        critical = [m for m in mat if isinstance(m, dict) and m.get("needs_order")]
        if not critical:
            continue
        lowest = min(critical, key=lambda m: (m.get("days_of_supply") or 999))
        signal = {
            "sbu": s["code"],
            "revenue_risk": s["risk"],
            "ach": s["ach"],
            "procurement_items": len(critical),
            "critical_item": lowest.get("item_name", "—"),
            "days_supply": lowest.get("days_of_supply"),
            "moq": lowest.get("moq"),
            "lead_time": lowest.get("lead_time_days"),
            "source_country": lowest.get("source_country"),
            "insight": ("%s revenue is %s (%.0f%% ach) but %s is critically low at %.1f days of supply "
                        "(safety stock breach) — procurement risk could disrupt future revenue."
                        % (s["code"], (s["risk"] or "No target").lower(),
                           (s["ach"] or 0), lowest.get("item_name", "key material"),
                           (lowest.get("days_of_supply") or 0))),
        }
        intel["signals"].append(signal)

    return intel


if __name__ == "__main__":
    main()