from __future__ import annotations

import argparse
import logging

from app.db.session import SessionLocal
from app.services.internal_candidate_embedding_service import bulk_index_candidate_embeddings


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill internal candidate resume embeddings into Qdrant.")
    parser.add_argument("--agency-id", default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        result = bulk_index_candidate_embeddings(db=db, agency_id=args.agency_id, batch_size=args.batch_size or 64)
    print(result)


if __name__ == "__main__":
    main()
