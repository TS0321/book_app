from datetime import datetime, date, time, timedelta, timezone
import os, csv, io
from pathlib import Path

from fastapi import FastAPI, Request, Form, status, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import asyncio
import aiosmtplib
import httpx

load_dotenv()
JST = timezone(timedelta(hours=9))

# --- DB ---
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./reservations.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at   = Column(DateTime(timezone=True), nullable=False)
    minutes  = Column(Integer, nullable=False, default=30)
    status   = Column(String(20), nullable=False, default="Booked")  # Booked/Done/Cancel
    fee_jpy  = Column(Integer, nullable=True)  # Done時に1000
    memo     = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(JST))

Base.metadata.create_all(engine)

app = FastAPI(title="Home Pilates Booking")

# --- TEMPLATES & STATIC ---
BASE_DIR = Path(__file__).parent
(static := BASE_DIR / "static").mkdir(exist_ok=True)
(templates_dir := BASE_DIR / "templates").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static)), name="static")
templates = Jinja2Templates(directory=str(templates_dir))

# --- UTIL ---
def dt_merge(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=JST)

def _to_aware_jst(dt: datetime) -> datetime:
    # tzinfo が無ければ JST として扱う
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=JST)

def overlaps(s1: datetime, e1: datetime, s2: datetime, e2: datetime) -> bool:
    # naive/aware 混在でも安全に比較できるよう正規化
    s1 = _to_aware_jst(s1); e1 = _to_aware_jst(e1)
    s2 = _to_aware_jst(s2); e2 = _to_aware_jst(e2)
    return not (e1 <= s2 or e2 <= s1)

async def notify(subject: str, message: str):
    # LINE Notify
    if os.getenv("LINE_TOKEN"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://notify-api.line.me/api/notify",
                    headers={"Authorization": f"Bearer {os.getenv('LINE_TOKEN')}"},
                    data={"message": f"{subject}\n{message}"}
                )
        except Exception:
            # 通知失敗はアプリを落とさない
            pass
    # Email
    if os.getenv("SMTP_HOST") and os.getenv("NOTIFY_TO"):
        try:
            await aiosmtplib.send(
                message=f"Subject: {subject}\r\nTo: {os.getenv('NOTIFY_TO')}\r\nFrom: {os.getenv('SMTP_USER')}\r\n\r\n{message}",
                hostname=os.getenv("SMTP_HOST"),
                port=int(os.getenv("SMTP_PORT", "587")),
                username=os.getenv("SMTP_USER"),
                password=os.getenv("SMTP_PASS"),
                start_tls=True,
            )
        except Exception:
            pass

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request, days: int = 7):
    db = SessionLocal()
    now = datetime.now(JST)
    until = now + timedelta(days=days)
    items = (db.query(Booking)
               .filter(Booking.start_at >= now - timedelta(hours=12))
               .filter(Booking.start_at <= until)
               .order_by(Booking.start_at.asc())
               .all())
    db.close()
    # 取り出し直後に tz を正規化しておくとテンプレ側でも安全
    for b in items:
        b.start_at = _to_aware_jst(b.start_at)
        b.end_at   = _to_aware_jst(b.end_at)

    # 日付ごとにグループ化
    grouped = {}
    for b in items:
        key = b.start_at.astimezone(JST).date()
        grouped.setdefault(key, []).append(b)
    return templates.TemplateResponse("index.html", {"request": request, "grouped": grouped, "days": days})

@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request):
    return templates.TemplateResponse("new.html", {"request": request})

@app.post("/new")
async def create_booking(
    request: Request,
    name: str = Form(...),
    date_str: str = Form(...),          # yyyy-mm-dd
    start_time: str = Form(...),        # "HH:MM" or "HH:MM:SS" にも対応
    minutes: int = Form(30),
    memo: str = Form(""),
):
    # 日付
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    # 時刻（秒付きにも対応）
    parts = start_time.split(":")
    h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    s = dt_merge(d, time(h, m))
    e = s + timedelta(minutes=minutes)

    db = SessionLocal()
    # 重複チェック（Cancel以外）
    exists = db.query(Booking).filter(Booking.status != "Cancel").all()
    for b in exists:
        if overlaps(b.start_at, b.end_at, s, e):
            db.close()
            return templates.TemplateResponse("new.html", {
                "request": request,
                "error": "同時間帯に既存の予約があるため作成できません。",
                "prefill": {"name": name, "date_str": date_str, "start_time": start_time, "minutes": minutes, "memo": memo}
            })

    # 作成
    bk = Booking(name=name, start_at=s, end_at=e, minutes=minutes, memo=memo)
    db.add(bk); db.commit(); db.refresh(bk); db.close()

    # 通知（安全に fire-and-forget）
    subj = f"【予約】{bk.start_at.strftime('%Y/%m/%d %H:%M')} {name}"
    body = f"{name}\n{bk.start_at.strftime('%Y/%m/%d %H:%M')} - {bk.end_at.strftime('%H:%M')}（{minutes}分）\n{memo or ''}"
    try:
        asyncio.get_running_loop().create_task(notify(subj, body))
    except RuntimeError:
        asyncio.run(notify(subj, body))

    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/booking/{bid}/status")
def update_status(bid: int, action: str = Form(...)):
    db = SessionLocal()
    b = db.query(Booking).get(bid)
    if not b:
        db.close()
        return RedirectResponse("/", status_code=303)
    if action == "done":
        b.status = "Done"
        if b.fee_jpy is None:
            b.fee_jpy = 1000
    elif action == "cancel":
        b.status = "Cancel"
    elif action == "book":
        b.status = "Booked"
        b.fee_jpy = None
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.get("/export.csv")
def export_csv():
    db = SessionLocal()
    rows = db.query(Booking).order_by(Booking.start_at.desc()).all()
    db.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id","name","start_at","end_at","minutes","status","fee_jpy","memo","created_at"])
    for r in rows:
        w.writerow([
            r.id, r.name,
            _to_aware_jst(r.start_at).isoformat(),
            _to_aware_jst(r.end_at).isoformat(),
            r.minutes, r.status, r.fee_jpy or "", r.memo or "",
            _to_aware_jst(r.created_at).isoformat() if r.created_at else ""
        ])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=bookings.csv"
    })
