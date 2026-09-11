#!/usr/bin/env python3
"""
Neonephos contributor export.

Writes one CSV row per contributor per project per time window, with the person's
name, GitHub account, the organization they contributed under, and the aggregated
(canonicalised) organization.

  python3 neonephos-contributors.py --all-windows -o contributors.csv
  python3 neonephos-contributors.py --since 2026-01-01 --until 2026-09-10 \
          --projects luigi,ironcore -o q3.csv

Sources
  * LFX Insights contributor leaderboard  -> person list + contribution counts
    (6 of 7 projects; same source and windows as the health report)
  * git history of every project repo     -> e-mail domain, commit counts,
    and the whole contributor list for Garden Linux (not tracked by LFX)
  * GitHub API                            -> login for a git identity, profile company

Organization is resolved per person, in this order:
  1. corporate e-mail domain in any of their commits          (org_source=email-domain)
  2. same, via a git identity with the same display name      (org_source=email-domain-name)
  3. corporate address they use elsewhere on GitHub           (org_source=commit-search)
  4. GitHub profile company field                             (org_source=github-company)
  5. public membership of a company GitHub org                (org_source=github-org)
  6. none of those -> "Individual"                            (org_source=individual)
Rows for the same person in the same project are then harmonised, so a second identity
that resolved to nothing inherits the company (org_source=same-person).
A domain only one person uses that reads like their own name (franzheidl.de) is kept in
the organization column but aggregates to "Individual" (org_source=personal-domain).

Requires: git, gh (authenticated). Clones are cached under --repos-dir,
GitHub lookups under --cache, so re-runs are cheap.
"""

import argparse, collections, csv, json, os, re, subprocess, sys, time
import unicodedata, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# clones and API lookups are cached outside the iCloud folder on purpose
CACHE = os.path.expanduser("~/.cache/neonephos-kpis")
LFX = "https://insights.linuxfoundation.org/api"

# key, display name, LFX slug (None = not in LFX), GitHub org(s) to read repos from
PROJECTS = [
    ("luigi",         "Luigi",                "luigi",                ["luigi-project"]),
    ("ironcore",      "IronCore",             "ironcore",             ["ironcore-dev"]),
    ("ocm",           "Open Component Model", "open-component-model", ["open-component-model"]),
    ("greenhouse",    "Greenhouse",           "greenhouse",           ["cloudoperators"]),
    ("gardenlinux",   "Garden Linux",         None,                   ["gardenlinux"]),
    ("platform-mesh", "Platform Mesh",        "platform-mesh",        ["platform-mesh"]),
    ("chantico",      "Chantico",             "chantico",             ["chantico-project"]),
]

WINDOWS = {                      # same windows as the project-health report
    "all": (None, None),
    "m12": ("2025-09-10", "2026-09-10"),
    "m3":  ("2026-06-10", "2026-09-10"),
}

BOT = re.compile(r"(\[bot\]|^dependabot|^renovate|github-action|actions-user|"
                 r"semantic-release|releasebot|^snyk-bot|^copilot)", re.I)
NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$")

FREE_MAIL = {
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.com", "yahoo.de", "yahoo.com",
    "protonmail.com", "proton.me", "pm.me", "posteo.de", "web.de", "hotmail.com", "hotmail.de",
    "outlook.com", "outlook.de", "outlook.es", "icloud.com", "me.com", "mac.com", "live.com",
    "aol.com", "mailbox.org", "fastmail.com", "zoho.com", "yandex.ru", "mail.ru", "qq.com",
    "163.com", "126.com", "seznam.cz", "t-online.de", "freenet.de", "arcor.de", "example.com",
    "localhost", "github.com", "users.noreply.github.com", "noreply.github.com",
}

