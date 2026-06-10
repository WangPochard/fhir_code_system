#!/usr/bin/env python
"""
SNOMED CT RF2 → PostgreSQL
直接讀取 RF2 Snapshot 檔，每個 description term 獨立一筆 embedding。

核心原則：
  嵌入的 content = 純文字 term（FSN 或 Preferred synonym），不含任何 metadata
  Concept ID 是搜尋後帶出的 metadata，不進 embedding content
  只嵌入 Preferred Terms（透過 Language refset 過濾），排除 Acceptable synonyms 的雜訊

使用方法：
  python script/snomed/import_rf2.py
  python script/snomed/import_rf2.py --dry-run     # 只統計不寫入
  python script/snomed/import_rf2.py --limit 5000  # 只處理前 N 個 concept（測試用）
"""

import sys
import csv
import re
import argparse
from pathlib import Path
from collections import defaultdict

csv.field_size_limit(sys.maxsize)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text
from app.database import get_snomed_context, SnomedSession
from app.snomed.models import Document, DocumentEmbedding
from app.config import get_settings
from app.logger import logger
from app.embedding import EmbeddingService

# ------------------------------------------------------------------
# RF2 file paths
# ------------------------------------------------------------------
_RF2_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "SnomedCT_InternationalRF2_PRODUCTION_20250701T120000Z"
    / "Snapshot"
)
CONCEPT_FILE     = _RF2_ROOT / "Terminology" / "sct2_Concept_Snapshot_INT_20250701.txt"
DESCRIPTION_FILE = _RF2_ROOT / "Terminology" / "sct2_Description_Snapshot-en_INT_20250701.txt"
LANGUAGE_FILE    = _RF2_ROOT / "Refset" / "Language" / "der2_cRefset_LanguageSnapshot-en_INT_20250701.txt"
EXTMAP_FILE      = _RF2_ROOT / "Refset" / "Map" / "der2_iisssccRefset_ExtendedMapSnapshot_INT_20250701.txt"

# SNOMED RF2 constants
TYPE_FSN         = "900000000000003001"
TYPE_SYNONYM     = "900000000000013009"
ACCEPT_PREFERRED = "900000000000548007"  # Preferred Term（只有這個才 embed）

EMBED_BATCH = 64  # 每批送入 embedding model 的詞數


