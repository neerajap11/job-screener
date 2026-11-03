"""Bengaluru/Hyderabad job screener.

Checks company job feeds, keeps MBA-type roles needing <= MAX_YEARS experience,
tracks which jobs are new, writes jobs.html and sends a macOS notification.
Run:  python3 screener.py
"""
import html
import json
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
SEEN_FILE = HERE / "seen_jobs.json"
REPORT_FILE = HERE / "jobs.html"
LOG_FILE = HERE / "last_run.log"
CURRENT_FILE = HERE / "current_jobs.json"

# ---------------------------------------------------------------- settings
CITIES = ["bengaluru", "bangalore", "hyderabad"]
MAX_YEARS = 8
MAX_AGE_DAYS = 90            # hide roles posted longer ago than this (roles without a date are kept)
NEW_DAYS = 7                 # NEW badge: posted within this many days (or first seen, when a site gives no date)
OPEN_PAGE_WHEN_NEW = True    # open jobs.html in the browser when new jobs are found

INCLUDE = [
    r"product manager", r"product owner", r"product management", r"program manager",
    r"programme manager", r"program management", r"project manager", r"project management",
    r"strateg", r"business analyst", r"business manager", r"business management",
    r"associate", r"client solutions", r"client consultant", r"solutions specialist",
    r"\brisk\b", r"chief of staff", r"\bmba\b",
    r"product lead", r"product specialist", r"product analyst", r"growth", r"\bgtm\b",
    r"go[- ]to[- ]market", r"\bops\b", r"business operations", r"bizops",
]
EXCLUDE = [
    r"engineer", r"developer", r"\bsde\b", r"\bqa\b", r"\btest", r"intern\b", r"internship",
    r"vice president", r"\bvp\b", r"director", r"\bhead\b", r"principal", r"architect",
    r"data scien", r"\bsvp\b", r"\bavp\b", r"executive director", r"managing director",
    r"scientist", r"support associate", r"technical support", r"advocacy", r"investigation", r"quality associate",
    r"operations associate", r"catalog", r"data associate", r"transaction risk", r"seller support",
]
# keywords used for sites that are searched by keyword rather than by city
SEARCH_TERMS = ["product manager", "program manager", "project manager", "strategy",
                "business analyst", "associate", "client solutions", "risk", "product lead",
                "product analyst", "product specialist", "growth", "go to market", "strategy operations"]

# ---------------------------------------------------------------- companies
WORKDAY = [  # name, tenant, wdN, site
    ("Salesforce", "salesforce", "wd12", "External_Career_Site"),
    ("Adobe", "adobe", "wd5", "external_experienced"),
    ("Workday", "workday", "wd5", "Workday"),
    ("Mastercard", "mastercard", "wd1", "CorporateCareers"),
    ("PayPal", "paypal", "wd1", "jobs"),
    ("Target", "target", "wd5", "targetcareers"),
    ("Novartis", "novartis", "wd3", "Novartis_Careers"),
    ("Wells Fargo", "wf", "wd1", "WellsFargoJobs"),
    ("Citi", "citi", "wd5", "2"),
    ("Barclays", "barclays", "wd3", "External_Career_Site_Barclays"),
    ("Morgan Stanley", "ms", "wd5", "External"),
    ("Deutsche Bank", "db", "wd3", "DBWebsite"),
    ("Cisco", "cisco", "wd5", "Cisco_Careers"),
    ("Visa", "visa", "wd5", "Visa"),
    ("Lowe's", "lowes", "wd5", "LWS_External_CS"),
    ("BrowserStack", "browserstack", "wd3", "External"),
    ("BlackRock", "blackrock", "wd1", "BlackRock_Professional"),
    ("Postman", "postman", "wd108", "careers"),
    ("Nvidia", "nvidia", "wd5", "NVIDIAExternalCareerSite"),
    ("Intel", "intel", "wd1", "External"),
    ("Expedia", "expedia", "wd108", "Search"),
    ("Autodesk", "autodesk", "wd1", "Ext"),
    ("Sprinklr", "sprinklr", "wd1", "careers"),
    ("Samsung", "sec", "wd3", "Samsung_Careers"),
    ("Philips", "philips", "wd3", "jobs-and-careers"),
    ("Shell", "shell", "wd3", "ShellCareers"),
    ("Nike", "nike", "wd1", "nke"),
    ("Fidelity", "fmr", "wd1", "FidelityCareers"),
    ("State Street", "statestreet", "wd1", "Global"),
    ("Northern Trust", "ntrs", "wd1", "northerntrust"),
    ("Invesco", "invesco", "wd1", "IVZ"),
    ("FactSet", "factset", "wd108", "FactSetCareers"),
    ("Nasdaq", "nasdaq", "wd1", "Global_External_Site"),
    ("Broadridge", "broadridge", "wd5", "Careers"),
    ("SS&C", "ssctech", "wd1", "SSCTechnologies"),
    ("Millennium", "mlp", "wd5", "mlpcareers"),
]
GREENHOUSE = [("Arcesium", "arcesiumllc"), ("Razorpay", "razorpaysoftwareprivatelimited"),
              ("Groww", "groww"), ("Slice", "slice"), ("InMobi", "inmobi"),
              ("Stripe", "stripe"), ("Databricks", "databricks"), ("Okta", "okta"), ("Twilio", "twilio"),
              ("Airbnb", "airbnb"), ("Agoda", "agoda"), ("AQR", "aqr"), ("WorldQuant", "worldquant"),
              ("Squarepoint", "squarepointcapital")]
LEVER = [("CRED", "cred"), ("Meesho", "meesho"), ("Paytm", "paytm"), ("Mindtickle", "mindtickle"), ("Fi Money", "fi"),
         ("LinkedIn", "linkedin")]
SMARTRECRUITERS = [("ServiceNow", "servicenow"), ("Swiggy", "swiggy"),
                   ("Freshworks", "freshworks"), ("Unacademy", "unacademy"), ("PhonePe", "PHONEPELIMITED"),
                   ("Lendingkart", "lendingkart"), ("ixigo", "ixigo"), ("Whatfix", "whatfix"), ("Bosch", "boschgroup")]
ASHBY = [("Snowflake", "snowflake"), ("Atlan", "atlan")]
ORACLE = [("Oracle", "eeho.fa.us2.oraclecloud.com", "CX_45001"),
          ("JPMorgan Chase", "jpmc.fa.oraclecloud.com", "CX_1001"),
          ("American Express", "egug.fa.us2.oraclecloud.com", "CX_1")]