# e-mail domain -> organization as it should read in the CSV
DOMAIN_ORG = {
    "sap.com": "SAP", "sapns2.com": "SAP", "sap.corp": "SAP", "exchange.sap.corp": "SAP",
    "b1-systems.de": "B1 Systems", "credativ.de": "credativ", "credativ.com": "credativ",
    "tno.nl": "TNO", "cst-bg.net": "CST", "kubermatic.com": "Kubermatic",
    "t-systems.com": "T-Systems", "telekom.de": "Deutsche Telekom",
    "canonical.com": "Canonical", "ubuntu.com": "Canonical", "netapp.com": "NetApp",
    "weave.works": "Weaveworks", "accenture.com": "Accenture", "suse.com": "SUSE",
    "suse.de": "SUSE", "vmware.com": "VMware", "clyso.com": "Clyso", "akendo.eu": "Akendo",
    "gardenlinux.io": "Garden Linux", "lovoo.com": "LOVOO", "mphasis.com": "Mphasis",
    "redhat.com": "Red Hat", "google.com": "Google", "microsoft.com": "Microsoft",
    "ibm.com": "IBM", "intel.com": "Intel", "nvidia.com": "NVIDIA", "amazon.com": "Amazon",
    "aws.amazon.com": "Amazon", "box.com": "Box", "coresystems.net": "Coresystems",
    "siemens.com": "Siemens", "bosch.com": "Bosch", "zalando.de": "Zalando",
    "kubeforge.io": "KubeForge", "gridscale.io": "gridscale", "stackit.cloud": "STACKIT",
    "schwarz-it.com": "Schwarz IT", "eviden.com": "Eviden", "atos.net": "Atos",
    "hansenpartnership.com": "Hansen Partnership", "linuxfoundation.org": "Linux Foundation",
}

# public GitHub org membership -> employer. Project orgs (cloudoperators, gardener,
# ironcore-dev, ...) are deliberately absent: they say what someone works on, not for whom.
GH_ORG_COMPANY = {
    "sap": "SAP", "sap-samples": "SAP", "sap-archive": "SAP", "sap-linuxlab": "SAP",
    "sapmentors": "SAP", "sapcc": "SAP", "sap-cloud-infrastructure": "SAP",
    "cobaltcore-dev": "SAP", "sap-contributions": "SAP",
    "b1-systems": "B1 Systems", "kubermatic": "Kubermatic", "kubermatic-labs": "Kubermatic",
    "canonical": "Canonical", "credativ": "credativ", "grip-on-software": "Grip on Software",
    "tno": "TNO", "suse": "SUSE", "redhat": "Red Hat", "microsoft": "Microsoft",
    "google": "Google", "netapp": "NetApp", "clyso": "Clyso", "lovoo": "LOVOO",
    "telekom": "Deutsche Telekom", "telekom-mms": "Deutsche Telekom", "t-systems": "T-Systems",
    "mphasis": "Mphasis", "stackitcloud": "STACKIT", "weaveworks": "Weaveworks",
}

# families that should collapse into one aggregated organization
AGG_RULES = [
    (re.compile(r"\bsap\b|sap se|sap labs|sap signavio|@sap", re.I), "SAP"),
    (re.compile(r"t-systems|deutsche telekom|telekom", re.I),        "Deutsche Telekom"),
    (re.compile(r"canonical|ubuntu", re.I),                          "Canonical"),
    (re.compile(r"\bsuse\b", re.I),                                  "SUSE"),
    (re.compile(r"red ?hat", re.I),                                  "Red Hat"),
    (re.compile(r"kubermatic", re.I),                                "Kubermatic"),
    (re.compile(r"b1[- ]systems", re.I),                             "B1 Systems"),
    (re.compile(r"credativ", re.I),                                  "credativ"),
    (re.compile(r"mphasis", re.I),                                   "Mphasis"),
    (re.compile(r"\btno\b", re.I),                                   "TNO"),
    (re.compile(r"microsoft|github", re.I),                          "Microsoft"),
    (re.compile(r"\bgoogle\b|alphabet", re.I),                       "Google"),
    (re.compile(r"amazon|\baws\b", re.I),                            "Amazon"),
    (re.compile(r"\bibm\b|red ?hat", re.I),                          "IBM"),
    (re.compile(r"freelance|freiberuflich|self[- ]employed|independent|"
                r"selbständig|selbstaendig", re.I),                  "Freelance"),
]
LEGAL = re.compile(r"\b(se|ag|gmbh|mbh|inc|incorporated|ltd|limited|llc|l\.l\.c|bv|b\.v|nv|"
                   r"co|corp|corporation|kg|kgaa|s\.a|sa|sas|srl|spa|ug|plc|oy|ab|as|aps|"
                   r"pvt|pte|bfl)\b\.?", re.I)


