from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from pysearch.index import Index
from pysearch.search import SearchEngine


class ResultModel(BaseModel):
    doc_id: int
    url: str
    title: str
    snippet: str
    highlights: list[tuple[int, int]]
    score: float


class SearchModel(BaseModel):
    query: str
    total: int
    took_ms: float
    results: list[ResultModel]


class StatsModel(BaseModel):
    documents: int
    terms: int
    avg_length: float


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PySearch</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--muted:#6b6b70;--link:#1a56db;--line:#e4e4e7}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#f2f2f3;--muted:#9a9aa0;--link:#7aa7ff;--line:#2e2e32}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px}
h1{font-size:28px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:16px;border:1px solid var(--line);
border-radius:10px;background:transparent;color:inherit}
.meta{color:var(--muted);font-size:14px;margin:12px 0}
.r{padding:14px 0;border-bottom:1px solid var(--line)}
.r a{color:var(--link);font-size:18px;text-decoration:none}
.u{color:var(--muted);font-size:13px;word-break:break-all}
.s{margin-top:4px}
mark{background:#fde68a;color:#1c1c1e;border-radius:3px;padding:0 1px}
</style></head><body><main>
<h1>PySearch</h1>
<input id="q" placeholder="Search the crawled pages…" autofocus>
<div class="meta" id="meta"></div><div id="out"></div>
</main>
<script>
const q=document.getElementById('q'),out=document.getElementById('out'),meta=document.getElementById('meta');
let timer;
async function run(){
  const text=q.value.trim(); out.textContent=''; if(!text){meta.textContent='';return;}
  const res=await fetch('/search?q='+encodeURIComponent(text)); const data=await res.json();
  meta.textContent=data.total+' results in '+data.took_ms+' ms';
  for(const r of data.results){
    const box=document.createElement('div'); box.className='r';
    const a=document.createElement('a'); a.textContent=r.title;
    if(/^https?:\\/\\//.test(r.url)){a.href=r.url;a.rel='noopener noreferrer';}
    const u=document.createElement('div'); u.className='u'; u.textContent=r.url;
    const s=document.createElement('div'); s.className='s'; let pos=0;
    for(const [from,to] of r.highlights){
      s.append(document.createTextNode(r.snippet.slice(pos,from)));
      const m=document.createElement('mark'); m.textContent=r.snippet.slice(from,to); s.append(m); pos=to;
    }
    s.append(document.createTextNode(r.snippet.slice(pos)));
    box.append(a,u,s); out.append(box);
  }
}
q.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(run,250);});
</script></body></html>"""


def create_app(db_path: str | Path = "pysearch.db") -> FastAPI:
    index = Index(db_path)
    engine = SearchEngine(index)
    app = FastAPI(title="PySearch", version="0.2.1")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> str:
        return PAGE

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/stats", response_model=StatsModel)
    def stats() -> StatsModel:
        s = index.stats()
        return StatsModel(documents=s.documents, terms=s.terms, avg_length=s.avg_length)

    @app.get("/search", response_model=SearchModel)
    def search(
        q: str = Query(..., min_length=1, max_length=200),
        limit: int = Query(10, ge=1, le=50),
        offset: int = Query(0, ge=0),
    ) -> SearchModel:
        r = engine.search(q, limit=limit, offset=offset)
        return SearchModel(
            query=r.query,
            total=r.total,
            took_ms=r.took_ms,
            results=[ResultModel(**vars(x)) for x in r.results],
        )

    return app
