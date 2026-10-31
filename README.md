# Utsav Media Vault (उत्सव मीडिया वॉल्ट)

High-performance, immutable media vault and multi-CDN edge origin for the **Festival (Utsav)** platform.

---

## 🏛️ Architecture Overview

This repository serves as **Tier 2 Media Overflow Storage & Cold Vault** for Utsav greeting cards, festival banners, ambient audio, and animations. It is integrated into a multi-CDN fallback hierarchy alongside our 4-shard Cloudinary primary delivery pool.

```
                    [ Flutter Client / Web SPA ]
                                |
             +------------------+------------------+
             |                                     |
       [ Primary Store ]                    [ Fallback Vault ]
    Cloudinary 4-Shard Pool              GitHub + Multi-CDN Edge
   (100GB Free Storage & Egress)         (Infinite Free Storage & Egress)
             |                                     |
  - Shard 0: pqovykn6                   1. jsDelivr Global Edge CDN
  - Shard 1: iio6tdoo                   2. Fastly jsDelivr Edge
  - Shard 2: bbjsynrb                   3. Statically AnyCast CDN
  - Shard 3: xbcdj1cu                   4. GitHub Raw Fallback
                                        5. GitHub Releases Download
```

---

## 📁 Directory Structure & Taxonomy

Media assets are organized using a **2-character cryptographic prefix sharding** layout to maintain $O(1)$ Git tree lookup performance and prevent filesystem directory bloat:

```
utsav-media-vault/
├── .github/
│   └── workflows/
│       └── validate-media.yml      # CI integrity & checksum validation
├── festivals/
│   ├── cards/                      # Shareable greeting cards (WebP)
│   │   ├── 0a/
│   │   │   └── 0a1b2c3d...webp
│   │   └── ff/
│   ├── heroes/                     # High-res festival banner images (WebP)
│   ├── ambient/                    # Background devotional soundscapes (AAC)
│   └── lotties/                    # Lightweight festive animations (JSON)
├── index.json                      # Cryptographic media manifest
├── LICENSE                         # MIT License
└── README.md
```

### Path Specification
$$\text{Storage Key} = \texttt{festivals/\{category\}/\{sha256[0:2]\}/\{sha256\}.\{ext\}}$$

- **Deterministic**: Every file's path is strictly derived from its SHA-256 hash.
- **Deduplicated**: Identical images share the same path and are never stored twice.
- **Immutable**: Files are write-once, preventing stale caches across edge nodes.

---

## 🌐 Multi-CDN Edge Resolvers

Assets committed here are served worldwide at edge speed through prioritized CDN providers:

| Priority | Edge Provider | URL Pattern |
| :---: | :--- | :--- |
| **Primary** | **jsDelivr** | `https://cdn.jsdelivr.net/gh/hopesomeee-eng/utsav-media-vault@{tag}/{path}` |
| **Fallback 1** | **Fastly jsDelivr** | `https://fastly.jsdelivr.net/gh/hopesomeee-eng/utsav-media-vault@{tag}/{path}` |
| **Fallback 2** | **Statically** | `https://cdn.statically.io/gh/hopesomeee-eng/utsav-media-vault@{tag}/{path}` |
| **Fallback 3** | **GitHub Raw** | `https://raw.githubusercontent.com/hopesomeee-eng/utsav-media-vault/{branch}/{path}` |
| **Fallback 4** | **Releases Direct** | `https://github.com/hopesomeee-eng/utsav-media-vault/releases/download/{tag}/{file}` |

> **Best Practice**: Always use tagged release references (e.g., `@v1.0.0`) in production clients for permanent, immutable browser and edge caching.

---

## 🔒 Automated Ingestion & Uploads

### 1. Direct Git Contents REST API
Admins uploading via the Utsav Admin Dashboard can commit assets directly:
```http
PUT https://api.github.com/repos/hopesomeee-eng/utsav-media-vault/contents/{storageKey}
Authorization: Bearer <GITHUB_PAT>
Content-Type: application/json

{
  "message": "chore(media): vault asset {sha256[:8]}",
  "content": "<base64_encoded_bytes>",
  "branch": "main"
}
```

### 2. Automated Sync Script
Run the automated sync script from the `festival` project to ingest and back up all active catalog images:
```bash
python scripts/sync_media_to_vault.py
```

---

## 📄 License

This repository and its media assets are licensed under the [MIT License](LICENSE).
