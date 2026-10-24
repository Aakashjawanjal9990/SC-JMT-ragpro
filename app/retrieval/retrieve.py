"""Query the retriever from the command line.

    python -m app.retrieval.retrieve "anticipatory bail under section 438 CrPC" \
        --top-k 5 --mode hybrid [--rerank] [--repeat 3]
"""

import argparse
import json

from app.retrieval.hybrid import MODES, HybridRetriever

STAGES = ("embed", "dense", "bm25", "fusion", "rerank", "hydrate", "total")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a retrieval query")
    parser.add_argument("query", nargs="+", help="One or more queries")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--candidate-k", type=int, default=None)
    parser.add_argument("--mode", choices=MODES, default=None)
    parser.add_argument("--rerank", action="store_true", help="Enable the cross-encoder")
    parser.add_argument("--max-per-doc", type=int, default=None)
    parser.add_argument("--filters", type=json.loads, default=None, help="JSON filter spec")
    parser.add_argument(
        "--repeat", type=int, default=1, help="Run each query N times; timings are from the last"
    )
    args = parser.parse_args(argv)

    options = {
        "mode": args.mode,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "filters": args.filters,
        "rerank": True if args.rerank else None,
    }
    if args.max_per_doc is not None:
        options["max_per_doc"] = args.max_per_doc or None

    with HybridRetriever.from_settings() as retriever:
        if args.rerank and retriever.reranker is not None:
            retriever.reranker.warmup()
        retriever.warmup()
        for query in args.query:
            for _ in range(max(args.repeat, 1)):
                result = retriever.search(query, **options)
            print(f"\nQuery: {query}  [mode={result.mode}]")
            print(
                "Latency ms: "
                + "  ".join(
                    f"{stage}={result.timings_ms[stage]:.1f}"
                    for stage in STAGES
                    if stage in result.timings_ms
                )
            )
            if not result.chunks:
                print("  (no results)")
            for i, chunk in enumerate(result.chunks, start=1):
                snippet = " ".join(chunk.text.split())[:150]
                print(f"{i:>2}. {chunk.score:.4f}  {chunk.citation}  {snippet}")


if __name__ == "__main__":
    main()
