#!/usr/bin/env python3
"""
sync_from_supabase.py — GitHub Action Automated Multi-Media Vault Sync Worker

Synchronizes all media assets across the Utsav platform from Supabase and Cloudinary
into the Tier 2 GitHub Media Vault (utsav-media-vault):
1. Visual Cards & Heroes (images table) -> festivals/cards/<prefix>/<sha256>.webp
2. Festive Bodymovin Lottie JSONs (lottie_overlays table) -> festivals/lotties/<clean_filename> & sharded
3. Sacred Ambient Audio Stems (ambient_audio table) -> festivals/ambient/<clean_filename> & sharded
4. Manifest Re-indexing -> index.json with full category telemetry
"""

import os
import sys
import json
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
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
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Error loading manifest: {e}, reinitializing")
    return {
        "version": "1.0.0",
        "name": REPO,
        "owner": OWNER,
        "repo": REPO,
        "default_tag": TAG,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {"total_assets": 0, "categories": {}},
        "assets": [],
    }


def save_manifest(manifest: Dict[str, Any]) -> None:
    """Saves updated index.json with recalculated category counts and updated_at."""
    categories_count: Dict[str, int] = {}
    for a in manifest.get("assets", []):
        cat = a.get("category", "cards")
        categories_count[cat] = categories_count.get(cat, 0) + 1

    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["stats"] = {
        "total_assets": len(manifest.get("assets", [])),
        "categories": categories_count,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def fetch_database_records(table: str, select_query: str) -> List[Dict[str, Any]]:
    """Generic fetcher for Supabase PostgREST tables."""
    endpoint = f"{SUPABASE_URL}/rest/v1/{table}?select={select_query}&is_deleted=eq.false"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
        "User-Agent": "Utsav-Vault-Sync/2.0",
    }
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[Supabase] Fetched {len(data)} records from {table}.")
            return data
    except Exception as e:
        print(f"[-] Failed to fetch {table} from Supabase: {e}")
        return []


