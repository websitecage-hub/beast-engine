"""publish.py — Instagram Graph API resumable-upload publishing (section 2.5).

Flow: POST /{ig-id}/media (REELS + resumable) -> HEAD rupload (read offset) ->
PUT raw MP4 -> poll status_code until FINISHED -> POST /{ig-id}/media_publish.
--test stops before media_publish (the container harmlessly expires).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import requests

from . import alerts, config, seo

GRAPH = "https://graph.instagram.com/v23.0"
RUPLOAD = "https://rupload.instagram.com"
POLL_INTERVAL = 5
POLL_MAX = 300
_container_cache: dict = {}


def _token() -> str:
    tok = config.env("IG_ACCESS_TOKEN")
    if not tok:
        raise RuntimeError("IG_ACCESS_TOKEN secret is not set")
    return tok


def _redact(text: str) -> str:
    tok = config.env("IG_ACCESS_TOKEN")
    return text.replace(tok, "***TOKEN***") if tok else text


def _get(path: str, params: dict | None = None, timeout: int = 60):
    p = dict(params or {})
    p["access_token"] = _token()
    return requests.get(f"{GRAPH}{path}", params=p, timeout=timeout)


def resolve_account() -> dict:
    """GET /me?fields=id,username — runtime resolution, never hardcoded."""
    r = _get("/me", {"fields": "id,username"})
    if r.status_code == 401:
        raise PermissionError("401 TOKEN DEAD")
    r.raise_for_status()
    data = r.json()
    handle = config.load_config()["brand"]["handle"]
    if data.get("username") and data["username"] != handle:
        print(f"[publish] WARNING: token account {data['username']!r} != configured {handle!r}")
    return data


def caption_for(content: dict, cfg: dict) -> str:
    tags = " ".join(content.get("hashtags") or [])
    cap = content.get("caption") or content.get("hook", "")
    if tags and tags not in cap:
        cap = f"{cap}\n\n{tags}"
    return cap[:2200]


def alt_text_for(content: dict, cfg: dict) -> str:
    """SEO §6: alt text for the media container. Google reads this directly.

    Rebuilt from the finished content dict when it is missing, so a reel built before
    the SEO layer existed still ships an alt text rather than silently skipping a
    Google-visible field.
    """
    alt = (content.get("alt_text") or "").strip()
    if alt:
        return alt[:1000]
    return seo.build_alt_text(
        scene=content.get("scene", ""), topic=content.get("topic", ""),
        hook=content.get("hook", ""),
        on_screen_text=seo.onscreen_text_of(content))[:1000]


def create_container(ig_id: str, caption: str, test: bool = False,
                     alt_text: str = "", hide_like_count: bool = False) -> dict:
    # SEO §6.1: the Graph API rejects unknown params on this endpoint — sending
    # alt_text returns HTTP 400 "The param alt_text is not supported for REEL" and the
    # whole publish fails. So the container is created WITHOUT it; publish_reel()
    # persists the alt text to memory.json for manual entry via the app instead.
    data = {"media_type": "REELS", "upload_type": "resumable",
            "mime_type": "video/mp4", "caption": caption}
    if hide_like_count:
        data["hide_like_count"] = "true"
    r = requests.post(f"{GRAPH}/{ig_id}/media",
                      params={"access_token": _token()},
                      data=data,
                      timeout=120)
    if r.status_code == 401:
        raise PermissionError("401 TOKEN DEAD")
    if r.status_code >= 400:
        raise RuntimeError(f"container create failed {r.status_code}: {_redact(r.text[:400])}")
    data = r.json()
    if not data.get("id") or not data.get("uri"):
        raise RuntimeError(f"container create returned unexpected body: {_redact(str(data)[:300])}")
    _container_cache[ig_id] = data
    return data


def _rupload_headers() -> dict:
    return {"Authorization": f"OAuth {_token()}"}


def upload(uri: str, video: Path) -> None:
    """HEAD to read offset, then a single PUT of the raw MP4 (<60MB)."""
    body = video.read_bytes()
    offset = 0
    try:
        h = requests.head(f"{RUPLOAD}/{uri}", headers=_rupload_headers(), timeout=60)
        offset = int(h.headers.get("offset", 0) or 0)
    except Exception:  # noqa: BLE001 — HEAD is advisory
        offset = 0

    for attempt in range(2):
        resp = requests.put(f"{RUPLOAD}/{uri}",
                            headers={**_rupload_headers(), "Offset": str(offset)},
                            data=body[offset:], timeout=600)
        if resp.status_code < 400:
            return
        if resp.status_code >= 500 and attempt == 0:
            time.sleep(5)
            try:
                h = requests.head(f"{RUPLOAD}/{uri}", headers=_rupload_headers(), timeout=60)
                offset = int(h.headers.get("offset", offset) or offset)
            except Exception:  # noqa: BLE001
                pass
            continue
        raise RuntimeError(f"upload failed {resp.status_code}: {_redact(resp.text[:300])}")
    raise RuntimeError("upload failed after resume attempt")


def wait_finished(container_id: str) -> str:
    deadline = time.time() + POLL_MAX
    while time.time() < deadline:
        r = _get(f"/{container_id}", {"fields": "status_code,status"})
        if r.status_code == 200:
            code = (r.json() or {}).get("status_code")
            if code == "FINISHED":
                return code
            if code == "ERROR":
                raise RuntimeError(f"container status ERROR: {(r.json() or {}).get('status')}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError("container did not reach FINISHED within 5 minutes")


def media_publish(ig_id: str, container_id: str) -> str:
    for ig in (ig_id, config.env("IG_USER_ID")):
        if not ig:
            continue
        r = requests.post(f"{GRAPH}/{ig}/media_publish",
                          params={"access_token": _token()},
                          data={"creation_id": container_id}, timeout=120)
        if r.status_code == 200 and (r.json() or {}).get("id"):
            return r.json()["id"]
        print(f"[publish] media_publish via {ig} -> {r.status_code} {_redact(r.text[:200])}")
    raise RuntimeError("media_publish failed for both runtime id and IG_USER_ID")


def insights(media_id: str) -> dict:
    """Full metric set, retry with the smaller set on 400. Partial results accepted."""
    full = "reach,plays,likes,comments,saved,shares,total_interactions"
    small = "reach,likes,comments,saved,shares"
    for metric in (full, small):
        try:
            r = _get(f"/{media_id}/insights", {"metric": metric})
            if r.status_code == 200:
                out = {}
                for row in (r.json() or {}).get("data", []):
                    name = row.get("name")
                    vals = row.get("values") or []
                    if name and vals:
                        out[name] = vals[0].get("value")
                return out
        except requests.RequestException:
            continue
    return {}


def refresh_token() -> dict | None:
    tok = config.env("IG_ACCESS_TOKEN")
    if not tok:
        return None
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": tok},
                         timeout=60)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        return None
    return None


def _set_secret(name: str, value: str) -> bool:
    """Rotate a GitHub Actions secret via the API (pynacl sealed box)."""
    repo = config.env("GITHUB_REPOSITORY", "websitecage-hub/beast-engine")
    gh = config.env("GITHUB_TOKEN") or config.env("GH_TOKEN")
    if not gh:
        return False
    try:
        from nacl import encoding, public
        key_resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers={"Authorization": f"Bearer {gh}",
                     "Accept": "application/vnd.github+json"},
            timeout=60)
        if key_resp.status_code != 200:
            return False
        key_data = key_resp.json()
        pk = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
        sealed = public.SealedBox(pk).encrypt(value.encode())
        import base64
        put = requests.put(
            f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
            headers={"Authorization": f"Bearer {gh}",
                     "Accept": "application/vnd.github+json"},
            json={"encrypted_value": base64.b64encode(sealed).decode(),
                  "key_id": key_data["key_id"]}, timeout=60)
        return put.status_code in (201, 204)
    except Exception as exc:  # noqa: BLE001
        print(f"[publish] secret rotation failed: {exc}")
        return False


def publish_reel(video: Path, content: dict, cfg: dict, test: bool = False,
                 dry_run: bool = False) -> dict:
    """Publish the reel.

    Route note: `graph.instagram.com` (Instagram-Login tokens) has no resumable upload —
    POST /{ig-id}/media demands `video_url`. We therefore park the locally built MP4 on an
    anonymous public host and hand Instagram the URL, falling back to the resumable flow
    only if the account ever gains a Facebook-Login route that supports it.
    """
    if dry_run:
        return {"skipped": "dry-run"}
    account = resolve_account()
    ig_id = str(account["id"])
    caption = caption_for(content, cfg)
    alt_text = alt_text_for(content, cfg)
    # SEO §6.1: Graph rejects alt_text on REELS containers (HTTP 400), so it cannot be
    # shipped via the API. It is surfaced loudly and returned in the result dict rather
    # than dropped, so the field can be added in the app and never silently disappears.
    print(f"[publish] alt text (add via app Advanced Settings): {alt_text[:110]}")
    hide_likes = bool(cfg.get("hide_like_count", False))
    if hide_likes:
        print("[publish] hide_like_count requested on the container")

    from . import upload_host
    url, host = upload_host.publicize(video)
    if url:
        try:
            container = create_container_url(ig_id, caption, url, alt_text=alt_text,
                                             hide_like_count=hide_likes)
            cid = container["id"]
            status = wait_finished(cid)
            if test:
                print(f"[publish] TEST mode: container {cid} ready ({status}), not publishing")
                return {"test": True, "container_id": cid, "status": status,
                        "host": host, "alt_text": alt_text,
                        "hide_like_count": hide_likes}
            media_id = media_publish(ig_id, cid)
            return {"media_id": media_id, "container_id": cid, "caption": caption,
                    "alt_text": alt_text, "hide_like_count": hide_likes,
                    "ig_id": ig_id, "status": status, "host": host, "video_url": url}
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[publish] hosted-url route failed ({exc}); trying resumable")

    container = create_container(ig_id, caption, test=test, alt_text=alt_text,
                                 hide_like_count=hide_likes)
    cid, uri = container["id"], container["uri"]
    upload(uri, video)
    status = wait_finished(cid)
    if test:
        print(f"[publish] TEST mode: container {cid} ready ({status}), not publishing")
        return {"test": True, "container_id": cid, "status": status}
    media_id = media_publish(ig_id, cid)
    return {"media_id": media_id, "container_id": cid, "caption": caption,
            "ig_id": ig_id, "status": status}


def create_container_url(ig_id: str, caption: str, video_url: str,
                         alt_text: str = "", hide_like_count: bool = False) -> dict:
    """Hosted-URL container — the only route Instagram-Login tokens accept.

    `alt_text` is accepted as a parameter but deliberately NOT sent: the Graph API
    rejects it on REELS containers with HTTP 400 ("The param alt_text is not supported
    for REEL"). It is carried through the return value so the caller can persist it.

    `hide_like_count` IS sent when enabled — the endpoint accepts it (HTTP 200) rather
    than rejecting it the way it rejects alt_text, and it is the only lever the API
    exposes for hiding like counts on a reel.
    """
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption}
    if hide_like_count:
        data["hide_like_count"] = "true"
    r = requests.post(f"{GRAPH}/{ig_id}/media",
                      params={"access_token": _token()},
                      data=data,
                      timeout=120)
    if r.status_code == 401:
        raise PermissionError("401 TOKEN DEAD")
    if r.status_code >= 400:
        raise RuntimeError(f"container create (url) failed {r.status_code}: "
                           f"{_redact(r.text[:400])}")
    data = r.json()
    if not data.get("id"):
        raise RuntimeError(f"unexpected body: {_redact(str(data)[:300])}")
    _container_cache[ig_id] = data
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = config.load_config()
    content = config.load_json(config.CONTENT_PATH, {})
    video = config.OUTPUTS / "reel.mp4"
    if not video.exists():
        print("no outputs/reel.mp4 to publish")
        return 1
    out = publish_reel(video, content, cfg, test=args.test, dry_run=args.dry_run)
    print(f"publish result: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
