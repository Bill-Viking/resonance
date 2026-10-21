"""Conservative, explainable account boundary checks; never a safety certification.

This module has no network or storage access. It checks supplied English text only.
Each result is a possible issue for a person to review, not an inferred intention.
"""

import hashlib
import json
import re


def _offset(text, position):
    """Return JavaScript-compatible UTF-16 offsets, including astral characters."""
    return len(text[:position].encode("utf-16-le")) // 2


def check(text, account=None):
    account = account or {}
    identity = account.get("identity_mode", "unclassified")
    daring = account.get("daring", "careful")
    sfw = identity != "alias" or account.get("sfw_only", True)
    basis = ("This account is tied to your name and must stay SFW."
             if identity == "named" else
             "Keep SFW until you have set this account’s boundaries."
             if identity == "unclassified" else
             "You have chosen an SFW boundary for this alias.")
    signals = []

    def flag(code, pattern, title, detail, category):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            signals.append({"code": code, "title": title, "detail": detail,
                            "start": _offset(text, match.start()),
                            "end": _offset(text, match.end()), "category": category})

    if sfw:
        flag("sfw-profanity", r"\b(?:f[u*]ck\w*|motherfuck\w*|sh[i*]t\w*|bullshit|assholes?|bitch(?:es|ing)?|cunts?)\b",
             "Profanity may cross your SFW boundary",
             basis + " Review the wording in context before leaving it attached to this account.", "sfw")
        flag("sfw-explicit", r"\b(?:blowjobs?|handjobs?|cumshots?|porn(?:ography|ographic)?|nudes?|naked|orgasms?|masturbat\w*|dick pics?|anal sex|oral sex|sex tape|fuck me)\b",
             "Sexual wording needs an SFW review",
             basis + " This could be explicit or a non-explicit discussion of the topic. Check the whole post and any media; a keyword alone cannot decide.", "sfw")
        flag("sfw-graphic", r"\b(?:gore|gory|decapitat\w*|dismember\w*|severed (?:head|limb)|graphic violence)\b",
             "Graphic wording needs an SFW review",
             basis + " Review any graphic description or linked media, including material that the text check cannot see.", "sfw")

    flag("targeted-threat", r"\b(?:i(?:’|'| a)?m going to|i will|i(?:’|')ll|we should|let(?:’|')s)\s+(?:kill|shoot|stab|rape|murder)\s+(?:you|them|him|her)\b",
         "This may read as a threat",
         "Even a daring account benefits from a second look at wording that could threaten someone. Consider quotation, fiction and context before deciding.", "risk")
    flag("group-attack", r"\b(?:women|men|immigrants|muslims|christians|jews|liberals|conservatives|gay people|trans people|black people|white people)\s+(?:are|are all|are just)\s+(?:fucking\s+)?(?:assholes?|stupid|idiots?|trash|scum|vermin|disgusting|worthless)\b",
         "A whole group is being attacked",
         "This may come across as contempt for a group rather than criticism of an action. Review it even on an alias; more daring does not remove that audience risk.", "risk")
    if sfw or daring in {"careful", "balanced"}:
        flag("personal-attack", r"\b(?:you(?:’|')re|you are)\s+(?:a |an |such a |such an )?(?:idiot|moron|loser|stupid|worthless|pathetic)\b",
             "The reply may sound personal",
             (basis + " " if sfw else "Your " + daring + " setting asks for a check on direct put-downs. ") + "Naming the behavior can keep the disagreement focused.", "tone")
    if daring == "careful":
        self_criticism = r"\bI(?:['’]m| am)\s+(?:(?:such )?a\s+)?(?:loser|failure|worthless|useless|pathetic)\b"
        if re.search(self_criticism, text, re.IGNORECASE):
            flag("self-criticism", self_criticism,
                 "Consider how this self-criticism comes across",
                 "This may be frustration or self-deprecating humor. Consider describing the situation instead of giving yourself a lasting label. This check does not assess you as a person.", "tone")
        else:
            flag("sweeping-claim", r"\b(?:everyone|nobody|always|never)\b",
                 "Check whether this claim is too broad",
                 "Your careful setting flags absolute language. Check whether the surrounding context supports that scope, or whether more specific wording would make your point clearer.", "tone")
        flag("heated-punctuation", r"[!?]{3,}",
             "The emphasis may sound heated",
             "Your careful setting flags repeated punctuation. A single mark may express the same point more calmly.", "tone")
    return signals


def account_flags(accounts, reviews):
    """Derive stable flags. Changed text or boundaries require a fresh review."""
    by_id = {a["id"]: a for a in accounts}
    result = []
    for review in reviews:
        account = by_id.get(review["account_id"])
        text = review.get("excerpt", "")
        if not account or not text.strip():
            continue
        boundary = {k: account.get(k, v) for k, v in
                    (("identity_mode", "unclassified"), ("daring", "careful"), ("sfw_only", True))}
        for signal in check(text, account):
            fingerprint = json.dumps([review["id"], account["id"], text, boundary, signal["code"]], sort_keys=True)
            result.append({"id": hashlib.sha256(fingerprint.encode()).hexdigest()[:32],
                           "review_id": review["id"], "account_id": account["id"],
                           "title": signal["title"], "detail": signal["detail"],
                           "excerpt": text, "source_url": review.get("source_url", ""),
                           "rule": signal["code"], "category": signal["category"],
                           "status": "open", "snoozed_until": None,
                           "updated_at": review.get("updated_at", review.get("reviewed_at"))})
    return result
