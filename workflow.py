"""Pure helpers for Resonance source identity, evidence grouping, and schedules."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TRACKING = {"fbclid", "gclid"}


def valid_timezone(value):
    if not value:
        return True
    try:
        ZoneInfo(value)
        return True
    except ZoneInfoNotFoundError:
        return False


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value, length=32):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def normalize_source(value):
    """Normalize only facts whose equivalence is explicit in the B1 contract."""
    if not value:
        return ""
    part = urlsplit(value)
    host = (part.hostname or "").lower()
    netloc = host
    if part.port is not None:
        netloc += ":" + str(part.port)
    query = []
    for component in part.query.split("&") if part.query else []:
        key = unquote_plus(component.split("=", 1)[0]).casefold()
        if key in TRACKING or key.startswith("utm_"):
            continue
        query.append(component)
    return urlunsplit((part.scheme.lower(), netloc, part.path or "/", "&".join(query), part.fragment))


def precise_source(value, platform="", kind="post"):
    """Return whether a URL identifies one mutable item rather than an account/history."""
    normalized = normalize_source(value)
    if not normalized:
        return False
    part = urlsplit(normalized)
    host = (part.hostname or "").lower()
    path = part.path.rstrip("/") or "/"
    query = dict(parse_qsl(part.query, keep_blank_values=True))
    platform = (platform or "").casefold()

    # Synthetic paths are explicitly useful only as isolated test fixtures.
    if host in {"example.com", "example.test"}:
        if kind == "post":
            return bool(re.fullmatch(r"/(?:posts?|items?)/[^/]+", path))
        return kind in {"comment", "reply"} and bool(re.fullmatch(r"/comments?/[^/]+", path))
    if platform == "youtube" and host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        if host == "youtu.be":
            return kind == "post" and bool(re.fullmatch(r"/[^/]+", path))
        return path == "/watch" and bool(query.get("v")) and ((kind == "post" and not query.get("lc")) or
                                                                 (kind in {"comment", "reply"} and bool(query.get("lc"))))
    if platform == "reddit" and (host == "reddit.com" or host.endswith(".reddit.com")):
        # The optional final comment component remains in the normalized key.
        match = re.fullmatch(r"/r/[^/]+/comments/[^/]+(?:/[^/]+)?(?:/[^/]+)?", path)
        if not match:
            return False
        parts = [value for value in path.split("/") if value]
        return len(parts) >= 6 if kind in {"comment", "reply"} else len(parts) <= 5
    if platform == "x" and host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return kind in {"post", "reply"} and bool(re.fullmatch(r"/[^/]+/status/[^/]+", path))
    if platform == "instagram" and host in {"instagram.com", "www.instagram.com"}:
        base = bool(re.fullmatch(r"/(?:p|reel|tv)/[^/]+", path))
        return base and ((kind == "post" and not query.get("comment_id")) or
                         (kind in {"comment", "reply"} and bool(query.get("comment_id"))))
    if platform == "tiktok" and host in {"tiktok.com", "www.tiktok.com"}:
        base = bool(re.fullmatch(r"/@[^/]+/video/[^/]+", path))
        return base and ((kind == "post" and not query.get("comment_id")) or
                         (kind in {"comment", "reply"} and bool(query.get("comment_id"))))
    if platform == "linkedin" and host in {"linkedin.com", "www.linkedin.com"}:
        base = bool(re.fullmatch(r"/(?:feed/update|posts)/.+", path))
        comment = query.get("commentUrn") or query.get("comment_urn")
        return base and ((kind == "post" and not comment) or (kind in {"comment", "reply"} and bool(comment)))
    if platform == "facebook" and host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        if path == "/permalink.php":
            base = bool(query.get("story_fbid") and query.get("id"))
            return base and ((kind == "post" and not query.get("comment_id")) or
                             (kind in {"comment", "reply"} and bool(query.get("comment_id"))))
        # comment_id is deliberately retained, so a comment never aliases its parent post.
        base = bool(re.fullmatch(r"/(?:[^/]+/posts|groups/[^/]+/posts)/[^/]+", path))
        return base and ((kind == "post" and not query.get("comment_id")) or
                         (kind in {"comment", "reply"} and bool(query.get("comment_id"))))
    return False


def source_identity(account, source_url, unique_hint, kind="post"):
    normalized = normalize_source(source_url)
    precise = precise_source(normalized, account.get("platform", ""), kind)
    if precise:
        key = normalized
        note = "Exact saved source for this account."
    elif normalized:
        key = "uncertain:" + digest([normalized, unique_hint])
        note = "This link does not identify one supported post or comment. Add an exact source before edit or delete."
    else:
        key = "missing:" + digest(unique_hint)
        note = "No exact source is saved. Add the original link before edit or delete."
    return {"source_key": key, "source_url": source_url or "",
            "precise_source": precise, "identity_note": note}


def review_source_url(review):
    """A local link correction never replaces the original observation."""
    return review.get("source_url_correction", review.get("source_url", ""))


def source_author_matches(account, source_url):
    """Reject explicit author conflicts; a handle-free URL proves no authorship."""
    part = urlsplit(source_url)
    platform = account.get("platform", "").casefold()
    path = part.path.rstrip("/")
    owner = None
    if platform == "x" and (part.hostname or "").lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        match = re.fullmatch(r"/([^/]+)/status/[^/]+", path)
        owner = match[1] if match else None
    elif platform == "tiktok" and (part.hostname or "").lower() in {"tiktok.com", "www.tiktok.com"}:
        match = re.fullmatch(r"/@([^/]+)/video/[^/]+", path)
        owner = match[1] if match else None
    elif platform == "facebook" and (part.hostname or "").lower() in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        match = re.fullmatch(r"/([^/]+)/posts/[^/]+", path)
        owner = match[1] if match else None
    elif platform == "linkedin" and (part.hostname or "").lower() in {"linkedin.com", "www.linkedin.com"}:
        match = re.fullmatch(r"/posts/([^/]+)_activity-[^/]+", path)
        owner = match[1] if match else None
    if owner is None:
        return True
    # A URL's username is comparable to a saved username/profile slug only.
    # Numeric provider IDs and display names do not establish the same fact.
    known = []
    handle = account.get("handle", "").lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_.-]+", handle):
        known.append(handle)
    profile = urlsplit(account.get("url", ""))
    if (profile.hostname or "").lower().removeprefix("www.").removeprefix("m.") in {
            "x": {"x.com", "twitter.com"}, "tiktok": {"tiktok.com"},
            "facebook": {"facebook.com"}, "linkedin": {"linkedin.com"}}.get(platform, set()):
        match = re.fullmatch(r"/(?:in/|company/)?@?([^/]+)/?", profile.path)
        if match:
            known.append(match[1])
    comparable = [value for value in known if value.isdigit() == owner.isdigit()]
    return all(value.casefold() == owner.casefold() for value in comparable)


def identity_handle(value):
    value = value.strip()
    if value.startswith('@'):
        value = value[1:]
    if value.startswith('u/'):
        value = value[2:]
    return value.casefold()


def profile_key(platform, value):
    """Conservative profile comparison; unknown routes never prove an identity."""
    part = urlsplit(normalize_source(value))
    host = (part.hostname or '').lower().removeprefix('www.').removeprefix('m.')
    allowed = {'X': {'x.com', 'twitter.com'}, 'YouTube': {'youtube.com'},
               'Facebook': {'facebook.com'}, 'Instagram': {'instagram.com'},
               'TikTok': {'tiktok.com'}, 'Reddit': {'reddit.com'},
               'LinkedIn': {'linkedin.com'}}
    if part.scheme != 'https' or host not in allowed.get(platform, set()) or part.port is not None or part.fragment:
        return None
    path = part.path.rstrip('/')
    if platform == 'Facebook' and path == '/profile.php':
        query = parse_qsl(part.query, keep_blank_values=True)
        return ('Facebook', 'id', query[0][1]) if len(query) == 1 and query[0][0] == 'id' and query[0][1].isdigit() else None
    if part.query:
        return None
    pattern = {'X': r'/([A-Za-z0-9_]+)', 'YouTube': r'/(@[^/@]+|channel/[^/]+)',
               'Facebook': r'/([A-Za-z0-9_.]+)', 'Instagram': r'/([A-Za-z0-9_.]+)',
               'TikTok': r'/(@[^/@]+)', 'Reddit': r'/user/([^/]+)',
               'LinkedIn': r'/(?:in|company)/([^/]+)'}[platform]
    match = re.fullmatch(pattern, path)
    if not match:
        return None
    slug = match[1]
    if platform == 'YouTube' and slug.startswith('channel/'):
        return (platform, 'id', slug[8:])  # Provider IDs remain case-sensitive.
    if platform == 'Facebook' and slug.isdigit():
        return (platform, 'id', slug)
    key = (platform, 'handle', identity_handle(slug))
    return key + (path.split('/')[1],) if platform == 'LinkedIn' else key


def observed_identity_matches(expected, handle, profile_url):
    if not identity_handle(handle) or identity_handle(handle) != identity_handle(expected['handle']):
        return False
    observed = profile_key(expected['platform'], profile_url)
    if observed is None:
        return False
    if observed[1] == 'handle' and observed[2] != identity_handle(handle):
        return False
    saved = profile_key(expected['platform'], expected['url']) if expected['url'] else None
    if expected['url']:
        return saved is not None and observed == saved
    return (expected['platform'] != 'LinkedIn' and observed[1] == 'handle' and
            observed[2] == identity_handle(expected['handle']))


def review_source_identity(account, review):
    identity = source_identity(account, review_source_url(review), ["review", review["id"]],
                               review.get("kind", "post"))
    if (review.get("kind") == "sample" or review.get("sample_count", 1) != 1) and identity["precise_source"]:
        identity.update(source_key="uncertain:" + digest([identity["source_key"], ["review", review["id"]]]),
                        precise_source=False,
                        identity_note="This evidence covers a sample or aggregate, not one actionable source item.")
    if (review.get("source_url_correction") and identity["precise_source"] and
            not source_author_matches(account, identity["source_url"])):
        identity.update(source_key="uncertain:" + digest([identity["source_key"], ["review", review["id"]]]),
                        precise_source=False,
                        identity_note="This corrected link names a different account. Review the saved account details before changing this item.")
    return identity


def semantic_finding(finding):
    value = {
        "title": finding.get("title", ""),
        "detail": finding.get("detail", ""),
        "severity": finding.get("severity", "suggestion"),
        "kind": finding.get("kind", "post"),
    }
    if finding.get('context'):
        # A fresh check date alone does not change meaning. Media, its source,
        # inspection state and the observed summary do.
        value['context'] = sorted([{key: item for key, item in observation.items() if key != 'checked_at'}
                                   for observation in finding['context']], key=canonical_json)
    return value


def evidence_fingerprint(account, source_key, reviews, findings):
    """Fingerprint meaning, not record IDs, mutable statuses, or report timestamps."""
    boundaries = {key: account.get(key) for key in
                  ("id", "platform", "handle", "display_name", "identity_mode", "daring", "sfw_only")}
    boundaries["url"] = normalize_source(account.get("url", ""))
    review_semantics = []
    for review in reviews:
        value = {key: review.get(key) for key in
                                 ("account_id", "kind", "title", "excerpt", "source_url",
                                  "sample_count", "severity", "finding", "suggestion", "origin")}
        value["source_url"] = normalize_source(value["source_url"])
        review_semantics.append(value)
    unique_reviews = {canonical_json(value): value for value in review_semantics}
    unique_findings = {canonical_json(semantic_finding(value)): semantic_finding(value) for value in findings}
    return digest({"source_key": source_key, "account": boundaries,
                   "reviews": sorted(unique_reviews.values(), key=canonical_json),
                   "findings": sorted(unique_findings.values(), key=canonical_json)})


def next_scheduled_check(config, current=None):
    """Compute the first future scheduled wall time, including DST gaps and folds."""
    current = current or datetime.now(timezone.utc)
    if not config.get("enabled") or not config.get("daily_time") or not config.get("timezone"):
        return None
    if config.get("ends_at") and datetime.fromisoformat(config["ends_at"].replace("Z", "+00:00")) <= current:
        return None
    try:
        zone = ZoneInfo(config["timezone"])
    except ZoneInfoNotFoundError:
        return None
    hour, minute = map(int, config["daily_time"].split(":"))
    local_now = current.astimezone(zone)
    for day_offset in range(0, 370):
        day = local_now.date() + timedelta(days=day_offset)
        naive = datetime(day.year, day.month, day.day, hour, minute)
        # Scan forward across a nonexistent wall time. For a fold, fold=0 is considered first.
        for gap_minutes in range(0, 181):
            wall = naive + timedelta(minutes=gap_minutes)
            candidates = []
            for fold in (0, 1):
                candidate = wall.replace(tzinfo=zone, fold=fold)
                roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
                if roundtrip.replace(tzinfo=None) != wall or roundtrip.fold != fold:
                    continue
                utc_candidate = candidate.astimezone(timezone.utc)
                if utc_candidate not in candidates:
                    candidates.append(utc_candidate)
            if not candidates:
                continue
            future = [value for value in sorted(candidates) if value > current]
            if future:
                utc_candidate = future[0]
                if config.get("ends_at") and utc_candidate > datetime.fromisoformat(config["ends_at"].replace("Z", "+00:00")):
                    return None
                return utc_candidate.isoformat(timespec="seconds").replace("+00:00", "Z")
            # Once the desired wall time (or its gap-adjusted replacement) is real and past,
            # this day's run is past. Later minutes are not additional schedule occurrences.
            if candidates:
                break
    return None
