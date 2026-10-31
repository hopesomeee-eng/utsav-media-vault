#!/usr/bin/env python3
"""
sync_from_supabase.py — GitHub Action Automated Media Sync Worker

Scans Supabase PostgreSQL for newly uploaded festival assets,
downloads them directly from the Cloudinary CDN shard,
verifies cryptographic SHA-256 integrity,
organizes them into 2-char hex prefix sharded paths,
updates the manifest (index.json),
and optionally patches fallback_url back to Supabase.
"""

import os
import sys
import json
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List

# Target Repository Configuration
VAULT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = VAULT_ROOT / "index.json"
OWNER = "hopesomeee-eng"
REPO = "utsav-media-vault"
TAG = "v1.0.0"

# Default Supabase Credentials (Overrideable via Environment)
DEFAULT_SUPABASE_URL = "https://holvlrymoagqwlbjltuv.supabase.co"
DEFAULT_SUPABASE_ANON_KEY = "sb_publishable_5GCe04LDHJSzla2XOmyGag_kkp62BEH"

SUPABASE_URL = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", DEFAULT_SUPABASE_ANON_KEY)


def load_manifest() -> Dict[str, Any]:
    """Loads existing index.json manifest or returns default structure."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "version": "1.0.0",
        "name": REPO,
        "owner": OWNER,
        "repo": REPO,
        "default_tag": TAG,
        "stats": {"total_assets": 0, "categories": {}},
        "assets": [],
    }


def save_manifest(manifest: Dict[str, Any]) -> None:
    """Saves updated index.json with recalculated category counts."""
    categories_count: Dict[str, int] = {}
    for a in manifest.get("assets", []):
        cat = a.get("category", "cards")
        categories_count[cat] = categories_count.get(cat, 0) + 1

    manifest["stats"] = {
        "total_assets": len(manifest.get("assets", [])),
        "categories": categories_count,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def fetch_database_images() -> List[Dict[str, Any]]:
    """Fetches image catalog records from Supabase PostgREST API."""
    endpoint = f"{SUPABASE_URL}/rest/v1/images?select=id,filename,sha256,cdn_url,storage_provider,fallback_url&is_deleted=eq.false"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
        "User-Agent": "Utsav-Vault-Sync/1.0",
    }
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[Supabase] Fetched {len(data)} total records from catalog.")
            return data
    except Exception as e:
        print(f"[-] Failed to fetch images from Supabase: {e}")
        return []


def download_and_verify(cdn_url: str, expected_sha256: str) -> bytes:
    """Downloads asset binary and verifies SHA-256 hash."""
    headers = {"User-Agent": "Mozilla/5.0 (Utsav Media Syncer)"}
    req = urllib.request.Request(cdn_url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        content = resp.read()

    hasher = hashlib.sha256(content)
    actual_hash = hasher.hexdigest().lower()
    if expected_sha256 and actual_hash != expected_sha256.lower():
        raise ValueError(f"Checksum mismatch: expected {expected_sha256}, got {actual_hash}")

    return content


def update_supabase_fallback_url(image_id: str, fallback_url: str) -> None:
    """Patches fallback_url into Supabase if service role key is provided."""
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        return

    endpoint = f"{SUPABASE_URL}/rest/v1/images?id=eq.{image_id}"
    payload = json.dumps({"fallback_url": fallback_url}).encode("utf-8")
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    req = urllib.request.Request(endpoint, data=payload, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                print(f"[Supabase] Updated fallback_url for image {image_id[:8]}")
    except Exception as e:
        print(f"[-] Supabase patch fallback_url failed: {e}")


def main():
    print("=" * 60)
    print(" UTSAV MEDIA VAULT — AUTOMATED SYNC WORKER")
    print("=" * 60)

    manifest = load_manifest()
    vaulted_hashes = {a["sha256"].lower() for a in manifest.get("assets", [])}
    print(f"[Manifest] Currently contains {len(vaulted_hashes)} vaulted assets.")

    db_images = fetch_database_images()
    if not db_images:
        print("[!] No database records retrieved. Exiting.")
        sys.exit(0)

    # Filter images that are not yet vaulted
    pending = []
    for img in db_images:
        sha256 = (img.get("sha256") or "").lower().strip()
        cdn_url = (img.get("cdn_url") or "").strip()
        if sha256 and cdn_url and sha256 not in vaulted_hashes:
            pending.append(img)

    print(f"[Diff] Found {len(pending)} new assets to vault.")
    if not pending:
        print("[OK] Media vault is fully up to date. Zero sync operations needed.")
        sys.exit(0)

    synced_count = 0
    for item in pending:
        sha256 = item["sha256"].lower()
        cdn_url = item["cdn_url"]
        category = "cards"
        prefix = sha256[:2]
        ext = "webp"

        target_dir = VAULT_ROOT / "festivals" / category / prefix
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{sha256}.{ext}"
        storage_key = f"festivals/{category}/{prefix}/{sha256}.{ext}"

        print(f"\n[Syncing] {item.get('filename', sha256)} -> {storage_key}")
        try:
            content = download_and_verify(cdn_url, sha256)
            with open(target_file, "wb") as f:
                f.write(content)

            jsdelivr_url = f"https://cdn.jsdelivr.net/gh/{OWNER}/{REPO}@{TAG}/{storage_key}"

            manifest["assets"].append({
                "sha256": sha256,
                "filename": target_file.name,
                "category": category,
                "storage_key": storage_key,
                "byte_size": len(content),
                "primary_url": jsdelivr_url,
            })
            vaulted_hashes.add(sha256)
            synced_count += 1

            # Update Supabase fallback URL
            update_supabase_fallback_url(item["id"], jsdelivr_url)
            print(f"[OK] Successfully vaulted {storage_key} ({len(content)} bytes)")

        except Exception as err:
            print(f"[-] Failed to sync asset {sha256}: {err}")

    if synced_count > 0:
        save_manifest(manifest)
        print(f"\n[SUCCESS] Vaulted {synced_count} new assets. Manifest updated.")
        # Output for GitHub Actions
        with open(os.environ.get("GITHUB_ENV", "github_env.tmp"), "a") as env_file:
            env_file.write(f"VAULT_SYNCED_COUNT={synced_count}\n")
    else:
        print("\n[!] Zero assets were vaulted due to errors or empty delta.")


if __name__ == "__main__":
    main()
