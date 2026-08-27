"""Turn messy India field-sales cells into one email, one phone, one name.

These workbooks are not CRM exports. One cell holds work mail, gmail, the words
"Personal email", two phone numbers, and a LinkedIn URL inside the name.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

PERSONAL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "yahoo.co.uk",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "rediffmail.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
    }
)

DNR_RE = re.compile(
    r"\bDNR\b|do not (contact|reach|call|email|disturb)|unsubscribe|"
    r"wrong person|no longer (with (the )?company|handling)|left the company|"
    r"not the right person|does not handle",
    re.I,
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
LI_RE = re.compile(r"(https?://)?([a-z]+\.)?linkedin\.com/(in|pub)/[A-Za-z0-9_\-%]+/?", re.I)
PHONE_CHUNK_RE = re.compile(r"(\+?\d[\d\-\s().]{6,}\d)")
NAME_TITLE_RE = re.compile(r"^\s*(.+?)\s*\(([^)]+)\)\s*$")
PAIR_RE = re.compile(r"([^,()]+?)\s*\(([^)]+)\)")
PINCODE_RE = re.compile(r"\b\d{6}\b")
ADDRESS_HINT = re.compile(
    r"\b(road|rd\.?|street|st\.?|nagar|layout|hosur|adugodi|bengaluru|bangalore|"
    r"mumbai|hyderabad|chennai|karnataka|maharashtra|telangana|pincode|sector)\b",
    re.I,
)
NOTE_HINT = re.compile(r"^(talk about|follow ?up|call |visit |notes?:)", re.I)

_BLANK = {"", "nan", "none", "null", "n/a", "na", "-", "--", "#n/a"}


def text(value: object) -> str:
    if value is None:
        return ""
    s = str(value).replace("\xa0", " ").strip()
    if s.lower() in _BLANK:
        return ""
    return s


def extract_emails(value: object) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in EMAIL_RE.finditer(text(value).replace("\n", " ")):
        addr = match.group(0).lower().rstrip(".,;:")
        if addr.endswith("."):
            continue
        if addr not in seen:
            seen.add(addr)
            found.append(addr)
    return found


def company_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def email_domain(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1].lower()


def pick_email(emails: list[str], *, company_name: str = "", company_domain: str = "") -> str:
    if not emails:
        return ""
    slug = company_slug(company_name)
    want = (company_domain or "").lower().removeprefix("www.")

    def score(addr: str) -> int:
        domain = email_domain(addr)
        root = domain.split(".")[0]
        s = 0
        if domain not in PERSONAL_DOMAINS:
            s += 40
        else:
            s -= 5
        if want and (domain == want or domain.endswith("." + want)):
            s += 50
        if slug and root and (root in slug or slug.startswith(root) or root.startswith(slug[:5])):
            s += 35
        return s

    ranked = sorted(emails, key=lambda a: (-score(a), emails.index(a)))
    return ranked[0]


def extract_phones(value: object) -> str:
    """First usable phone. Indian 10-digit numbers become +91."""
    blob = text(value)
    if not blob:
        return ""
    if "linkedin.com" in blob.lower():
        return ""
    blob = re.sub(r"(\d)\.0\b", r"\1", blob)
    blob = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", " ", blob)
    for chunk in PHONE_CHUNK_RE.findall(blob):
        digits = re.sub(r"\D", "", chunk)
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 11 and digits[0] in "6789":
            ten = digits[:10]
            if ten[0] in "6789":
                return "+91" + ten
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        if digits.startswith("91") and len(digits) >= 12:
            rest = digits[2:]
            if len(rest) == 10 and rest[0] in "6789":
                return "+91" + rest
        if chunk.strip().startswith("+") and 10 <= len(digits) <= 15:
            return "+" + digits
    return ""


def extract_linkedin(value: object) -> str:
    m = LI_RE.search(text(value))
    if not m:
        return ""
    url = m.group(0)
    if not url.lower().startswith("http"):
        url = "https://" + url
    url = url.split("?")[0].rstrip("/).,;").split(",")[0].split(";")[0]
    if not url.lower().startswith("http"):
        url = "https://" + url
    handle = linkedin_handle(url)
    if handle:
        slug = handle.split("/")[0].strip().lower()
        if slug:
            return "https://www.linkedin.com/in/" + slug
    return url.rstrip("/")


def looks_like_address(value: object) -> bool:
    raw = text(value)
    if not raw:
        return False
    if PINCODE_RE.search(raw) and ADDRESS_HINT.search(raw):
        return True
    if raw.count(",") >= 2 and ADDRESS_HINT.search(raw):
        return True
    return False


def looks_like_note(value: object) -> bool:
    raw = text(value)
    if not raw:
        return False
    if NOTE_HINT.search(raw):
        return True
    if len(raw) > 80 and "@" not in raw and "linkedin.com" not in raw.lower():
        return True
    if (
        raw.endswith((".", "!"))
        and raw.count(" ") >= 3
        and not re.search(r"\b(Ltd|Limited|Pvt|Inc|LLP|Bank|Technologies|Solutions|Private)\b", raw, re.I)
    ):
        return True
    return False


def clean_company(value: object) -> str:
    raw = text(value)
    if not raw:
        return ""
    if looks_like_address(raw) or looks_like_note(raw):
        return ""
    if raw.startswith("("):
        return ""
    if re.search(
        r"\b(currently|already working|told me|sent a |call me|follow ?up call|"
        r"crowdstrike|partners already|bought s\d|closed it|work on this)\b",
        raw,
        re.I,
    ):
        return ""
    raw = re.sub(r"\s+", " ", raw).strip(" -")
    if len(raw) < 2:
        return ""
    return raw


_LEGAL_RE = re.compile(
    r",?\s*\b(private limited|pvt\.?\s*ltd\.?|limited|ltd\.?|llp|inc\.?|llc|plc|corp\.?|corporation)\b\.?",
    re.I,
)
_COMMON_CO = {"ace", "new", "the", "and", "india", "global", "tech", "digital", "red", "pro"}


def company_core(name: str) -> str:
    s = text(name)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = _LEGAL_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    return s.lower()


def _better_company_name(a: str, b: str) -> str:
    def score(n: str) -> tuple[int, int]:
        legal = 1 if _LEGAL_RE.search(n) else 0
        return (legal, len(n))

    return a if score(a) >= score(b) else b


def canonical_company_map(names: list[str]) -> dict[str, str]:
    """Map 'Aequs' and 'Aequs Limited' to one display name."""
    clean = [text(n) for n in names if text(n) and clean_company(n)]
    if not clean:
        return {}
    cores: dict[str, list[str]] = {}
    for n in clean:
        cores.setdefault(company_core(n) or n.lower(), []).append(n)

    keys = sorted(cores, key=len)
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for short in keys:
        if short in _COMMON_CO or len(short) < 4:
            continue
        for long in keys:
            if long == short:
                continue
            if long.startswith(short + " ") or (len(short) >= 5 and long.startswith(short)):
                parent[find(short)] = find(long)

    clusters: dict[str, list[str]] = {}
    for k, names_for in cores.items():
        clusters.setdefault(find(k), []).extend(names_for)

    canon: dict[str, str] = {}
    for members in clusters.values():
        best = members[0]
        for n in members[1:]:
            best = _better_company_name(best, n)
        for n in members:
            canon[n] = best
    return canon


def infer_company_from_email(email: str, known: list[str]) -> str:
    domain = email_domain(email)
    if not domain or domain in PERSONAL_DOMAINS:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "", domain.split(".")[0].lower())
    if len(slug) < 3:
        return ""
    scored: list[tuple[int, str]] = []
    for name in known:
        core = re.sub(r"[^a-z0-9]+", "", company_core(name))
        if not core:
            continue
        if slug == core or slug in core or core.startswith(slug):
            scored.append((len(name), name))
    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][1]


def parse_name_title(value: object) -> tuple[str, str]:
    """'Iranna Gadad (Assistant Manager IT)' -> name, title."""
    raw = text(value)
    if not raw:
        return "", ""
    raw = re.sub(r"\(\s*https?://[^)]+\)", " ", raw)
    raw = LI_RE.sub("", raw)
    raw = re.sub(r"\(\s*\)", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" -|,(")
    m = NAME_TITLE_RE.match(raw)
    if m:
        return m.group(1).strip(" -|,"), m.group(2).strip()
    return raw, ""


def split_contacts(value: object) -> list[tuple[str, str]]:
    """One cell, several people: 'Krish Srikant (VP IT), Sivakami Balan (GM)'."""
    raw = text(value)
    if not raw:
        return []
    pairs = PAIR_RE.findall(raw)
    if len(pairs) >= 2:
        return [(n.strip(" -|,"), t.strip()) for n, t in pairs if n.strip()]
    name, title = parse_name_title(raw)
    return [(name, title)] if name else []


def tidy_name_part(part: str) -> str:
    raw = text(part)
    if not raw:
        return ""
    bits = []
    for token in raw.replace("_", " ").split():
        if token.isupper() and len(token) <= 2:
            bits.append(token)
        elif token.lower() in {"de", "da", "van", "von", "al", "el"}:
            bits.append(token.lower())
        elif token.endswith("."):
            bits.append(token[0].upper() + token[1:].lower())
        else:
            bits.append(token[0].upper() + token[1:].lower())
    return " ".join(bits).strip(" (,")


def tidy_person_name(first: str, last: str) -> tuple[str, str]:
    return tidy_name_part(first), tidy_name_part(last)


def linkedin_handle(url: str) -> str:
    raw = (url or "").rstrip("/")
    if "/in/" in raw:
        return raw.split("/in/", 1)[1]
    if "/pub/" in raw:
        return raw.split("/pub/", 1)[1]
    return raw


def split_person_name(full: str) -> tuple[str, str]:
    full = text(full)
    if not full:
        return "", ""
    if "," in full and not full.lower().startswith("http"):
        last, first = [p.strip() for p in full.split(",", 1)]
        return first, last
    parts = full.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def is_dnr(*blobs: object) -> bool:
    joined = " ".join(text(b) for b in blobs if text(b))
    return bool(joined and DNR_RE.search(joined))


def remarks_of(row: dict) -> str:
    parts: list[str] = []
    for key, value in row.items():
        k = re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")
        if k in {"remarks", "remark", "notes", "note", "comment", "comments"} or k.startswith("unnamed"):
            t = text(value)
            if t:
                parts.append(t)
    return " | ".join(parts)


def domain_from(value: str) -> str:
    raw = text(value)
    if not raw:
        return ""
    if "@" in raw and "://" not in raw and " " not in raw:
        return raw.split("@", 1)[1].lower()
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower().removeprefix("www.")
    return host
