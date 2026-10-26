"""Query the retriever from the command line.

    python -m app.retrieval.retrieve "anticipatory bail under section 438 CrPC" --top-k 5
"""

import argparse

from app.retrieval.hybrid import HybridRetriever

STAGES = ("embed", "vector", "bm25", "reread", "rerank", "total")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a retrieval query")
    parser.add_argument("query", nargs="+", help="One or more queries")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args(argv)

    with HybridRetriever.from_settings() as retriever:
        retriever.warmup()
        for query in args.query:
            result = retriever.search(query, top_k=args.top_k)
            print(f"\nQuery: {query}")
            print("Latency ms: " + "  ".join(
                f"{stage}={result.timings_ms[stage]:.1f}" for stage in STAGES if stage in result.timings_ms
            ))
            if not result.chunks:
                print("  (no results)")
            for i, chunk in enumerate(result.chunks, start=1):
                snippet = " ".join(chunk.text.split())[:150]
                print(f"{i:>2}. {chunk.score:.4f}  {chunk.citation}  {snippet}")


if __name__ == "__main__":
    main()