NOT_COVERED = [
    "Rapido, Zepto, upGrad, Spinny, Ather, SAP Labs, Uber (sites block automated access)",
    "Perfios, Moneyview, Digit Insurance, BigBasket, Darwinbox (Darwinbox careers sites block automated access)",
    "Zomato, Chargebee, Dream11 (no public job listings; referrals or LinkedIn only)",
    "Ola, Zerodha (careers site lists 0 openings right now)",
    "Flipkart, Nykaa, Udaan, Khatabook, OfBusiness (careers pages broken, missing or placeholder)",
    "Pine Labs, BharatPe, PayU, Zeta, M2P, KreditBee, INDmoney, Smallcase, PolicyBazaar, CoinDCX, CoinSwitch, "
    "Myntra, Blinkit, Delhivery, Shiprocket, Purplle, Honasa, boAt, MakeMyTrip, Cleartrip, OYO, PhysicsWallah, "
    "Vedantu, Innovaccer, Icertis, Zetwerk, Moglix, Infra.Market, Tata Digital, Jio (no readable job feed found)",
    "Meta, Netflix, IBM, Dell, AMD, Booking.com, Palo Alto Networks, Nutanix, DocuSign, ThoughtSpot, Rakuten, "
    "Siemens, Walmart Global Tech (no readable job feed found)",
    "Bank of America, UBS, Societe Generale, BNP Paribas, HDFC Bank, ICICI Bank, Axis Bank, Kotak, MSCI, Qube, "
    "Addepar (no readable job feed found)",
    "Zoho (only support/sales roles listed)",
]


# ---------------------------------------------------------------- helpers
def http(url, data=None, headers=None, as_json=True):
    """Fetch with curl (python.org Python on this Mac lacks SSL certificates)."""
    args = ["curl", "-sL", "-m", "40", "-A", "Mozilla/5.0 (Macintosh) Chrome/120",
            "-H", "Accept: application/json"]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if data is not None:
        args += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(data)]
    for attempt in range(3):
        out = subprocess.run(args + [url], capture_output=True, text=True, errors="ignore").stdout
        time.sleep(0.4)
        if not as_json:
            return out
        try:
            return json.loads(out)
        except ValueError:  # rate limited or temporary error: back off and retry
            time.sleep(10 * (attempt + 1))
    raise ValueError(f"no JSON from {url[:80]}: {out[:60]!r}")


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def in_city(text):
    t = (text or "").lower()
    return any(c in t for c in CITIES)


def title_ok(title):
    t = title.lower()
    return any(re.search(p, t) for p in INCLUDE) and not any(re.search(p, t) for p in EXCLUDE)


YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)", re.I)


def min_years(text):
    """Smallest 'X years' requirement mentioned near the word experience; None if not stated."""
    found = []
    for m in YEARS_RE.finditer(text or ""):
        ctx = text[max(0, m.start() - 80): m.end() + 80].lower()
        if "experience" in ctx or "exp" in ctx:
            n = int(m.group(1))
            if 0 < n <= 30:
                found.append(n)
    return min(found) if found else None


def rel_posted(s):
    """'Posted 3 Days Ago' -> date string."""
    s = (s or "").lower()
    today = date.today()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days", s)
    return (today - timedelta(days=int(m.group(1)))).isoformat() if m else ""


def job(company, jid, title, location, url, posted="", desc=None, closes=""):
    return {"company": company, "id": f"{company}:{jid}", "title": title.strip(),
            "location": location, "url": url, "posted": posted, "desc": desc,
            "closes": (closes or "")[:10] or closing_date(desc)}


CLOSE_RE = re.compile(r"(?:closing date|apply by|applications? close[sd]?(?: on)?|last date(?: to apply)?|deadline)"
                      r"\W{0,5}(\d{1,2}(?:st|nd|rd|th)?[\s/.-]+[A-Za-z]{3,9}[\s/.,-]+\d{4}|\d{4}-\d{2}-\d{2}"
                      r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})", re.I)


def closing_date(text):
    """Closing date written in the job description, e.g. 'Closing Date: 14 Nov 2026'."""
    m = CLOSE_RE.search(text or "")
    if not m:
        return ""
    raw = re.sub(r"(?<=\d)(st|nd|rd|th)", "", m.group(1)).replace(",", " ")
    raw = re.sub(r"[\s/.-]+", " ", raw).strip()
    for f in ("%d %b %Y", "%d %B %Y", "%Y %m %d", "%d %m %Y"):
        try:
            return datetime.strptime(raw, f).date().isoformat()
        except ValueError:
            pass
    return ""


# ---------------------------------------------------------------- adapters
def workday(name, tenant, wd, site):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for city in ["Bengaluru", "Bangalore", "Hyderabad"]:
        offset = 0
        while offset < 5000:
            d = http(api + "/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": city})
            posts = d.get("jobPostings") or []
            for p in posts:
                if p.get("externalPath") in seen or not title_ok(p.get("title", "")):
                    continue
                seen.add(p["externalPath"])
                out.append((p, city))
            offset += 20
            if not posts or offset >= d.get("total", 0):
                break
    jobs = []
    for p, city in out:
        loc = p.get("locationsText", "")
        posted = rel_posted(p.get("postedOn"))
        desc, closes = None, ""
        try:
            info = http(api + p["externalPath"]).get("jobPostingInfo", {})
            desc = strip_html(info.get("jobDescription"))
            loc = ", ".join([info.get("location", "")] + (info.get("additionalLocations") or []))
            posted = info.get("startDate") or posted
            closes = info.get("endDate") or ""
        except Exception:
            pass
        if not in_city(loc):
            continue
        jobs.append(job(name, p["externalPath"], p["title"], loc, f"{base}/{site}{p['externalPath']}",
                        posted, desc, closes))
    return jobs