def download_media(url: str, expected_sha256: str = "") -> bytes:
    """Downloads media binary with timeout and optional sha256 validation."""
    headers = {"User-Agent": "Mozilla/5.0 (Utsav Media Syncer 2.0)"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        content = resp.read()

    if expected_sha256:
        actual_hash = hashlib.sha256(content).hexdigest().lower()
        if actual_hash != expected_sha256.lower():
            raise ValueError(f"Checksum mismatch: expected {expected_sha256}, got {actual_hash}")

    return content


def update_supabase_fallback_url(table: str, record_id: str, fallback_url: str) -> None:
    """Patches fallback_url into Supabase record if service role key is provided."""
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        return

    endpoint = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{record_id}"
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
                print(f"[Supabase] Updated fallback_url for {table} id {record_id[:8]}")
    except Exception as e:
        print(f"[-] Supabase patch fallback_url failed: {e}")


def sync_images(manifest: Dict[str, Any], vaulted_hashes: set) -> int:
    """Syncs images table into festivals/cards/."""
    db_images = fetch_database_records(
        "images", "id,filename,sha256,cdn_url,storage_provider,fallback_url"
    )
    synced = 0
    for img in db_images:
        sha256 = (img.get("sha256") or "").lower().strip()
        cdn_url = (img.get("cdn_url") or "").strip()
        if not sha256 or not cdn_url or sha256 in vaulted_hashes:
            continue

        prefix = sha256[:2]
        ext = "webp"
        target_dir = VAULT_ROOT / "festivals" / "cards" / prefix
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{sha256}.{ext}"
        storage_key = f"festivals/cards/{prefix}/{sha256}.{ext}"

        try:
            content = download_media(cdn_url, expected_sha256=sha256)
            with open(target_file, "wb") as f:
                f.write(content)

            jsdelivr_url = f"https://cdn.jsdelivr.net/gh/{OWNER}/{REPO}@{TAG}/{storage_key}"
            manifest["assets"].append({
                "sha256": sha256,
                "filename": target_file.name,
                "category": "cards",
                "storage_key": storage_key,
                "byte_size": len(content),
                "primary_url": jsdelivr_url,
            })
            vaulted_hashes.add(sha256)
            synced += 1
            update_supabase_fallback_url("images", img["id"], jsdelivr_url)
            print(f"[OK] Vaulted Image: {storage_key} ({len(content)} B)")
        except Exception as e:
            print(f"[-] Error syncing image {sha256[:8]}: {e}")

    return synced


def sync_lotties(manifest: Dict[str, Any], vaulted_filenames: set) -> int:
    """Syncs lottie_overlays table into festivals/lotties/ (both named and sha256 paths)."""
    db_lotties = fetch_database_records("lottie_overlays", "id,slug,name,lottie_url")
    synced = 0
    lottie_base_dir = VAULT_ROOT / "festivals" / "lotties"
    lottie_base_dir.mkdir(parents=True, exist_ok=True)

    for item in db_lotties:
        lottie_url = (item.get("lottie_url") or "").strip()
        if not lottie_url:
            continue

        # Extract filename from URL (e.g. ramadan_lantern.json)
        filename = lottie_url.split("/")[-1].split("?")[0]
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        if filename in vaulted_filenames:
            # File already vaulted, ensure disk file exists
            if (lottie_base_dir / filename).exists():
                continue

        try:
            content = download_media(lottie_url)
            # Validate JSON
            json.loads(content.decode("utf-8"))
            sha256 = hashlib.sha256(content).hexdigest().lower()

            # 1. Save flat predictable named file: festivals/lotties/<filename>
            named_file = lottie_base_dir / filename
            with open(named_file, "wb") as f:
                f.write(content)

            # 2. Save sharded path: festivals/lotties/<prefix>/<sha256>.json
            prefix = sha256[:2]
            sharded_dir = lottie_base_dir / prefix
            sharded_dir.mkdir(parents=True, exist_ok=True)
            sharded_file = sharded_dir / f"{sha256}.json"
            with open(sharded_file, "wb") as f:
                f.write(content)

            storage_key = f"festivals/lotties/{filename}"
            jsdelivr_url = f"https://cdn.jsdelivr.net/gh/{OWNER}/{REPO}@{TAG}/{storage_key}"

            manifest["assets"].append({
                "sha256": sha256,
                "filename": filename,
                "category": "lotties",
                "storage_key": storage_key,
                "byte_size": len(content),
                "primary_url": jsdelivr_url,
            })
            vaulted_filenames.add(filename)
            synced += 1
            print(f"[OK] Vaulted Lottie: {storage_key} ({len(content)} B)")
        except Exception as e:
            print(f"[-] Error syncing lottie {filename}: {e}")

    return synced


def sync_ambient_audio(manifest: Dict[str, Any], vaulted_filenames: set) -> int:
    """Syncs ambient_audio table into festivals/ambient/ (both named and sha256 paths)."""
    db_audio = fetch_database_records("ambient_audio", "id,slug,title,audio_url")
    synced = 0
    audio_base_dir = VAULT_ROOT / "festivals" / "ambient"
    audio_base_dir.mkdir(parents=True, exist_ok=True)

    for track in db_audio:
        audio_url = (track.get("audio_url") or "").strip()
        if not audio_url:
            continue

        filename = audio_url.split("/")[-1].split("?")[0]
        ext = filename.split(".")[-1] if "." in filename else "wav"

        if filename in vaulted_filenames:
            if (audio_base_dir / filename).exists():
                continue

        try:
            content = download_media(audio_url)
            sha256 = hashlib.sha256(content).hexdigest().lower()

            # 1. Save flat predictable named file: festivals/ambient/<filename>
            named_file = audio_base_dir / filename
            with open(named_file, "wb") as f:
                f.write(content)

            # 2. Save sharded path: festivals/ambient/<prefix>/<sha256>.<ext>
            prefix = sha256[:2]
            sharded_dir = audio_base_dir / prefix
            sharded_dir.mkdir(parents=True, exist_ok=True)
            sharded_file = sharded_dir / f"{sha256}.{ext}"
            with open(sharded_file, "wb") as f:
                f.write(content)

            storage_key = f"festivals/ambient/{filename}"
            jsdelivr_url = f"https://cdn.jsdelivr.net/gh/{OWNER}/{REPO}@{TAG}/{storage_key}"

            manifest["assets"].append({
                "sha256": sha256,
                "filename": filename,
                "category": "ambient",
                "storage_key": storage_key,
                "byte_size": len(content),
                "primary_url": jsdelivr_url,
            })
            vaulted_filenames.add(filename)
            synced += 1
            print(f"[OK] Vaulted Audio: {storage_key} ({len(content)} B)")
        except Exception as e:
            print(f"[-] Error syncing audio {filename}: {e}")

    return synced


def main():
    print("=" * 65)
    print(" UTSAV MEDIA VAULT — AUTOMATED MULTI-MEDIA SYNC WORKER")
    print("=" * 65)

    manifest = load_manifest()
    vaulted_hashes = {a["sha256"].lower() for a in manifest.get("assets", []) if "sha256" in a}
    vaulted_filenames = {a.get("filename", "") for a in manifest.get("assets", [])}

    print(f"[Manifest] Initial vaulted assets: {len(manifest.get('assets', []))}")

    # 1. Sync visual cards
    synced_images = sync_images(manifest, vaulted_hashes)

    # 2. Sync Lottie vector animations
    synced_lotties = sync_lotties(manifest, vaulted_filenames)

    # 3. Sync Sacred Ambient Audio stems
    synced_audio = sync_ambient_audio(manifest, vaulted_filenames)

    total_new = synced_images + synced_lotties + synced_audio
    print("-" * 65)
    print(f"[Sync Summary] Images: +{synced_images} | Lotties: +{synced_lotties} | Audio: +{synced_audio}")
    print(f"[Sync Summary] Total new media vaulted: {total_new}")

    # Deduplicate manifest assets by storage_key
    seen_keys = set()
    deduped_assets = []
    for a in manifest.get("assets", []):
        sk = a.get("storage_key")
        if sk and sk not in seen_keys:
            seen_keys.add(sk)
            deduped_assets.append(a)
    manifest["assets"] = deduped_assets

    save_manifest(manifest)
    print(f"[SUCCESS] Manifest updated. Total catalog: {len(manifest['assets'])} assets.")

    # Write GITHUB_ENV if running in GitHub Actions
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a") as f:
            f.write(f"VAULT_SYNCED_COUNT={total_new}\n")


if __name__ == "__main__":
    main()
