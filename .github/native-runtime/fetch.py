import sys, json, hashlib, os, time, urllib.request, urllib.error, urllib.parse
root = sys.argv[1]
inputs = json.load(open(os.path.join(root, "SOURCE-INPUTS.json")))
dl = os.path.join(root, "downloads")
CAP = 60 * 1024 * 1024


class Strict(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError("HTTPS downgrade refused: " + newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


opener = urllib.request.build_opener(Strict)


def xattrs(p):
    try:
        return sorted(os.listxattr(p))
    except Exception as e:
        return "n/a:" + repr(e)


receipts = []
for s in inputs["sources"]:
    r = {"name": s["name"], "sourceURL": s["url"], "expected_sha256": s["sha256"]}
    fn = os.path.join(dl, os.path.basename(urllib.parse.urlsplit(s["url"]).path))
    r["file"] = os.path.basename(fn)
    t0 = time.monotonic()
    try:
        assert s["url"].startswith("https://")
        req = urllib.request.Request(s["url"], headers={"User-Agent": "curl/8.7.1"})
        h = hashlib.sha256()
        n = 0
        with opener.open(req, timeout=30) as resp, open(fn, "xb") as f:
            r["finalURL"] = resp.geturl()
            r["status"] = resp.status
            if urllib.parse.urlsplit(resp.geturl()).scheme != "https":
                raise Exception("final not https")
            while True:
                if time.monotonic() - t0 > 120:
                    raise Exception("120s deadline exceeded")
                b = resp.read(65536)
                if not b:
                    break
                n += len(b)
                if n > CAP:
                    raise Exception("exceeds 60MB cap")
                h.update(b)
                f.write(b)
        r["bytes"] = n
        r["actual_sha256"] = h.hexdigest()
        r["match"] = r["actual_sha256"] == s["sha256"]
        r["xattrs"] = xattrs(fn)
    except Exception as e:
        r["error"] = repr(e)
        r["match"] = False
        if os.path.exists(fn):
            r["xattrs"] = xattrs(fn)
            r["bytes"] = os.path.getsize(fn)
    r["seconds"] = round(time.monotonic() - t0, 1)
    receipts.append(r)
    print(json.dumps(r), flush=True)
json.dump(receipts, open(os.path.join(root, "FETCH-RECEIPT.json"), "w"), indent=1)
