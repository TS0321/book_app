from datetime import datetime, date, time, timedelta, timezone
import os, csv, io
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, Form, status, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, func, text
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
    fee_jpy  = Column(Integer, nullable=True)  # Done 時に 1000
    memo     = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(JST))

Base.metadata.create_all(engine)

# 索引（初回だけ作成される）
with engine.begin() as conn:
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_bookings_start_at ON bookings(start_at)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_bookings_status_start ON bookings(status, start_at)"))

app = FastAPI(title="Home Pilates Booking")

# CORS（LAN の Streamlit から呼べるよう緩め）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

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
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=JST)

def overlaps(s1: datetime, e1: datetime, s2: datetime, e2: datetime) -> bool:
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

# ---------------- Web画面（既存） ----------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, days: int = 7, booking_created: Optional[str] = None):
    db = SessionLocal()
    now = datetime.now(JST)
    # 今日 0:00 から表示（同日の過去も見えるように）
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    until = start + timedelta(days=days)
    items = (db.query(Booking)
               .filter(Booking.start_at >= start)
               .filter(Booking.start_at <  until)
               .order_by(Booking.start_at.asc())
               .all())
    db.close()
    for b in items:
        b.start_at = _to_aware_jst(b.start_at)
        b.end_at   = _to_aware_jst(b.end_at)
    grouped = {}
    for b in items:
        key = b.start_at.astimezone(JST).date()
        grouped.setdefault(key, []).append(b)
    return templates.TemplateResponse("index.html", {
        "request": request, "grouped": grouped, "days": days,
        "booking_created": booking_created,
    })

@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request):
    return templates.TemplateResponse("new.html", {"request": request})