# ---------------------------------------------------------------- helpers

def log(*a):
    print(*a, file=sys.stderr)


def lfx_get(path, **params):
    params = {k: v for k, v in params.items() if v is not None}
    url = LFX + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "neonephos-contributors/1.0"})
    err = None
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:                                    # noqa: BLE001
            err = e
            time.sleep(2)
    log(f"  !! LFX {path} {params} -> {err}")
    return None


def gh_graphql(query):
    r = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"],
                       capture_output=True, text=True)
    if r.returncode:
        log("  !! graphql:", r.stderr.strip()[:200])
        return {}
    return json.loads(r.stdout).get("data") or {}


def gh_repos(org):
    """Repos an org owns itself — forks are skipped, they carry upstream history."""
    r = subprocess.run(["gh", "repo", "list", org, "--limit", "1000", "--no-archived",
                        "--source", "--json", "nameWithOwner", "--jq", ".[].nameWithOwner"],
                       capture_output=True, text=True)
    return [x for x in r.stdout.split() if x]


# ---------------------------------------------------------------- repo cache

def ensure_repos(repos_dir, keys, refresh=False):
    """Bare-clone (blobless) every repo of every selected project. Idempotent.

    Returns project key -> set of clone directory names, so that repos which are
    cached but no longer in scope (forks, removed repos) stay out of the numbers.
    """
    os.makedirs(repos_dir, exist_ok=True)
    listing_p = os.path.join(repos_dir, "repos.json")
    listing = json.load(open(listing_p)) if os.path.exists(listing_p) else {}
    have = set(os.listdir(repos_dir))
    for key, _name, slug, orgs in PROJECTS:
        if key not in keys:
            continue
        if key in listing and not refresh:
            continue
        wanted = []
        if slug:
            proj = lfx_get(f"/project/{slug}")
            for r in (proj or {}).get("repositories") or []:
                m = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", r.get("url") or "")
                if m:
                    wanted.append(m.group(1))
        if not wanted:
            for org in orgs:
                wanted += gh_repos(org)
        missing = [w for w in wanted if f"{key}__{w.split('/')[1]}.git" not in have]
        if missing:
            log(f"  cloning {len(missing)} repo(s) for {key}")
        for w in missing:
            dest = os.path.join(repos_dir, f"{key}__{w.split('/')[1]}.git")
            subprocess.run(["git", "clone", "--bare", "--filter=blob:none", "-q",
                            f"https://github.com/{w}.git", dest],
                           capture_output=True, text=True)
        listing[key] = sorted(f"{key}__{w.split('/')[1]}.git" for w in wanted)
        json.dump(listing, open(listing_p, "w"), indent=1)
    return {k: set(v) for k, v in listing.items()}


def git_index(repos_dir, keys, since, until, in_scope):
    """project key -> e-mail -> {names, commits, first, last, sample commit}."""
    idx = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"names": collections.Counter(), "commits": 0,
                 "first": None, "last": None, "sample": None}))
    for d in sorted(os.listdir(repos_dir)):
        if not d.endswith(".git"):
            continue
        key = d.split("__")[0]
        if key not in keys or d not in in_scope.get(key, set()):
            continue
        path = os.path.join(repos_dir, d)
        url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"github\.com[/:]([^/]+)/(.+?)(?:\.git)?$", url)
        owner, repo = (m.group(1), m.group(2)) if m else (None, None)
        cmd = ["git", "-C", path, "log", "--no-merges",
               "--pretty=%H%x09%an%x09%ae%x09%ad", "--date=short"]
        if since:
            cmd.append(f"--since={since}")
        if until:
            cmd.append(f"--until={until}")
        for line in subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines():
            p = line.split("\t")
            if len(p) != 4:
                continue
            sha, name, email, date = p
            email = email.strip().lower()
            if not email or BOT.search(email) or BOT.search(name):
                continue
            e = idx[key][email]
            e["names"][name] += 1
            e["commits"] += 1
            e["first"] = date if e["first"] is None else min(e["first"], date)
            e["last"] = date if e["last"] is None else max(e["last"], date)
            if e["sample"] is None and owner:
                e["sample"] = {"sha": sha, "owner": owner, "repo": repo}
    return idx