def greenhouse(name, board):
    d = http(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    return [job(name, j["id"], j["title"], j["location"]["name"], j["absolute_url"],
                (j.get("first_published") or j.get("updated_at") or "")[:10], strip_html(j.get("content")))
            for j in d.get("jobs", []) if in_city(j["location"]["name"]) and title_ok(j["title"])]


def lever(name, slug):
    jobs = []
    for j in http(f"https://api.lever.co/v0/postings/{slug}?mode=json"):
        cat = j.get("categories", {})
        loc = ", ".join([cat.get("location") or ""] + (cat.get("allLocations") or []))
        if not (in_city(loc) and title_ok(j["text"])):
            continue
        desc = " ".join([j.get("descriptionPlain", ""), j.get("additionalPlain", "")] +
                        [strip_html(l.get("content")) for l in j.get("lists", [])])
        posted = datetime.fromtimestamp(j["createdAt"] / 1000).date().isoformat() if j.get("createdAt") else ""
        jobs.append(job(name, j["id"], j["text"], loc, j["hostedUrl"], posted, desc))
    return jobs


def smartrecruiters(name, slug):
    jobs, offset = [], 0
    while offset < 10000:
        d = http(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?country=in&limit=100&offset={offset}")
        for p in d.get("content", []):
            loc = p.get("location", {})
            loc_s = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            if not (in_city(loc_s) and title_ok(p["name"])):
                continue
            desc = None
            try:
                sec = http(p["ref"]).get("jobAd", {}).get("sections", {})
                desc = " ".join(strip_html(v.get("text")) for v in sec.values() if isinstance(v, dict))
            except Exception:
                pass
            jobs.append(job(name, p["id"], p["name"], loc_s, f"https://jobs.smartrecruiters.com/{slug}/{p['id']}",
                            (p.get("releasedDate") or "")[:10], desc))
        offset += 100
        if offset >= d.get("totalFound", 0):
            break
    return jobs


def ashby(name, board):
    jobs = []
    for j in http(f"https://api.ashbyhq.com/posting-api/job-board/{board}").get("jobs", []):
        loc = "; ".join([j.get("location") or ""] + [l.get("location", "") for l in j.get("secondaryLocations") or []])
        if in_city(loc) and title_ok(j["title"]):
            jobs.append(job(name, j["id"], j["title"], loc, j["jobUrl"], (j.get("publishedAt") or "")[:10],
                            j.get("descriptionPlain")))
    return jobs


def rippling(name, board):
    jobs = []
    for page in range(100):
        d = http(f"https://ats.rippling.com/api/v2/board/{board}/jobs?page={page}&pageSize=100")
        for j in d.get("items", []):
            loc = "; ".join(l.get("name", "") for l in j.get("locations") or [])
            if in_city(loc) and title_ok(j["name"]):
                jobs.append(job(name, j["id"], j["name"], loc, j["url"]))
        if page + 1 >= d.get("totalPages", 0):
            break
    return jobs


def oracle(name, host, site):
    api = f"https://{host}/hcmRestApi/resources/latest"
    seen, jobs = set(), []
    for term in SEARCH_TERMS:
        for offset in range(0, 2000, 25):
            d = http(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
                     f"&finder=findReqs;siteNumber={site},limit=25,offset={offset},keyword={quote(term)}")
            reqs = (d.get("items") or [{}])[0].get("requisitionList") or []
            for r in reqs:
                loc = ", ".join([r.get("PrimaryLocation") or ""] +
                                [s.get("Name", "") for s in r.get("secondaryLocations") or []])
                if r["Id"] in seen or not (in_city(loc) and title_ok(r["Title"])):
                    continue
                seen.add(r["Id"])
                desc = None
                try:
                    det = http(f'{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true'
                               f'&finder=ById;Id="{r["Id"]}",siteNumber={site}')["items"][0]
                    desc = strip_html(" ".join(str(det.get(k) or "") for k in
                                               ("ExternalDescriptionStr", "ExternalQualificationsStr",
                                                "ExternalResponsibilitiesStr")))
                except Exception:
                    pass
                jobs.append(job(name, r["Id"], r["Title"], loc,
                                f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}",
                                r.get("PostedDate") or "", desc, r.get("PostingEndDate") or ""))
            if len(reqs) < 25:
                break
    return jobs


def amazon():
    seen, jobs = set(), []
    for city in ["Bengaluru", "Hyderabad"]:
        for term in [""]:
            for offset in range(0, 5000, 100):
                d = http(f"https://www.amazon.jobs/en/search.json?base_query={quote(term)}&loc_query={city}"
                         f"&country=IND&result_limit=100&offset={offset}&sort=recent")
                rows = d.get("jobs", [])
                for j in rows:
                    loc = j.get("normalized_location") or j.get("location", "")
                    if j["id_icims"] in seen or not (in_city(loc) and title_ok(j["title"])):
                        continue
                    seen.add(j["id_icims"])
                    try:
                        posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").date().isoformat()
                    except ValueError:
                        posted = ""
                    jobs.append(job("Amazon", j["id_icims"], j["title"], loc,
                                    "https://www.amazon.jobs" + j["job_path"], posted,
                                    strip_html(j.get("basic_qualifications", ""))))
                if len(rows) < 100:
                    break
    return jobs


def pcsx(name, host, domain):
    seen, jobs = set(), []
    for city in ["Bengaluru", "Hyderabad"]:
        for term in SEARCH_TERMS:
            for start in range(0, 300, 10):
                time.sleep(1.5)
                try:
                    d = http(f"https://{host}/api/pcsx/search?domain={domain}"
                             f"&query={quote(term)}&location={city}&start={start}")
                except ValueError:
                    break
                pos = (d.get("data") or {}).get("positions") or []
                for p in pos:
                    loc = "; ".join(p.get("locations") or [])
                    if p["id"] in seen or not (in_city(loc) and title_ok(p["name"])):
                        continue
                    seen.add(p["id"])
                    ts = p.get("postedTs")
                    posted = datetime.fromtimestamp(ts).date().isoformat() if ts else ""
                    url = p.get("positionUrl") or f"/careers/job/{p['id']}"
                    if url.startswith("/"):
                        url = f"https://{host}" + url
                    jobs.append(job(name, p["id"], p["name"], loc, url, posted))
                if len(pos) < 10:
                    break
    return jobs


def goldman():
    q = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: "
         "$searchQueryInput) { totalCount items { roleId jobTitle locations { city } } } }")
    seen, jobs = set(), []
    for term in SEARCH_TERMS + ["Bengaluru", "Hyderabad"]:
        for page in range(20):
            d = http("https://api-higher.gs.com/gateway/api/v1/graphql", {
                "operationName": "GetRoles", "query": q, "variables": {"searchQueryInput": {
                    "page": {"pageSize": 50, "pageNumber": page},
                    "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                    "filters": [], "experiences": ["PROFESSIONAL"], "searchTerm": term}}})
            items = ((d.get("data") or {}).get("roleSearch") or {}).get("items") or []
            for r in items:
                loc = ", ".join(l.get("city") or "" for l in r.get("locations") or [])
                if r["roleId"] in seen or not (in_city(loc) and title_ok(r["jobTitle"])):
                    continue
                seen.add(r["roleId"])
                jobs.append(job("Goldman Sachs", r["roleId"], r["jobTitle"], loc,
                                f"https://higher.gs.com/roles/{r['roleId'].split('_')[0]}"))
            if len(items) < 50:
                break
    return jobs


def google():
    seen, jobs = set(), []
    for city in ["Bengaluru", "Hyderabad"]:
        for pg in range(1, 80):
            page = http(f"https://www.google.com/about/careers/applications/jobs/results"
                        f"?location={city}%20India&page={pg}", as_json=False)
            paths = re.findall(r"jobs/results/(\d+-[a-z0-9-]+)", page)
            if not paths:
                break
            for path in paths:
                jid, slug = path.split("-", 1)
                title = slug.replace("-", " ").title()
                if jid in seen or not title_ok(title):
                    continue
                seen.add(jid)
                jobs.append(job("Google", jid, title, f"{city} (search)",
                                f"https://www.google.com/about/careers/applications/jobs/results/{path}"))
    return jobs


def deshaw():
    page = http("https://www.deshawindia.com/careers", as_json=False)
    i = page.find('"regularJobs":')
    rows, _ = json.JSONDecoder().raw_decode(page, i + len('"regularJobs":'))
    jobs = []
    for r in rows:
        d = r.get("data", {})
        meta = d.get("jobMetadata") or {}
        loc = ", ".join(o.get("name", "") for o in r.get("office") or meta.get("jobLocations") or [])
        title = d.get("displayName", "")
        if meta.get("isExploratory") or not (in_city(loc) and title_ok(title)):
            continue
        jd = d.get("jobDescription") or {}
        desc = strip_html(" ".join(str(jd.get(k) or "") for k in
                                   ("websiteDescription", "responsibilities", "peopleWeAreLookingFor")))
        jobs.append(job("D. E. Shaw", d.get("id"), title, loc,
                        f"https://www.deshawindia.com/careers/{d.get('jobUrl')}", "", desc, d.get("validToDate") or ""))
    return jobs


def tesco():
    from urllib.parse import unquote
    base = "https://careers.tesco.com/en_GB/careers"
    found = {}
    for offset in range(0, 300, 10):
        page = http(f"{base}/SearchJobs/?search=Bengaluru&jobOffset={offset}", as_json=False)
        links = re.findall(r'mailto:\?subject=([^&"]*)&amp;body=([^"]*)"', page)
        for subj, body in links:
            url = unquote(body)
            title = re.sub(r"^\d+\s*-\s*|\s*-\s*Tesco Careers$", "", unquote(subj))
            found.setdefault(url, title)
        if len(links) == 0:
            break
    jobs = []
    for url, title in found.items():
        if not title_ok(title):
            continue
        page = http(url, as_json=False)
        m = re.search(r'"datePosted":\s*"(\d{4}-\d{2}-\d{2})', page)
        end = re.search(r'"validThrough":\s*"(\d{4}-\d{2}-\d{2})', page)
        loc = re.search(r'"addressLocality":\s*"([^"]*)"', page)
        d = re.search(r'"description":\s*"(.*?)",\s*"', page, re.S)
        desc = strip_html(d.group(1).encode().decode("unicode_escape", "ignore")) if d else None
        jobs.append(job("Tesco", url.rstrip("/").rsplit("/", 1)[-1], title,
                        loc.group(1) if loc else "Bengaluru", url, m.group(1) if m else "", desc,
                        end.group(1) if end else ""))
    return jobs


def hsbc():
    jobs, seen = [], set()
    for city in ["Bengaluru", "Hyderabad"]:
        for start in range(0, 3000, 10):
            d = http(f"https://portal.careers.hsbc.com/api/apply/v2/jobs?domain=hsbc.com&location={city}&start={start}&num=10")
            pos = d.get("positions") or []
            for p in pos:
                loc = "; ".join(p.get("locations") or [p.get("location", "")])
                if p["id"] in seen or not (in_city(loc) and title_ok(p["name"])):
                    continue
                seen.add(p["id"])
                posted = datetime.fromtimestamp(p["t_create"]).date().isoformat() if p.get("t_create") else ""
                jobs.append(job("HSBC", p["id"], p["name"], loc, p.get("canonicalPositionUrl", ""), posted))
            if not pos:
                break
    return jobs


def atlassian():
    jobs = []
    for j in http("https://www.atlassian.com/endpoint/careers/listings"):
        loc = "; ".join(j.get("locations") or [])
        if in_city(loc) and title_ok(j["title"]):
            desc = strip_html(" ".join(j.get(k) or "" for k in ("overview", "responsibilities", "qualifications")))
            posted = ((j.get("portalJobPost") or {}).get("updatedDate") or "")[:10]
            jobs.append(job("Atlassian", j["id"], j["title"], loc, j.get("applyUrl", ""), posted, desc))
    return jobs


def apple():
    cookies = HERE / ".apple_cookies"
    hdr = subprocess.run(["curl", "-s", "-m", "30", "-A", "Mozilla/5.0 (Macintosh) Chrome/120", "-D", "-", "-o", "/dev/null",
                          "-c", str(cookies), "https://jobs.apple.com/api/v1/CSRFToken"],
                         capture_output=True, text=True).stdout
    token = re.search(r"x-apple-csrf-token:\s*(\S+)", hdr, re.I).group(1)
    jobs = []
    for page in range(1, 60):
        out = subprocess.run(["curl", "-s", "-m", "30", "-A", "Mozilla/5.0 (Macintosh) Chrome/120", "-b", str(cookies),
                              "-X", "POST", "https://jobs.apple.com/api/v1/search", "-H", f"x-apple-csrf-token: {token}",
                              "-H", "Content-Type: application/json", "-d", json.dumps({
                                  "query": "", "filters": {"locations": ["postLocation-BGS", "postLocation-HY1"]},
                                  "page": page, "locale": "en-in", "sort": "newest",
                                  "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}})],
                             capture_output=True, text=True).stdout
        time.sleep(0.5)
        rows = json.loads(out)["res"]["searchResults"]
        for r in rows:
            loc = "; ".join(l.get("name") or l.get("city") or "" for l in r.get("locations") or [])
            if not title_ok(r["postingTitle"]):
                continue
            jobs.append(job("Apple", r["positionId"], r["postingTitle"], loc or "Bengaluru/Hyderabad",
                            f"https://jobs.apple.com/en-in/details/{r['positionId']}/{r.get('transformedPostingTitle', '')}",
                            (r.get("postDateInGMT") or "")[:10], r.get("jobSummary")))
        if len(rows) < 20:
            break
    return jobs


def intuit():
    seen, jobs = set(), []
    for city in ["Bengaluru", "Hyderabad"]:
        for page in range(1, 20):
            h = http(f"https://jobs.intuit.com/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage=100"
                     f"&Keywords={city}&Location=&SearchResultsModuleName=Search+Results"
                     f"&SearchFiltersModuleName=Search+Filters&SortCriterion=0&SortDirection=0&SearchType=5")["results"]
            rows = re.findall(r'href="(/job/[^"]+)"[^>]*>\s*<h2>([^<]+)</h2>.*?job-location">([^<]+)<', h, re.S)
            for path, title, loc in rows:
                title = html.unescape(title).strip()
                if path in seen or not (in_city(loc) and title_ok(title)):
                    continue
                seen.add(path)
                jobs.append(job("Intuit", path.rsplit("/", 1)[-1], title, loc.strip(), "https://jobs.intuit.com" + path))
            if len(rows) < 100:
                break
    return jobs


def standard_chartered():
    seen, jobs = set(), []
    for city in ["Bangalore", "Hyderabad"]:
        for page in range(0, 100):
            d = http("https://jobs.standardchartered.com/services/recruiting/v1/jobs", {
                "locale": "en_GB", "pageNumber": page, "sortBy": "", "keywords": "", "location": city,
                "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
            rows = d.get("jobSearchResult") or []
            for r in rows:
                r = r["response"]
                loc = "; ".join(r.get("jobLocationShort") or [])
                title = r.get("unifiedStandardTitle", "")
                if r["id"] in seen or not (in_city(loc) and title_ok(title)):
                    continue
                seen.add(r["id"])
                dmy = lambda s: "-".join(reversed(s.split("/"))) if s and s.count("/") == 2 else ""
                jobs.append(job("Standard Chartered", r["id"], title, loc,
                                f"https://jobs.standardchartered.com/job/{r.get('unifiedUrlTitle', '')}/{r['id']}-en_GB",
                                dmy(r.get("unifiedStandardStart")), None, dmy(r.get("unifiedStandardEnd"))))
            if len(rows) < 10:
                break
    return jobs


def juspay():
    jobs = []
    for j in http("https://joinus.juspay.in/api/careerJobOpening?limit=1000")["allJobs"]:
        if str(j.get("opening_status")) != "True" or not (in_city(j.get("job_location")) and title_ok(j["job_title"])):
            continue
        desc = f"{j.get('experience_year', '')} years experience " + (j.get("job_description_career") or "")
        jobs.append(job("Juspay", j["job_id"], j["job_title"], j["job_location"], "https://juspay.io/careers", "", desc))
    return jobs


def lenskart():
    jobs = []
    for j in http("https://ainterviews.com/api/job_board/lenskart_ho/jobs/")["jobs"]:
        if in_city(j.get("location")) and title_ok(j["title"]):
            jobs.append(job("Lenskart", j["id"], j["title"], j["location"], "https://ainterviews.com" + j["apply_url"],
                            (j.get("posted_date") or "")[:10], strip_html(j.get("description")),
                            j.get("application_deadline") or ""))
    return jobs


def urban_company():
    jobs = []
    for j in http("https://www.urbanclap.com/api/v2/platform-gateway/getAllJobs", {})["jobs"]:
        loc = str(j.get("location_city") or j.get("location"))
        if in_city(loc) and title_ok(j["job_title"]):
            jobs.append(job("Urban Company", j["job_id"], j["job_title"], loc.strip("[]'"),
                            f"https://careers.urbancompany.com/jobDetail?id={j['job_id']}", "",
                            strip_html(j.get("job_description"))))
    return jobs


def mynexthire(name, slug):
    import base64
    d = http(f"https://{slug}.mynexthire.com/employer/careers/reqlist/get",
             {"source": "careers", "code": "", "filterByBuId": -1})
    jobs = []
    for r in d.get("reqDetailsBOList") or []:
        loc = " ".join([r.get("location") or "", str(r.get("locationList") or "")])
        if not (in_city(loc) and title_ok(r["reqTitle"])):
            continue
        p = base64.b64encode(json.dumps({"pageType": "jd", "cvSource": "careers", "reqId": int(r["reqId"]),
                                         "requester": {"id": "", "code": "", "name": ""}, "page": "careers",
                                         "bufilter": -1, "customFields": {}}, separators=(",", ":")).encode()).decode()
        desc = f"{float(r.get('expMin') or 0):.0f} years experience " + strip_html(r.get("jdDisplay"))
        jobs.append(job(name, r["reqId"], r["reqTitle"], r.get("location") or "",
                        f"https://{slug}.mynexthire.com/employer/jobs?src=careers&p={p}",
                        (r.get("approvedOn") or "")[:10], desc))
    return jobs


def kula(name, account):
    jobs = []
    for page in range(1, 20):
        rows = http(f"https://careers.kula.ai/api/internal/ats_job_posts?accountName={account}&page={page}"
                    f"&type=ats_job_post.index&items=99").get("data") or []
        for r in rows:
            aj = r.get("ats_job") or {}
            # the office name can be just the company, and the city a locality such as Bellandur
            loc = "; ".join((o.get("location") or o.get("name", "")) + (", Bengaluru" if o.get("state") == "Karnataka" else "")
                            for o in aj.get("offices") or [])
            if in_city(loc) and title_ok(r["title"]):
                jobs.append(job(name, r["id"], r["title"], loc, f"https://careers.kula.ai/{account}/{r['id']}",
                                (r.get("launch_at") or "")[:10], strip_html(aj.get("job_description")),
                                (r.get("end_at") or "")[:10]))
        if len(rows) < 99:
            break
    return jobs


def cars24():
    d = http("https://api.cars24.com/gw/plt/bffsvc/api/jobs", headers={"Origin": "https://careers.cars24.com"})
    rows = next(v for v in (d.values() if isinstance(d, dict) else [d]) if isinstance(v, list))
    jobs = []
    for j in rows:
        loc = str(j.get("location_city") or j.get("location"))
        if not (in_city(loc) and title_ok(j["job_title"])):
            continue
        ts = j.get("job_created_timestamp") or ""
        posted = "-".join(reversed(ts[:10].split("-"))) if ts[2:3] == "-" else ""
        jobs.append(job("Cars24", j["job_id"], j["job_title"], loc.strip("[]").replace("'", ""),
                        f"https://careers.cars24.com/job/{j['job_id']}", posted,
                        f"{j.get('experience_from', '')} years experience"))
    return jobs


def turbohire(name, sub, org):
    hdr = {"Origin": f"https://{sub}.turbohire.co", "Referer": f"https://{sub}.turbohire.co/"}
    token = http("https://thapi.azurewebsites.net/api/token/noauth", headers=hdr)["access_token"]
    rows = http(f"https://thapi.azurewebsites.net/api/careerpagev2/filteredjobs?orgId={org}&pageType=0", {},
                headers={**hdr, "Authorization": f"Bearer {token}"}) or []
    jobs = []
    for j in rows if isinstance(rows, list) else rows.get("Result") or []:
        locs = json.loads(j["Location"]) if isinstance(j.get("Location"), str) else (j.get("Location") or [])
        loc = "; ".join(l.get("Address", "") for l in locs)
        if not (in_city(loc) and title_ok(j["JobTitle"])):
            continue
        exp = j.get("Experience")
        exp = json.loads(exp) if isinstance(exp, str) else (exp or {})
        jobs.append(job(name, j["JobId"], j["JobTitle"], loc,
                        f"https://{sub}.turbohire.co/job/publicjobs/{j['JobIdObfuscated']}",
                        (j.get("PublishedDate") or "")[:10],
                        f"{exp.get('MinExp', '')} years experience " + strip_html(j.get("JobDescV2"))))
    return jobs


def keka(name, sub, portal):
    jobs = []
    for j in http(f"https://{sub}.keka.com/careers/api/embedjobs/default/active/{portal}"):
        loc = "; ".join(f"{l.get('name', '')} {l.get('city', '')}" for l in j.get("jobLocations") or [])
        if in_city(loc) and title_ok(j["title"]):
            jobs.append(job(name, j["id"], j["title"], loc, f"https://{sub}.keka.com/careers/jobdetails/{j['id']}",
                            (j.get("publishedOn") or "")[:10],
                            f"{(j.get('experience') or '').split('-')[0]} years experience " + strip_html(j.get("description"))))
    return jobs


def sources():
    s = [(n, lambda a=a: workday(*a)) for a in WORKDAY for n in [a[0]]]
    s += [(n, lambda n=n, b=b: greenhouse(n, b)) for n, b in GREENHOUSE]
    s += [(n, lambda n=n, b=b: lever(n, b)) for n, b in LEVER]
    s += [(n, lambda n=n, b=b: smartrecruiters(n, b)) for n, b in SMARTRECRUITERS]
    s += [(n, lambda n=n, b=b: ashby(n, b)) for n, b in ASHBY]
    s += [(a[0], lambda a=a: oracle(*a)) for a in ORACLE]
    s += [("Microsoft", lambda: pcsx("Microsoft", "apply.careers.microsoft.com", "microsoft.com")),
          ("Qualcomm", lambda: pcsx("Qualcomm", "careers.qualcomm.com", "qualcomm.com")),
          ("HSBC", hsbc), ("Atlassian", atlassian), ("Apple", apple), ("Intuit", intuit),
          ("Standard Chartered", standard_chartered), ("Juspay", juspay), ("Lenskart", lenskart),
          ("Urban Company", urban_company), ("Licious", lambda: mynexthire("Licious", "licious")),
          ("ShareChat", lambda: mynexthire("ShareChat", "sharechat")), ("Acko", lambda: kula("Acko", "acko")),
          ("Cashfree", lambda: kula("Cashfree", "cashfree")), ("Rippling", lambda: rippling("Rippling", "rippling")),
          ("Cars24", cars24), ("Navi", lambda: turbohire("Navi", "navi", "3e818601-0baa-429c-b6f8-4b21903ae0e6")),
          ("Jupiter", lambda: keka("Jupiter", "jupiter", "b5279857-cf81-4dde-a215-fc48957ee2b5"))]
    s += [("Amazon", amazon), ("Goldman Sachs", goldman),
          ("Google", google), ("D. E. Shaw", deshaw), ("Tesco", tesco)]
    return s


# ---------------------------------------------------------------- run
def run_source(item):
    name, fn = item
    try:
        return name, fn(), None
    except Exception as e:
        return name, [], f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"


def render(all_jobs, seen, status, latest=(), latest_at=""):
    today = date.today()
    # spread company colours around the colour wheel (golden angle) so neighbours differ
    hues = {c: round(i * 137.5) % 360 for i, c in enumerate(sorted({j["company"] for j in all_jobs}))}
    # roles without a posting date that were in the very first run aren't "new", just the starting list
    baseline = min((v["first_seen"] for v in seen.values()), default="")
    data = []
    for j in all_jobs:
        if j.get("closes") and j["closes"] < today.isoformat():
            continue
        if j.get("posted") and j["posted"] < (today - timedelta(days=MAX_AGE_DAYS)).isoformat():
            continue
        first = seen[j["id"]]["first_seen"]
        loc = j["location"].lower()
        data.append({
            "id": j["id"], "company": j["company"], "title": j["title"], "location": j["location"][:90],
            "url": j["url"], "posted": j["posted"], "first": first, "years": j["years"],
            "closes": j.get("closes", ""), "latest": j["id"] in latest,
            "isNew": ((today - date.fromisoformat(j["posted"])).days < NEW_DAYS) if j["posted"]
                     else (first != baseline and (today - date.fromisoformat(first)).days < NEW_DAYS), "hue": hues[j["company"]],
            "city": " ".join(c for c, keys in (("blr", ("bengaluru", "bangalore")), ("hyd", ("hyderabad",)))
                             if any(k in loc for k in keys)),
        })
    data.sort(key=lambda d: d["posted"] or d["first"], reverse=True)
    data.sort(key=lambda d: not d["isNew"])
    new_count = sum(1 for d in data if d["isNew"])
    ok = [n for n, (c, e) in status.items() if not e]
    bad = [f"{n} ({e.splitlines()[0][:80]})" for n, (c, e) in status.items() if e]
    checked = datetime.now().strftime("%d %b %Y, %I:%M %p")
    jobs_json = json.dumps(data).replace("</", "<\\/")
    REPORT_FILE.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Screener</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--new:#ecfdf5;--acc:#047857;--todo:#b45309}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--new:#052e22;--acc:#34d399;--todo:#fbbf24}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} .mut{{color:var(--mut)}} a{{color:var(--fg)}} .sm{{font-size:12px;margin-top:2px}}
.tabs{{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-top:18px}}
.tabs button{{font:inherit;font-weight:600;padding:8px 14px;border:0;border-bottom:2px solid transparent;background:none;color:var(--mut);cursor:pointer}}
.tabs button.on{{color:var(--fg);border-bottom-color:var(--fg)}}
.bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px 0}}
input,select{{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}}
input{{flex:1;min-width:180px;max-width:360px}}
.seg{{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.seg button{{font:inherit;padding:8px 14px;border:0;background:transparent;color:var(--fg);cursor:pointer}}
.seg button+button{{border-left:1px solid var(--line)}} .seg button.on{{background:var(--fg);color:var(--bg)}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{font-size:12px;color:var(--mut);font-weight:600}} tr.new{{background:var(--new)}}
td:nth-child(3),td:nth-child(4){{white-space:nowrap}}
.badge{{background:var(--acc);color:var(--bg);font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px}}
.co{{display:inline-block;white-space:nowrap;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px;
  background:hsl(var(--h) 75% 90%);color:hsl(var(--h) 65% 26%);border-left:4px solid hsl(var(--h) 65% 45%)}}
@media (prefers-color-scheme:dark){{.co{{background:hsl(var(--h) 45% 22%);color:hsl(var(--h) 80% 82%)}}}}
.act{{width:1%}} .act button{{display:block;width:100%;text-align:left;white-space:nowrap;font:inherit;font-size:12px;padding:4px 8px;margin:0 0 4px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--fg);cursor:pointer}}
.act button.todo.on{{border-color:var(--todo);color:var(--todo);font-weight:600}}
.act button.app.on{{border-color:var(--acc);color:var(--acc);font-weight:600}}
.close{{color:var(--mut)}}
tr.grp td{{cursor:pointer;background:color-mix(in srgb,var(--fg) 4%,transparent);padding:10px}} tr.grp:hover td{{background:color-mix(in srgb,var(--fg) 8%,transparent)}}
.arr{{display:inline-block;width:18px;font-size:16px;transition:transform .15s;color:var(--mut)}} .arr.o{{transform:rotate(90deg)}}
tr.grp .badge{{margin-left:6px}} .ghost{{font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--fg);cursor:pointer}} .close.soon{{color:#dc2626;font-weight:600}}
.just{{font-size:11px;font-weight:700;color:#fff;background:#2563eb;border-radius:4px;padding:1px 5px}}
.gone{{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:1px 5px}}
.empty{{padding:32px 10px;color:var(--mut)}} details{{margin-top:24px}}
</style></head><body>
<h1>Job Screener — Bengaluru &amp; Hyderabad</h1>
<div class="mut">Last checked {checked} · <b>Just added</b> = roles found in the run of {latest_at or "—"}</div>
<div class="mut"> · {len(data)} matching roles · <b>{new_count} new</b> (added in the last {NEW_DAYS} days) · experience ≤ {MAX_YEARS} yrs or not stated · posted in the last {MAX_AGE_DAYS} days</div>
<div class="tabs" id="tabs"><button data-t="latest">🔔 Just added</button><button data-t="open">Open roles</button><button data-t="todo">☆ To do</button><button data-t="applied">✓ Applied</button></div>
<div class="bar"><div class="seg" id="loc">
<button data-c="">All</button><button data-c="blr">Bengaluru</button><button data-c="hyd">Hyderabad</button></div>
<select id="co"><option value="">All companies</option>{"".join(
f'<option>{html.escape(c)}</option>' for c in sorted(hues, key=str.lower))}</select>
<input id="q" placeholder="Filter by title…"><button id="exp" class="ghost">Expand / collapse all</button><span class="mut" id="cnt"></span></div>
<div class="wrap"><table><thead><tr><th></th><th>Role &amp; location</th><th>Experience</th><th>Posted</th><th></th></tr></thead>
<tbody id="tb"></tbody></table></div>
<details><summary>Sources: {len(ok)} worked, {len(bad)} failed, {len(NOT_COVERED)} not covered yet</summary>
<p><b>Failed this run:</b> {html.escape("; ".join(bad)) or "none"}</p>
<p><b>Not covered yet:</b> {html.escape(", ".join(NOT_COVERED))}</p></details>
<script>
const JOBS={jobs_json};
const LIVE=new Set(JOBS.map(j=>j.id));
// saved roles live in this browser's storage, with a copy of the job so they survive the listing being removed
function load(k,d){{try{{return JSON.parse(localStorage.getItem(k))??d}}catch(e){{return d}}}}
function save(k,v){{try{{localStorage.setItem(k,JSON.stringify(v))}}catch(e){{}}}}
const open_=new Set();
let marks=load('marks',{{}}),loc=load('loc',''),tab=location.hash==='#new'?'latest':load('tab','open');
const $=id=>document.getElementById(id),q=$('q'),co=$('co'),tb=$('tb');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const fmt=d=>d?new Date(d+'T00:00').toLocaleDateString('en-GB',{{day:'2-digit',month:'short',year:'numeric'}}):'';
const days=d=>Math.round((new Date(d+'T00:00')-new Date(new Date().toDateString()))/864e5);
function ago(d){{const n=-days(d);return n<=0?'today':n===1?'yesterday':n+' days ago'}}
function closes(d){{if(!d)return '';const n=days(d);
  return n<0?'<div class="sm close">closed</div>':`<div class="sm close ${{n<=7?'soon':''}}">${{n===0?'closes today':n===1?'closes tomorrow':'closes in '+n+' days'}}</div>`}}
function mark(id,st){{const j=JOBS.find(x=>x.id===id)||marks[id]?.job;
  if(marks[id]?.status===st) delete marks[id];
  else marks[id]={{status:st,job:j,at:new Date().toISOString().slice(0,10)}};
  save('marks',marks);draw()}}
function row(j){{const m=marks[j.id]?.status;
  return `<tr class="${{j.isNew&&tab==='open'?'new':''}}"><td>${{j.isNew&&tab==='open'?'<span class=badge>NEW</span>':''}}</td>
  <td><a href="${{esc(j.url)}}" target="_blank" rel="noopener">${{esc(j.title)}}</a>
   ${{j.latest?' <span class=just>JUST ADDED</span>':''}}${{LIVE.has(j.id)?'':' <span class=gone>no longer listed</span>'}}<div class="mut sm">${{esc(j.location)}}</div></td>
  <td>${{j.years==null?'<span class=mut>Not stated</span>':j.years+'+ yrs'}}</td>
  <td><span title="${{j.posted?fmt(j.posted):''}}">${{j.posted?'Posted '+ago(j.posted):'<span class=mut>Date not given</span>'}}</span>${{closes(j.closes)}}
   <div class="mut sm">${{m?(m==='applied'?'applied ':'saved ')+ago(marks[j.id].at):'first seen '+ago(j.first)}}</div></td>
  <td class="act"><button class="todo ${{m==='todo'?'on':''}}" data-id="${{esc(j.id)}}" data-s="todo">${{m==='todo'?'★':'☆'}} To do</button><button class="app ${{m==='applied'?'on':''}}" data-id="${{esc(j.id)}}" data-s="applied">✓ Applied</button></td></tr>`}}
function draw(){{const v=q.value.toLowerCase();
  document.querySelectorAll('#loc button').forEach(b=>b.classList.toggle('on',b.dataset.c===loc));
  document.querySelectorAll('#tabs button').forEach(b=>{{const t=b.dataset.t,n=t==='latest'?JOBS.filter(j=>j.latest).length:t==='open'?JOBS.filter(j=>!marks[j.id]).length:Object.values(marks).filter(m=>m.status===t).length;
    b.classList.toggle('on',t===tab);b.textContent=b.textContent.replace(/ \\(\\d+\\)$/,'')+` (${{n}})`}});
  let list=tab==='latest'?JOBS.filter(j=>j.latest):tab==='open'?JOBS.filter(j=>!marks[j.id])
    :Object.entries(marks).filter(([,m])=>m.status===tab).sort((a,b)=>b[1].at.localeCompare(a[1].at)).map(([id,m])=>JOBS.find(j=>j.id===id)||m.job);
  list=list.filter(j=>j&&(j.company+' '+j.title).toLowerCase().includes(v)&&(!loc||(j.city||'').includes(loc))&&(!co.value||j.company===co.value));
  // one collapsible header per company (A–Z), its roles underneath
  const groups={{}};list.forEach(j=>(groups[j.company]=groups[j.company]||[]).push(j));
  tb.innerHTML=Object.keys(groups).sort((a,b)=>a.localeCompare(b)).map(c=>{{const g=groups[c],o=open_.has(c),n=g.filter(j=>j.isNew).length;
    return `<tr class="grp" data-c="${{esc(c)}}"><td colspan=5><span class="arr ${{o?'o':''}}">▸</span>
     <span class="co" style="--h:${{g[0].hue}}">${{esc(c)}}</span> <b>${{g.length}}</b> <span class=mut>role${{g.length>1?'s':''}}</span>
     ${{n&&tab==='open'?`<span class=badge>${{n}} NEW</span>`:''}}</td></tr>`+(o?g.map(row).join(''):'')}}).join('')
   ||`<tr><td colspan=5 class=empty>${{tab==='latest'?'No roles were added in the latest run.':tab==='open'?'No roles match these filters.':'Nothing here yet. Use the buttons on a role to add it.'}}</td></tr>`;
  $('cnt').textContent=list.length+' shown'}}
$('loc').onclick=e=>{{if(e.target.dataset.c!==undefined){{loc=e.target.dataset.c;save('loc',loc);draw()}}}};
$('tabs').onclick=e=>{{if(e.target.dataset.t){{tab=e.target.dataset.t;save('tab',tab);draw()}}}};
tb.onclick=e=>{{const b=e.target.closest('button[data-s]');if(b)return mark(b.dataset.id,b.dataset.s);
  const g=e.target.closest('tr.grp');if(g){{const c=g.dataset.c;open_.has(c)?open_.delete(c):open_.add(c);draw()}}}};
$('exp').onclick=()=>{{const all=[...tb.querySelectorAll('tr.grp')].map(r=>r.dataset.c);
  const anyClosed=all.some(c=>!open_.has(c));all.forEach(c=>anyClosed?open_.add(c):open_.delete(c));draw()}};
q.oninput=draw;co.onchange=draw;draw();
</script>
</body></html>""")
    return new_count


ON_MAC = sys.platform == "darwin"  # on GitHub's servers: no notifications, no opening a browser


def notify(msg, subtitle=""):
    if not ON_MAC:
        return
    esc = lambda t: t.replace("\\", "").replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{esc(msg)}" with title "Job Screener" '
                                       f'subtitle "{esc(subtitle)}" sound name "Glass"'])


def main():
    if "--render" in sys.argv:  # rebuild jobs.html from the last run without fetching
        cur = json.loads(CURRENT_FILE.read_text())
        render(cur["jobs"], json.loads(SEEN_FILE.read_text()), {k: tuple(v) for k, v in cur["status"].items()},
               set(cur.get("latest", [])), cur.get("latest_at", ""))
        return
    started = time.time()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(run_source, sources()))
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    today = date.today().isoformat()
    status, current, new_jobs = {}, [], []
    for name, jobs, err in results:
        status[name] = (len(jobs), err)
        for j in jobs:
            if j["id"] in seen:
                j["years"] = seen[j["id"]].get("years")
            else:
                j["years"] = min_years(j.pop("desc", None) or "")
            j.pop("desc", None)
            if j["years"] is not None and j["years"] > MAX_YEARS:
                continue
            if j["id"] not in seen:
                seen[j["id"]] = {"first_seen": today, "years": j["years"], "title": j["title"]}
                new_jobs.append(j)
            seen[j["id"]]["last_seen"] = today
            current.append(j)
    SEEN_FILE.write_text(json.dumps(seen, indent=1))
    first_run = len(seen) == len(new_jobs)
    # "Just added" keeps the last run that found something, so the tab isn't emptied by a quiet run
    prev = json.loads(CURRENT_FILE.read_text()) if CURRENT_FILE.exists() else {}
    if new_jobs and not first_run:
        latest, latest_at = [j["id"] for j in new_jobs], datetime.now().strftime("%d %b, %I:%M %p")
    else:
        latest, latest_at = prev.get("latest", []), prev.get("latest_at", "")
    CURRENT_FILE.write_text(json.dumps({"jobs": current, "status": status, "latest": latest,
                                        "latest_at": latest_at}, indent=1))
    new_count = render(current, seen, status, set(latest), latest_at)
    log = [f"Run {datetime.now():%Y-%m-%d %H:%M} took {time.time() - started:.0f}s; "
           f"{len(current)} matches, {len(new_jobs)} new this run"]
    log += [f"  {n}: {c} jobs" + (f"  ERROR {e.splitlines()[0]}" if e else "") for n, (c, e) in sorted(status.items())]
    LOG_FILE.write_text("\n".join(log) + "\n")
    print("\n".join(log))
    if new_jobs and not first_run:
        lines = [f"{j['company']}: {j['title']}" for j in new_jobs[:3]]
        more = f" (+{len(new_jobs) - 3} more)" if len(new_jobs) > 3 else ""
        notify("\n".join(lines) + more, f"{len(new_jobs)} new role(s) added")
        if OPEN_PAGE_WHEN_NEW and ON_MAC:  # opens straight on the Just added tab
            subprocess.run(["open", REPORT_FILE.as_uri() + "#new"])
    elif first_run:
        notify(f"First run done: {len(current)} matching roles found")


if __name__ == "__main__":
    sys.exit(main())