@app.post("/new")
async def create_booking(
    request: Request,
    name: str = Form(...),
    date_str: str = Form(...),          # yyyy-mm-dd
    start_time: str = Form(...),        # HH:MM or HH:MM:SS
    minutes: int = Form(30),
    memo: str = Form(""),
):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    parts = start_time.split(":"); h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    s = dt_merge(d, time(h, m)); e = s + timedelta(minutes=minutes)

    # 過去禁止
    if s < datetime.now(JST):
        return templates.TemplateResponse("new.html", {
            "request": request,
            "error": "過去の時刻には予約できません（現在以降を選んでください）。",
            "prefill": {"name": name, "date_str": date_str, "start_time": start_time, "minutes": minutes, "memo": memo}
        }, status_code=status.HTTP_400_BAD_REQUEST)

    db = SessionLocal()
    exists = db.query(Booking).filter(Booking.status != "Cancel").all()
    for b in exists:
        if overlaps(b.start_at, b.end_at, s, e):
            db.close()
            return templates.TemplateResponse("new.html", {
                "request": request,
                "error": "同時間帯に既存の予約があるため作成できません。",
                "prefill": {"name": name, "date_str": date_str, "start_time": start_time, "minutes": minutes, "memo": memo}
            })
    bk = Booking(name=name, start_at=s, end_at=e, minutes=minutes, memo=memo)
    db.add(bk); db.commit(); db.refresh(bk); db.close()

    subj = f"【予約】{bk.start_at.strftime('%Y/%m/%d %H:%M')} {name}"
    body = f"{name}\n{bk.start_at.strftime('%Y/%m/%d %H:%M')} - {bk.end_at.strftime('%H:%M')}（{minutes}分）\n{memo or ''}"
    try:
        asyncio.get_running_loop().create_task(notify(subj, body))
    except RuntimeError:
        asyncio.run(notify(subj, body))

    return RedirectResponse("/?booking_created=1", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/booking/{bid}/status")
def update_status(bid: int, action: str = Form(...)):
    db = SessionLocal()
    b = db.query(Booking).get(bid)
    if not b:
        db.close(); return RedirectResponse("/", status_code=303)
    if action == "done":
        b.status = "Done"; b.fee_jpy = b.fee_jpy or 1000
    elif action == "cancel":
        b.status = "Cancel"
    elif action == "book":
        b.status = "Booked"; b.fee_jpy = None
    db.commit(); db.close()
    return RedirectResponse("/", status_code=303)

@app.get("/export.csv")
def export_csv():
    db = SessionLocal(); rows = db.query(Booking).order_by(Booking.start_at.desc()).all(); db.close()
    buf = io.StringIO(); w = csv.writer(buf)
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
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bookings.csv"})

# ---------------- API ----------------
class BookingIn(BaseModel):
    name: str = Field(..., max_length=100)
    start_date: date
    start_time: str  # "HH:MM" or "HH:MM:SS"
    minutes: int = 30
    memo: Optional[str] = ""

class BookingOut(BaseModel):
    id: int
    name: str
    start_at: datetime
    end_at: datetime
    minutes: int
    status: str
    fee_jpy: Optional[int]
    memo: Optional[str]

class StatusIn(BaseModel):
    action: str  # "book" | "done" | "cancel"

@app.get("/api/bookings", response_model=List[BookingOut])
def api_list_bookings(
    fr: Optional[datetime] = None,
    to: Optional[datetime] = None,
    status_eq: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    db = SessionLocal()
    q = db.query(Booking)
    if fr: q = q.filter(Booking.start_at >= fr)
    if to: q = q.filter(Booking.start_at <= to)
    if status_eq: q = q.filter(Booking.status == status_eq)
    rows = q.order_by(Booking.start_at.asc()).limit(limit).offset(offset).all()
    db.close()
    return [
        BookingOut(
            id=r.id, name=r.name,
            start_at=_to_aware_jst(r.start_at), end_at=_to_aware_jst(r.end_at),
            minutes=r.minutes, status=r.status, fee_jpy=r.fee_jpy, memo=r.memo
        ) for r in rows
    ]

@app.post("/api/bookings", response_model=BookingOut, status_code=201)
def api_create_booking(payload: BookingIn):
    parts = payload.start_time.split(":")
    h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
    s = dt_merge(payload.start_date, time(h, m)); e = s + timedelta(minutes=payload.minutes)

    # 過去禁止（API でも明示）
    if s < datetime.now(JST):
        raise HTTPException(status_code=400, detail="Cannot create booking in the past.")

    db = SessionLocal()
    exists = db.query(Booking).filter(Booking.status != "Cancel").all()
    for b in exists:
        if overlaps(b.start_at, b.end_at, s, e):
            db.close()
            raise HTTPException(status_code=409, detail="Time slot overlaps an existing booking.")
    bk = Booking(name=payload.name, start_at=s, end_at=e, minutes=payload.minutes, memo=payload.memo or "")
    db.add(bk); db.commit(); db.refresh(bk); db.close()

    subj = f"【予約】{bk.start_at.strftime('%Y/%m/%d %H:%M')} {bk.name}"
    body = f"{bk.name}\n{bk.start_at.strftime('%Y/%m/%d %H:%M')} - {bk.end_at.strftime('%H:%M')}（{payload.minutes}分）\n{payload.memo or ''}"
    try:
        asyncio.get_running_loop().create_task(notify(subj, body))
    except RuntimeError:
        asyncio.run(notify(subj, body))

    return BookingOut(
        id=bk.id, name=bk.name, start_at=bk.start_at, end_at=bk.end_at,
        minutes=bk.minutes, status=bk.status, fee_jpy=bk.fee_jpy, memo=bk.memo
    )

@app.post("/api/bookings/{bid}/status", response_model=BookingOut)
def api_update_status(bid: int, payload: StatusIn):
    db = SessionLocal(); b = db.query(Booking).get(bid)
    if not b:
        db.close(); raise HTTPException(404, "Booking not found")
    if payload.action == "done":
        b.status = "Done"; b.fee_jpy = b.fee_jpy or 1000
    elif payload.action == "cancel":
        b.status = "Cancel"
    elif payload.action == "book":
        b.status = "Booked"; b.fee_jpy = None
    else:
        db.close(); raise HTTPException(400, "Invalid action")
    db.commit(); db.refresh(b); db.close()
    return BookingOut(
        id=b.id, name=b.name,
        start_at=_to_aware_jst(b.start_at), end_at=_to_aware_jst(b.end_at),
        minutes=b.minutes, status=b.status, fee_jpy=b.fee_jpy, memo=b.memo
    )

@app.get("/api/stats/monthly")
def api_stats_monthly(year: int, month: int):
    # 月初〜月末（JST）
    start = datetime(year, month, 1, 0, 0, tzinfo=JST)
    if month == 12:
        end = datetime(year+1, 1, 1, 0, 0, tzinfo=JST) - timedelta(seconds=1)
    else:
        end = datetime(year, month+1, 1, 0, 0, tzinfo=JST) - timedelta(seconds=1)

    db = SessionLocal()
    rows = (db.query(
                func.count(Booking.id).label("done_count"),
                func.coalesce(func.sum(Booking.fee_jpy), 0).label("total_fee")
            )
            .filter(Booking.status == "Done")
            .filter(Booking.start_at >= start)
            .filter(Booking.start_at <= end)
            .one())
    db.close()
    return {"year": year, "month": month, "done_count": rows.done_count, "total_fee": rows.total_fee}

@app.delete("/api/bookings/{bid}", status_code=204)
def api_delete_booking(bid: int):
    """予約を完全削除する"""
    db = SessionLocal()
    b = db.query(Booking).get(bid)
    if not b:
        db.close()
        raise HTTPException(status_code=404, detail="Booking not found")
    db.delete(b)
    db.commit()
    db.close()
    return  # 204 No Content

# --- DBモデル ---
class Feedback(Base):
    __tablename__ = "feedbacks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(JST))

Base.metadata.create_all(engine)

# --- API ---
from pydantic import BaseModel
class FeedbackIn(BaseModel):
    text: str

class FeedbackOut(BaseModel):
    id: int
    text: str
    created_at: datetime

@app.post("/api/feedback", response_model=FeedbackOut, status_code=201)
def api_create_feedback(payload: FeedbackIn):
    db = SessionLocal()
    fb = Feedback(text=payload.text)
    db.add(fb)
    db.commit()
    db.refresh(fb)
    db.close()
    return FeedbackOut(id=fb.id, text=fb.text, created_at=_to_aware_jst(fb.created_at))

@app.get("/api/feedback", response_model=List[FeedbackOut])
def api_list_feedback():
    db = SessionLocal()
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    db.close()
    return [
        FeedbackOut(id=r.id, text=r.text, created_at=_to_aware_jst(r.created_at))
        for r in rows
    ]
