"""Radar KaBuM via Product Data Feed da Awin. Diagnóstico por padrão; não publica."""
import argparse
import csv
import gzip
import io
import os
import sqlite3
import time
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

DB_NAME = "kabum_historico.sqlite3"

def money(v):
    try:
        d=Decimal(str(v).strip().replace("R$","").replace(" ","").replace(",","."))
        return int(d*100) if d>0 else None
    except (InvalidOperation, ValueError):
        return None

def open_feed(source):
    if source.startswith(("http://","https://")):
        with urllib.request.urlopen(source, timeout=60) as r: raw=r.read()
    else:
        raw=Path(source).read_bytes()
    if raw[:2]==b"\x1f\x8b": raw=gzip.decompress(raw)
    return io.StringIO(raw.decode("utf-8-sig", errors="replace"), newline="")

def init_db(path):
    db=sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS kabum_products(
      product_id TEXT PRIMARY KEY, name TEXT NOT NULL, brand TEXT, category TEXT,
      price_cents INTEGER NOT NULL, affiliate_url TEXT NOT NULL, image_url TEXT,
      in_stock TEXT, last_seen REAL NOT NULL, payload TEXT);
    CREATE TABLE IF NOT EXISTS kabum_price_history(
      id INTEGER PRIMARY KEY, product_id TEXT NOT NULL, price_cents INTEGER NOT NULL,
      observed REAL NOT NULL, UNIQUE(product_id, price_cents, observed));
    CREATE INDEX IF NOT EXISTS kabum_hist_idx ON kabum_price_history(product_id, observed);
    """)
    return db

def ingest(source, db):
    now=time.time(); total=valid=0
    reader=csv.DictReader(open_feed(source))
    required={"aw_deep_link","product_name","merchant_product_id","search_price"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Feed sem colunas obrigatórias: "+", ".join(sorted(required-set(reader.fieldnames or []))))
    with db:
        for row in reader:
            total+=1
            pid=(row.get("merchant_product_id") or row.get("aw_product_id") or "").strip()
            price=money(row.get("search_price"))
            link=(row.get("aw_deep_link") or "").strip()
            if not pid or not price or not link: continue
            valid+=1
            old=db.execute("SELECT price_cents FROM kabum_products WHERE product_id=?",(pid,)).fetchone()
            if old is None or old[0]!=price:
                db.execute("INSERT INTO kabum_price_history(product_id,price_cents,observed) VALUES(?,?,?)",(pid,price,now))
            import json
            db.execute("""INSERT INTO kabum_products VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(product_id) DO UPDATE SET name=excluded.name,brand=excluded.brand,
              category=excluded.category,price_cents=excluded.price_cents,affiliate_url=excluded.affiliate_url,
              image_url=excluded.image_url,in_stock=excluded.in_stock,last_seen=excluded.last_seen,payload=excluded.payload""",
              (pid,(row.get("product_name") or "").strip(),(row.get("brand_name") or "").strip(),
               (row.get("merchant_product_category_path") or row.get("merchant_category") or "").strip(),
               price,link,(row.get("large_image") or row.get("merchant_image_url") or row.get("aw_image_url") or "").strip(),
               (row.get("in_stock") or row.get("stock_status") or "").strip(),now,json.dumps(row,ensure_ascii=False)))
    return total,valid

def drops(db, minimum_pct=5, limit=20):
    rows=db.execute("""SELECT p.product_id,p.name,p.brand,p.price_cents,p.affiliate_url,p.image_url,
      (SELECT h.price_cents FROM kabum_price_history h WHERE h.product_id=p.product_id
       AND h.price_cents>p.price_cents ORDER BY h.observed DESC LIMIT 1) previous
      FROM kabum_products p ORDER BY p.last_seen DESC""").fetchall()
    out=[]
    for r in rows:
        if r[6]:
            pct=(r[6]-r[3])*100/r[6]
            if pct>=minimum_pct: out.append((*r,pct))
    return sorted(out,key=lambda x:x[-1],reverse=True)[:limit]

def main():
    base=Path(__file__).resolve().parent
    try:
        from dotenv import load_dotenv; load_dotenv(base/".env",encoding="utf-8-sig")
    except ImportError: pass
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arquivo",help="CSV ou CSV.GZ local da Awin.")
    ap.add_argument("--url",help="URL privada do feed; prefira KABUM_AWIN_FEED_URL no .env.")
    ap.add_argument("--queda-min",type=float,default=5.0)
    ap.add_argument("--limite",type=int,default=20)
    args=ap.parse_args()
    source=args.arquivo or args.url or os.getenv("KABUM_AWIN_FEED_URL")
    if not source: ap.error("Informe --arquivo ou configure KABUM_AWIN_FEED_URL no .env.")
    db=init_db(base/DB_NAME)
    try:
        total,valid=ingest(source,db)
        print(f"KaBuM/Awin: {total} linhas lidas; {valid} produtos válidos registrados.")
        ds=drops(db,args.queda_min,args.limite)
        if not ds:
            print("Nenhuma queda comprovada ainda. A primeira coleta cria a linha de base do histórico.")
        else:
            print(f"Quedas comprovadas >= {args.queda_min:g}%:")
            for r in ds:
                print(f"- {r[1]} | R$ {r[3]/100:.2f} | antes R$ {r[6]/100:.2f} | queda {r[7]:.1f}%")
        print("Modo diagnóstico: nenhuma publicação foi enviada.")
    finally: db.close()

if __name__=="__main__": main()