# ------------------------------------------------------------------
# Step 1: 載入 active concept ID 集合
# ------------------------------------------------------------------
def load_active_concepts() -> set[str]:
    active: set[str] = set()
    with open(CONCEPT_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row["active"] == "1":
                active.add(row["id"])
    logger.info(f"Active concepts: {len(active):,}")
    return active


# ------------------------------------------------------------------
# Step 2: 載入 Preferred Term 的 description ID 集合
# 只有 Language refset 標記為 Preferred 的 description 才進 embedding
# 避免 Acceptable synonym 的 lexical 雜訊污染向量空間
# ------------------------------------------------------------------
def load_preferred_desc_ids() -> set[str]:
    preferred: set[str] = set()
    with open(LANGUAGE_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row["active"] == "1" and row["acceptabilityId"] == ACCEPT_PREFERRED:
                preferred.add(row["referencedComponentId"])
    logger.info(f"Preferred description IDs: {len(preferred):,}")
    return preferred


# ------------------------------------------------------------------
# Step 3: 載入每個 concept 的 FSN 與同義詞
# preferred_synonyms = 只有 PT 的 synonym（進 embedding）
# all_synonyms       = 所有 synonym（存 metadata，不進 embedding）
# ------------------------------------------------------------------
def load_descriptions(active_concepts: set[str], preferred_desc_ids: set[str]) -> dict:
    data: dict = defaultdict(lambda: {
        "fsn": None,
        "preferred_synonyms": [],
        "all_synonyms": [],
    })

    with open(DESCRIPTION_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row["active"] != "1":
                continue
            cid = row["conceptId"]
            if cid not in active_concepts:
                continue

            desc_id = row["id"]
            term    = row["term"].strip()
            type_id = row["typeId"]

            if type_id == TYPE_FSN:
                data[cid]["fsn"] = term
            elif type_id == TYPE_SYNONYM and term:
                data[cid]["all_synonyms"].append(term)
                if desc_id in preferred_desc_ids:
                    data[cid]["preferred_synonyms"].append(term)

    valid = {k: v for k, v in data.items() if v["fsn"]}
    logger.info(f"Concepts with FSN: {len(valid):,}")

    # 統計同義詞覆蓋
    with_pt = sum(1 for v in valid.values() if v["preferred_synonyms"])
    logger.info(f"  有 Preferred synonym: {with_pt:,} / {len(valid):,}")
    return valid


# ------------------------------------------------------------------
# Step 4: 載入 SNOMED → ICD-10 對照
# ------------------------------------------------------------------
def load_icd10_mapping(active_concepts: set[str]) -> dict[str, list[str]]:
    codes: dict[str, set[str]] = defaultdict(set)
    with open(EXTMAP_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row["active"] != "1":
                continue
            cid    = row["referencedComponentId"]
            if cid not in active_concepts:
                continue
            target = (row.get("mapTarget") or "").strip()
            if target and not target.startswith("NCEP") and target != "...":
                codes[cid].add(target)
    return {cid: sorted(v) for cid, v in codes.items()}


# ------------------------------------------------------------------
# 工具函式
# ------------------------------------------------------------------
def extract_semantic_tag(fsn: str) -> str:
    m = re.search(r'\(([^)]+)\)$', fsn.strip())
    return m.group(1) if m else ""


def get_existing_concept_ids() -> set[str]:
    db = SnomedSession()
    try:
        rows = db.execute(text("SELECT snomed_concept_id FROM documents")).fetchall()
        return {r[0] for r in rows}
    finally:
        db.close()


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main(dry_run: bool = False, limit: int = 0):
    settings = get_settings()
    embedder = EmbeddingService(model_name=settings.sentence_transformer_model)

    logger.info("載入 RF2 資料...")
    active           = load_active_concepts()
    preferred_ids    = load_preferred_desc_ids()
    desc_map         = load_descriptions(active, preferred_ids)
    icd10_map        = load_icd10_mapping(active)

    existing         = get_existing_concept_ids()
    to_process       = [cid for cid in desc_map if cid not in existing]
    if limit:
        to_process = to_process[:limit]

    logger.info(
        f"待處理: {len(to_process):,} / 總計: {len(desc_map):,} "
        f"(已跳過 {len(existing):,} 個已存在)"
    )

    if dry_run:
        term_counts = [
            1 + len(desc_map[cid]["preferred_synonyms"])
            for cid in to_process
        ]
        total_terms = sum(term_counts)
        logger.info(
            f"[DRY RUN] 預計新增: {len(to_process):,} concepts, "
            f"{total_terms:,} embedding rows"
        )
        logger.info(f"  平均每 concept: {total_terms / max(len(to_process), 1):.1f} 個 term")
        return

    total   = len(to_process)
    success = 0
    fail    = 0

    for i, cid in enumerate(to_process, 1):
        info              = desc_map[cid]
        fsn               = info["fsn"]
        pref_synonyms     = info["preferred_synonyms"]
        all_synonyms      = info["all_synonyms"]
        semantic_tag      = extract_semantic_tag(fsn)
        icd10_codes       = icd10_map.get(cid, [])

        # 只 embed FSN + Preferred synonyms（去重）
        seen: set[str]       = {fsn}
        embed_terms: list[str] = [fsn]
        for syn in pref_synonyms:
            if syn not in seen:
                seen.add(syn)
                embed_terms.append(syn)

        try:
            vectors = embedder.embed_documents(embed_terms, batch_size=EMBED_BATCH)

            with get_snomed_context() as db:
                doc = Document(
                    snomed_concept_id=cid,
                    fsn=fsn,
                    synonyms=all_synonyms if all_synonyms else None,
                    semantic_tag=semantic_tag or None,
                    icd10_codes=icd10_codes if icd10_codes else None,
                    source="RF2",
                )
                db.add(doc)
                db.flush()

                for term, vector in zip(embed_terms, vectors):
                    db.add(DocumentEmbedding(
                        document_id=doc.id,
                        term=term,
                        embedding=vector,
                    ))

            success += 1

        except Exception as e:
            logger.error(f"concept {cid} 失敗: {e}")
            fail += 1

        if i % 1000 == 0 or i == total:
            logger.info(
                f"進度: {i:,}/{total:,} ({i/total*100:.1f}%) "
                f"success={success:,} fail={fail:,}"
            )

    logger.info(f"完成：success={success:,}, fail={fail:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import SNOMED CT RF2 into PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="統計但不寫入 DB")
    parser.add_argument("--limit",   type=int, default=0, help="只處理前 N 個 concept（測試用）")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit)