# ---------------------------------------------------------------- GitHub cache

class GitHub:
    def __init__(self, path, search=True):
        self.path = path
        self.search = search
        self._last_search = 0.0
        self.d = json.load(open(path)) if os.path.exists(path) else {"email2login": {}, "user": {}}

    def save(self):
        json.dump(self.d, open(self.path, "w"))

    def resolve_emails(self, samples):
        """samples: e-mail -> {sha, owner, repo}. Fills email -> login."""
        todo = []
        for email, s in samples.items():
            if email in self.d["email2login"]:
                continue
            m = NOREPLY.match(email)
            if m:
                self.d["email2login"][email] = m.group(1)
            elif s:
                todo.append(email)
            else:
                self.d["email2login"][email] = None
        if todo:
            log(f"  resolving {len(todo)} git identities via GitHub")
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            q = " ".join(
                f'a{j}: repository(owner:"{samples[e]["owner"]}",name:"{samples[e]["repo"]}")'
                f'{{object(oid:"{samples[e]["sha"]}"){{... on Commit{{author{{user{{login}}}}}}}}}}'
                for j, e in enumerate(chunk))
            data = gh_graphql("query{" + q + "}")
            for j, e in enumerate(chunk):
                node = ((data.get(f"a{j}") or {}).get("object") or {})
                self.d["email2login"][e] = ((node.get("author") or {}).get("user") or {}).get("login")
            self.save()
        self.save()

    def profiles(self, logins):
        def stale(l):
            u = self.d["user"].get(l)
            return u is None or "organizations" not in u
        todo = sorted({l for l in logins if l and stale(l)})
        if todo:
            log(f"  fetching {len(todo)} GitHub profiles")
        for i in range(0, len(todo), 50):
            chunk = todo[i:i + 50]
            q = " ".join(f'u{j}: user(login:"{l}"){{login name company location '
                         f'organizations(first:30){{nodes{{login}}}}}}'
                         for j, l in enumerate(chunk))
            data = gh_graphql("query{" + q + "}")
            for j, l in enumerate(chunk):
                self.d["user"][l] = data.get(f"u{j}") or {"login": l}
            self.save()
        self.save()

    def login_of(self, email):
        return self.d["email2login"].get(email)

    def company_of(self, login):
        u = self.d["user"].get(login) or {}
        return (u.get("company") or "").strip()

    def name_of(self, login):
        u = self.d["user"].get(login) or {}
        return (u.get("name") or "").strip()

    def emails_of(self, login):
        """E-mail addresses this account has authored commits with, anywhere on GitHub.

        Fills the gap left by people who commit to these projects under a
        users.noreply.github.com address. Search is rate limited to 30 calls a
        minute, hence the spacing and the cache.
        """
        if not self.search:
            return {}
        box = self.d.setdefault("login2emails", {})
        if login in box:
            return box[login]
        wait = 2.2 - (time.time() - self._last_search)
        if wait > 0:
            time.sleep(wait)
        self._last_search = time.time()
        r = subprocess.run(["gh", "api", "-X", "GET", "search/commits",
                            "-f", f"q=author:{login}", "-f", "per_page=100",
                            "--jq", "[.items[].commit.author.email]"],
                           capture_output=True, text=True)
        found = collections.Counter()
        if r.returncode == 0 and r.stdout.strip():
            for e in json.loads(r.stdout):
                if e:
                    found[e.strip().lower()] += 1
        box[login] = dict(found)
        self.save()
        return box[login]

    def orgs_of(self, login):
        u = self.d["user"].get(login) or {}
        return [(o or {}).get("login", "").lower()
                for o in ((u.get("organizations") or {}).get("nodes") or [])]


