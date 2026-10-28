"""
Export Chunks and 768-Dim Embeddings to JSON & JSONL Deliverables
Generates complete, verifiable JSON files containing:
1. chunks.jsonl & chunks_with_embeddings.jsonl (each chunk with full text & 768-dim vector)
2. embeddings.json (mapping of chunk_id -> 768-dim vector)
3. sample_chunks_preview.json (indented preview for inspection)
"""

import sqlite3
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "output" / "scj_rag_v2.db"
OUT_DIR = REPO_ROOT / "output" / "local_ocr" / "data_folder_test"

OUT_DIR.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

query = """
SELECT 
    sf.original_filename,
    c.chunk_uuid,
    c.chunk_index,
    c.page_start,
    c.page_end,
    c.section,
    c.metadata,
    c.content,
    c.embedding
FROM chunks c
JOIN source_files sf ON c.source_file_uuid = sf.source_file_uuid
WHERE c.is_active = 1
ORDER BY sf.original_filename, c.chunk_index;
"""

cur.execute(query)
rows = cur.fetchall()
print(f"Total active chunks fetched: {len(rows)}")

all_chunks = []
embeddings_dict = {}
preview_samples = []

for r in rows:
    filename, chunk_uuid, chunk_idx, p_start, p_end, section, meta_raw, content, emb_raw = r
    doc_id = filename.replace(".pdf", "")
    chunk_id = f"{doc_id}_p{p_start}_c{chunk_idx + 1}"
    
    vec = json.loads(emb_raw) if emb_raw else []
    norm = math.sqrt(sum(x * x for x in vec)) if vec else 0.0
    meta = json.loads(meta_raw) if meta_raw else {}
    
    chunk_obj = {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "original_filename": filename,
        "page_start": p_start,
        "page_end": p_end,
        "chunk_index": chunk_idx,
        "section": section,
        "metadata": meta,
        "content": content,
        "embedding": vec,
        "embedding_dim": len(vec),
        "embedding_l2_norm": round(norm, 5)
    }
    
    all_chunks.append(chunk_obj)
    embeddings_dict[chunk_id] = {
        "document_id": doc_id,
        "original_filename": filename,
        "page_start": p_start,
        "page_end": p_end,
        "chunk_index": chunk_idx,
        "embedding_dim": len(vec),
        "embedding_l2_norm": round(norm, 5),
        "vector": vec
    }
    
    # Collect 5 diverse samples across eras for preview
    if len(preview_samples) < 5 and p_start == 1:
        preview_samples.append({
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "original_filename": filename,
            "page_start": p_start,
            "page_end": p_end,
            "chunk_index": chunk_idx,
            "section": section,
            "content_preview": content[:250] + "...",
            "embedding_dimension": len(vec),
            "embedding_l2_norm": round(norm, 5),
            "embedding_first_10_dims": [round(x, 5) for x in vec[:10]]
        })

# 1. Write updated chunks.jsonl
chunks_jsonl_path = OUT_DIR / "chunks.jsonl"
with open(chunks_jsonl_path, "w", encoding="utf-8") as f:
    for c in all_chunks:
        f.write(json.dumps(c) + "\n")
print(f"[OK] Saved chunks.jsonl: {chunks_jsonl_path} ({len(all_chunks)} records)")

# 2. Write chunks_with_embeddings.jsonl
chunks_with_emb_path = OUT_DIR / "chunks_with_embeddings.jsonl"
with open(chunks_with_emb_path, "w", encoding="utf-8") as f:
    for c in all_chunks:
        f.write(json.dumps(c) + "\n")
print(f"[OK] Saved chunks_with_embeddings.jsonl: {chunks_with_emb_path} ({len(all_chunks)} records)")

# 3. Write embeddings.json
embeddings_json_path = OUT_DIR / "embeddings.json"
with open(embeddings_json_path, "w", encoding="utf-8") as f:
    json.dump(embeddings_dict, f)
print(f"[OK] Saved embeddings.json: {embeddings_json_path} ({len(embeddings_dict)} vectors)")

# 4. Write pretty preview sample_chunks_preview.json
preview_path = OUT_DIR / "sample_chunks_preview.json"
with open(preview_path, "w", encoding="utf-8") as f:
    json.dump(preview_samples, f, indent=2)
print(f"[OK] Saved sample_chunks_preview.json: {preview_path} ({len(preview_samples)} samples)")

con.close()

