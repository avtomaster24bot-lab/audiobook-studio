"""Демо end-to-end без веб-сервера.

    python scripts/demo.py "Хамелеон Чехов" --edits scripts/demo_hameleon_edits.json --out docs/demo

Ищет текст в открытых источниках, размечает, озвучивает, накладывает музыку с ducking, нормализует до −16 LUFS,
печатает отчёт об уровнях (голос vs музыка под речью).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app import pipeline  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--edits", help="JSON с ручной правкой атрибуции/голосов")
    ap.add_argument("--out", default="demo_out")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    t = time.time()
    prep = pipeline.prepare_book({"query": args.query}, lambda p, s: print(f"[{p:4.0%}] {s}"))
    bid = prep["book_id"]
    if args.edits:
        e = json.loads(Path(args.edits).read_text())
        pipeline.update_analysis(bid, speakers=e.get("speakers"), emotions=e.get("emotions"), voices=e.get("voices"))
    summary = pipeline.book_summary(bid)
    print(json.dumps({k: summary[k] for k in ("title", "author", "source_url", "words", "analysis_method", "voices")},
                     ensure_ascii=False, indent=1))
    job_dir = config.DATA_DIR / "jobs" / f"demo-{bid}"
    job_dir.mkdir(parents=True, exist_ok=True)
    res = pipeline.render(job_dir, bid, {"preview": args.preview, "formats": ["mp3", "m4b"], "keep_work": True},
                          lambda p, s: print(f"[{p:4.0%}] {s}"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in res["files"].values():
        shutil.copy(job_dir / f, out / f)
    (out / "report.json").write_text(json.dumps({"book": summary, "result": res}, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"Готово за {time.time() - t:.0f} с → {out.resolve()}")


if __name__ == "__main__":
    main()