# ---------------------------------------------------------------- org logic

def org_from_domain(domain):
    if not domain or domain in FREE_MAIL:
        return None
    if domain in DOMAIN_ORG:
        return DOMAIN_ORG[domain]
    sld = domain.split(".")
    base = sld[-2] if len(sld) >= 2 else domain
    for i in range(len(sld) - 2, -1, -1):          # skip co.uk / com.br style suffixes
        if sld[i] not in ("co", "com", "org", "net", "gov", "ac", "edu"):
            base = sld[i]
            break
    return base.replace("-", " ").title()


def clean_company(c):
    c = (c or "").strip().lstrip("@").strip()
    c = re.sub(r"\s+", " ", c)
    return c if c and len(c) < 60 else ""


def aggregate(org, source=""):
    """Canonical organization name — collapses SAP SE / @SAP / sap.com into one."""
    if not org or source in ("individual", "personal-domain"):
        return "Individual"
    for pat, name in AGG_RULES:
        if pat.search(org):
            return name
    n = org.lstrip("@").strip().strip(",.")
    n = LEGAL.sub("", n)
    n = re.sub(r"[.,]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n or org


def corporate(domain):
    return bool(org_from_domain(domain))


def personal_domain(domain, person):
    """b1-systems.de is a company, franzheidl.de is a person with a domain."""
    sld = org_from_domain(domain) or ""
    a = norm_name(sld)
    b = norm_name(person)
    return bool(a) and bool(b) and (a in b or b in a)


def resolve_org(evidence, person):
    """evidence: {'domains': Counter, 'named_domains': Counter, 'company': str,
    'gh_orgs': [str]} — everything known about this person, across all projects."""
    for src, doms in (("email-domain", evidence["domains"]),
                      ("email-domain-name", evidence["named_domains"]),
                      ("commit-search", evidence["searched_domains"])):
        for dom, _n in doms.most_common():
            o = org_from_domain(dom)
            if not o:
                continue
            if personal_domain(dom, person):
                return o, "personal-domain", dom
            return o, src, dom
    seen = evidence["domains"].most_common(1)
    dom = seen[0][0] if seen else ""
    c = clean_company(evidence["company"])
    if c:
        return c, "github-company", dom
    for o in evidence["gh_orgs"]:
        if o in GH_ORG_COMPANY:
            return GH_ORG_COMPANY[o], "github-org", dom
    return "Individual", "individual", dom


def norm_name(n):
    """Kurajský and Kurajsky have to land in the same bucket."""
    n = unicodedata.normalize("NFKD", n or "")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", n.lower())


def harmonise(rows):
    """One person, one organization.

    LFX lists somebody twice when their git identity and their GitHub account were
    never linked, and people show up under two accounts. Where one of those rows
    resolved to a company and the other did not, the company wins for both.
    """
    best = {}
    for r in rows:
        if r["organization_aggregated"] == "Individual":
            continue
        k = (r["project"], norm_name(r["person"]))
        cur = best.get(k)
        if cur is None or float(r["contributions"] or 0) > float(cur["contributions"] or 0):
            best[k] = r
    fixed = 0
    for r in rows:
        if r["organization_aggregated"] != "Individual":
            continue
        m = best.get((r["project"], norm_name(r["person"])))
        if not m:
            continue
        r["organization"] = m["organization"]
        r["organization_aggregated"] = m["organization_aggregated"]
        r["org_source"] = "same-person"
        r["email_domain"] = m["email_domain"]
        fixed += 1
    return fixed


# ---------------------------------------------------------------- build rows

def person_evidence(gidx_all, gh):
    """Everything we know about each person, merged across all projects and all time.

    Affiliation is a property of the person, not of one repo: somebody who commits to
    IronCore from sap.com and to Greenhouse from a noreply address is the same SAP
    employee in both. Keyed by GitHub login where we have one, by normalised display
    name otherwise; the name index is also kept as a fallback for LFX contributor rows
    that carry no handle.
    """
    by_login = collections.defaultdict(lambda: {"domains": collections.Counter(), "names": collections.Counter()})
    by_name  = collections.defaultdict(lambda: {"domains": collections.Counter(), "names": collections.Counter()})
    for _key, emails in gidx_all.items():
        for email, e in emails.items():
            dom = email.split("@")[-1]
            login = gh.login_of(email)
            name = e["names"].most_common(1)[0][0] if e["names"] else ""
            if login:
                by_login[login]["domains"][dom] += e["commits"]
                by_login[login]["names"][name] += e["commits"]
            if name:
                by_name[norm_name(name)]["domains"][dom] += e["commits"]
                by_name[norm_name(name)]["names"][name] += e["commits"]
    return by_login, by_name


def window_stats(gidx_win, gh):
    """project key -> (login -> git facts, normalised name -> git facts) for one window."""
    out = {}
    for key, emails in gidx_win.items():
        by_login, by_name = {}, {}
        for email, e in emails.items():
            login = gh.login_of(email)
            name = e["names"].most_common(1)[0][0] if e["names"] else ""
            for bucket in ([by_login.setdefault(login, {})] if login else []) + \
                          ([by_name.setdefault(norm_name(name), {})] if name else []):
                bucket["commits"] = bucket.get("commits", 0) + e["commits"]
                bucket["first"] = min(x for x in (bucket.get("first"), e["first"]) if x)
                bucket["last"] = max(x for x in (bucket.get("last"), e["last"]) if x)
        out[key] = (by_login, by_name)
    return out


def build(keys, wname, since, until, gidx_win, ev_login, ev_name, gh):
    stats = window_stats(gidx_win, gh)
    rows = []
    for key, name, slug, _orgs in PROJECTS:
        if key not in keys:
            continue
        by_login, by_name = stats.get(key, ({}, {}))
        people = []                                   # (person, login, contributions, pct, roles)
        if slug:
            d = lfx_get("/widget/contributors/contributor-leaderboard",
                        project=slug, limit=5000, startDate=since, endDate=until)
            for c in (d or {}).get("data", []):
                if BOT.search(c.get("name") or ""):
                    continue
                handles = c.get("githubHandleArray") or []
                people.append((c.get("name") or "", handles[0] if handles else "",
                               c.get("contributions"), c.get("percentage"),
                               "|".join(c.get("roles") or [])))
        else:                                          # Garden Linux: git is the only source
            seen, total = {}, 0
            for email, e in gidx_win.get(key, {}).items():
                login = gh.login_of(email)
                pname = e["names"].most_common(1)[0][0] if e["names"] else email
                k = login or norm_name(pname)
                p = seen.setdefault(k, {"name": pname, "login": login or "", "commits": 0})
                p["commits"] += e["commits"]
                total += e["commits"]
            for p in sorted(seen.values(), key=lambda x: -x["commits"]):
                people.append((p["name"], p["login"], p["commits"],
                               round(100 * p["commits"] / total, 2) if total else 0, ""))

        gh.profiles([p[1] for p in people if p[1]])

        for person, login, contribs, pct, roles in people:
            nkey = norm_name(person)
            ev = ev_login.get(login) if login else None
            named = ev_name.get(nkey) or ev_name.get(norm_name(gh.name_of(login))) or {}
            evidence = {
                "domains": (ev or {}).get("domains", collections.Counter()),
                "named_domains": named.get("domains", collections.Counter()),
                "searched_domains": collections.Counter(),
                "company": gh.company_of(login) if login else "",
                "gh_orgs": gh.orgs_of(login) if login else [],
            }
            if login and not any(corporate(d) for d in
                                 list(evidence["domains"]) + list(evidence["named_domains"])):
                for e, n in gh.emails_of(login).items():
                    evidence["searched_domains"][e.split("@")[-1]] += n
            org, src, dom = resolve_org(evidence, person or gh.name_of(login))
            g = by_login.get(login) if login else None
            if g is None:
                g = by_name.get(nkey) or by_name.get(norm_name(gh.name_of(login))) or {}
            rows.append({
                "project": name,
                "window": wname,
                "window_start": since or "",
                "window_end": until or "",
                "person": person or gh.name_of(login) or login,
                "github_login": login,
                "organization": org,
                "organization_aggregated": aggregate(org, src),
                "org_source": src,
                "contributions": contribs if contribs is not None else "",
                "contribution_pct": pct if pct is not None else "",
                "roles": roles,
                "email_domain": dom,
                "github_company": clean_company(gh.company_of(login) if login else ""),
                "git_commits": g.get("commits", 0),
                "first_commit": g.get("first") or "",
                "last_commit": g.get("last") or "",
                "source": "lfx" if slug else "git",
            })
    return rows


FIELDS = ["project", "window", "window_start", "window_end", "person", "github_login",
          "organization", "organization_aggregated", "org_source", "contributions",
          "contribution_pct", "roles", "email_domain", "github_company", "git_commits",
          "first_commit", "last_commit", "source"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="YYYY-MM-DD (omit for all time)")
    ap.add_argument("--until", help="YYYY-MM-DD")
    ap.add_argument("--window", choices=sorted(WINDOWS), help="preset window from the report")
    ap.add_argument("--all-windows", action="store_true", help="all three report windows")
    ap.add_argument("--projects", help="comma-separated keys (default: all)",
                    default=",".join(p[0] for p in PROJECTS))
    ap.add_argument("--repos-dir", default=os.path.join(CACHE, "repos"))
    ap.add_argument("--cache", default=os.path.join(CACHE, "github.json"))
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "neonephos-contributors.csv"))
    ap.add_argument("--json", help="also write rows as JSON (used by the HTML report)")
    ap.add_argument("--no-commit-search", action="store_true",
                    help="skip the GitHub commit search that finds a person's work address "
                         "when they commit under a noreply address (faster, less complete)")
    ap.add_argument("--refresh-repo-list", action="store_true",
                    help="re-query which repos each project owns (cached otherwise)")
    a = ap.parse_args()

    keys = {k.strip() for k in a.projects.split(",") if k.strip()}
    unknown = keys - {p[0] for p in PROJECTS}
    if unknown:
        ap.error(f"unknown project(s): {', '.join(sorted(unknown))}")

    if a.all_windows:
        windows = [(k, *WINDOWS[k]) for k in ("all", "m12", "m3")]
    elif a.window:
        windows = [(a.window, *WINDOWS[a.window])]
    else:
        label = f"{a.since or 'start'}..{a.until or 'today'}"
        windows = [(label, a.since, a.until)]

    os.makedirs(os.path.dirname(a.cache), exist_ok=True)
    in_scope = ensure_repos(a.repos_dir, keys, refresh=a.refresh_repo_list)
    gh = GitHub(a.cache, search=not a.no_commit_search)

    log("· reading git history")
    gidx_all = git_index(a.repos_dir, keys, None, None, in_scope)
    samples = {}
    for key in gidx_all:
        for email, e in gidx_all[key].items():
            samples.setdefault(email, e["sample"])
    gh.resolve_emails(samples)
    gh.profiles({gh.login_of(e) for e in samples})
    ev_login, ev_name = person_evidence(gidx_all, gh)

    rows = []
    for wname, since, until in windows:
        log(f"· window {wname} ({since or 'start'} → {until or 'today'})")
        gidx_win = (gidx_all if (since, until) == (None, None)
                    else git_index(a.repos_dir, keys, since, until, in_scope))
        rows += build(keys, wname, since, until, gidx_win, ev_login, ev_name, gh)

    fixed = harmonise(rows)
    if fixed:
        log(f"  harmonised {fixed} second identities onto their person's organization")

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    log(f"\nwrote {a.out} — {len(rows)} rows")
    if a.json:
        json.dump(rows, open(a.json, "w"))
        log(f"wrote {a.json}")

    for wname, _s, _u in windows:
        sub = [r for r in rows if r["window"] == wname]
        agg = collections.Counter()
        for r in sub:
            agg[r["organization_aggregated"]] += 1
        top = ", ".join(f"{k} {v}" for k, v in agg.most_common(4))
        log(f"  {wname:>5}: {len(sub):4} people · top orgs: {top}")


if __name__ == "__main__":
    main()
