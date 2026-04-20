import os
import json
from datetime import datetime
from typing import Optional

from fastapi import (
    FastAPI, Request, Depends, HTTPException,
    Form, UploadFile, File
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, outerjoin
from sqlalchemy import desc, func

from database import (
    SessionLocal, get_db, create_tables,
    User, Designer, Client, Period, Project
)
from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin, get_optional_user,
    COOKIE_NAME, _LoginRedirect, _ForbiddenRedirect
)
from excel import parse_studio_export, norm_name


app = FastAPI(title="Oxford Design Studio — Profitability Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# Client names that should never appear (Studio report summary rows)
BAD_CLIENT_NAMES = [
    'report total:', 'report total', 'client office expense',
    'grand total', 'total'
]

def is_bad_client(name: str) -> bool:
    n = name.strip().lower()
    return n in BAD_CLIENT_NAMES or n.startswith('report')


@app.on_event("startup")
def startup():
    create_tables()
    # Clean up any bad clients that slipped in from previous uploads
    db = SessionLocal()
    try:
        all_clients = db.query(Client).all()
        removed = 0
        for c in all_clients:
            if is_bad_client(c.name):
                # Remove associated projects first
                db.query(Project).filter(Project.client_id == c.id).delete()
                db.delete(c)
                removed += 1
        if removed:
            db.commit()
            print(f"Startup cleanup: removed {removed} bad client(s)")
    except Exception as e:
        print(f"Startup cleanup error: {e}")
        db.rollback()
    finally:
        db.close()


# ── Auth redirect handlers ───────────────────────────────────────────
@app.exception_handler(_LoginRedirect)
async def login_redirect_handler(request: Request, exc: _LoginRedirect):
    return RedirectResponse(url="/login", status_code=302)

@app.exception_handler(_ForbiddenRedirect)
async def forbidden_handler(request: Request, exc: _ForbiddenRedirect):
    return RedirectResponse(url="/", status_code=302)


# ═══════════════════════════════════════════
# ONE-TIME SETUP
# ═══════════════════════════════════════════

@app.get("/setup", response_class=HTMLResponse)
def setup(key: str = "", email: str = "", password: str = "", db: Session = Depends(get_db)):
    setup_key = os.environ.get("SETUP_KEY", "")
    if not setup_key or key != setup_key:
        return HTMLResponse("<h2>Invalid or missing setup key.</h2>", status_code=403)
    if not email or not password:
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;padding:20px;">
        <h2>Oxford Dashboard — First Time Setup</h2>
        <form method="get">
          <input type="hidden" name="key" value="{key}">
          <p><label>Email<br><input name="email" type="email" style="width:100%;padding:8px;" required></label></p>
          <p><label>Password<br><input name="password" type="password" style="width:100%;padding:8px;" required></label></p>
          <button type="submit" style="padding:10px 20px;background:#1c1a16;color:white;border:none;cursor:pointer;">
            Create Admin &amp; Seed Data</button>
        </form></body></html>""")
    from seed import seed
    try:
        seed(email, password)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;padding:20px;">
        <h2>✓ Setup complete</h2>
        <p>Admin created for <strong>{email}</strong>. All designers and clients seeded.</p>
        <p><a href="/login" style="background:#1c1a16;color:white;padding:10px 20px;text-decoration:none;">
          Go to Login →</a></p>
        </body></html>""")
    except Exception as e:
        return HTMLResponse(f"<h2>Error:</h2><pre>{e}</pre>", status_code=500)


# ═══════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_optional_user(request, db):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})
    token = create_token(user.id, user.role)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(COOKIE_NAME, token, httponly=True, max_age=60*60*24*7, samesite="lax")
    return response

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ═══════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def dashboard(
    request:      Request,
    period_id:    Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    periods = (
        db.query(Period)
        .filter(Period.is_duplicate == False)
        .order_by(desc(Period.uploaded_at))
        .all()
    )
    selected = db.query(Period).filter(Period.id == period_id).first() if period_id else (periods[0] if periods else None)

    projects_data    = []
    designer_summary = []

    if selected:
        # LEFT outer join so clients without a designer still appear
        projects = (
            db.query(Project)
            .filter(Project.period_id == selected.id)
            .join(Client, Project.client_id == Client.id)
            .outerjoin(Designer, Client.designer_id == Designer.id)
            .all()
        )
        for p in projects:
            des = p.client.designer
            projects_data.append({
                "client_id":      p.client_id,
                "client":         p.client.name,
                "designer":       des.name      if des else "Unassigned",
                "designer_label": des.label     if des else "Unassigned",
                "color_hex":      des.color_hex if des else "#aaaaaa",
                "revenue":        round(float(p.revenue or 0), 2),
                "profit":         round(float(p.profit or 0), 2),
                "time_billing":   round(float(p.time_billing or 0), 2),
                "margin":         round(float(p.margin or 0), 1),
                "monthly_data":   p.monthly_data or {},
                "date_min":       p.date_min,
                "date_max":       p.date_max,
            })

        des_map: dict = {}
        for p in projects_data:
            d = p["designer"]
            if d == "Unassigned":
                continue
            if d not in des_map:
                des_map[d] = {"name": d, "label": p["designer_label"],
                              "color_hex": p["color_hex"],
                              "revenue": 0.0, "profit": 0.0, "time_billing": 0.0, "count": 0}
            des_map[d]["revenue"]      += p["revenue"]
            des_map[d]["profit"]       += p["profit"]
            des_map[d]["time_billing"] += p["time_billing"]
            des_map[d]["count"]        += 1
        for d in des_map.values():
            d["margin"] = round(d["profit"] / d["revenue"] * 100, 1) if d["revenue"] else 0.0
        designer_summary = sorted(des_map.values(), key=lambda x: x["revenue"], reverse=True)

    designers = db.query(Designer).filter(Designer.active == True).order_by(Designer.name).all()

    return templates.TemplateResponse("dashboard.html", {
        "request":          request,
        "user":             current_user,
        "periods":          periods,
        "selected_period":  selected,
        "projects":         json.dumps(projects_data),
        "designer_summary": json.dumps(designer_summary),
        "designers":        designers,
    })


@app.post("/designers/retune-colors")
def retune_designer_colors(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """One-time: retune existing designer colors to Oxford brand palette."""
    oxford_palette = ['#444735', '#353738', '#b89653', '#6b6f57', '#8a7444', '#8a8e73', '#a88f4c', '#5a5e48']
    designers = db.query(Designer).order_by(Designer.id).all()
    for i, des in enumerate(designers):
        des.color_hex = oxford_palette[i % len(oxford_palette)]
    db.commit()
    return RedirectResponse(url="/designers", status_code=302)


@app.post("/periods/{period_id}/delete")
def delete_period(
    period_id:    int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    period = db.query(Period).filter(Period.id == period_id).first()
    if not period:
        raise HTTPException(status_code=404)
    # Delete all projects for this period first
    db.query(Project).filter(Project.period_id == period_id).delete()
    db.delete(period)
    db.commit()
    return RedirectResponse(url="/", status_code=302)


# ═══════════════════════════════════════════
# CLIENTS — list + detail + reassign
# ═══════════════════════════════════════════

@app.get("/clients", response_class=HTMLResponse)
def clients_list(
    request:      Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    clients = (
        db.query(Client)
        .outerjoin(Designer, Client.designer_id == Designer.id)
        .order_by(Client.name)
        .all()
    )
    designers = db.query(Designer).filter(Designer.active == True).order_by(Designer.name).all()

    # Aggregate lifetime metrics per client
    client_metrics = {}
    for c in clients:
        projects = db.query(Project).filter(Project.client_id == c.id).all()
        if projects:
            total_rev    = sum(p.revenue for p in projects)
            total_profit = sum(p.profit for p in projects)
            total_tb     = sum(p.time_billing for p in projects)
            margin       = round(total_profit / total_rev * 100, 1) if total_rev else 0.0
            periods_seen = len(set(p.period_id for p in projects))
        else:
            total_rev = total_profit = total_tb = margin = 0.0
            periods_seen = 0
        client_metrics[c.id] = {
            "revenue":      total_rev,
            "profit":       total_profit,
            "time_billing": total_tb,
            "margin":       margin,
            "periods":      periods_seen,
        }

    return templates.TemplateResponse("clients.html", {
        "request":        request,
        "user":           current_user,
        "clients":        clients,
        "client_metrics": client_metrics,
        "designers":      designers,
    })


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_detail(
    client_id:    int,
    request:      Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    designers = db.query(Designer).filter(Designer.active == True).order_by(Designer.name).all()

    # All periods this client appears in
    projects = (
        db.query(Project)
        .filter(Project.client_id == client_id)
        .join(Period)
        .order_by(desc(Period.uploaded_at))
        .all()
    )

    period_data = []
    for p in projects:
        period_data.append({
            "period_id":    p.period_id,
            "period_label": p.period.label,
            "period_date":  p.period.uploaded_at.strftime("%b %d, %Y"),
            "revenue":      round(p.revenue, 2),
            "profit":       round(p.profit, 2),
            "time_billing": round(p.time_billing, 2),
            "margin":       round(p.margin, 1),
        })

    # Lifetime totals
    total_rev    = sum(p["revenue"]      for p in period_data)
    total_profit = sum(p["profit"]       for p in period_data)
    total_tb     = sum(p["time_billing"] for p in period_data)
    avg_margin   = round(total_profit / total_rev * 100, 1) if total_rev else 0.0

    return templates.TemplateResponse("client_detail.html", {
        "request":      request,
        "user":         current_user,
        "client":       client,
        "designers":    designers,
        "period_data":  period_data,
        "total_rev":    total_rev,
        "total_profit": total_profit,
        "total_tb":     total_tb,
        "avg_margin":   avg_margin,
    })


@app.post("/clients/{client_id}/assign")
def assign_client_designer(
    client_id:    int,
    designer_id:  int = Form(...),
    ajax:         int = Form(default=0),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    client.designer_id = designer_id if designer_id != 0 else None
    db.commit()
    if ajax:
        des = db.query(Designer).filter(Designer.id == designer_id).first() if designer_id else None
        return JSONResponse({
            "ok": True,
            "designer_label": des.label     if des else None,
            "designer_color": des.color_hex if des else None,
        })
    return RedirectResponse(url=f"/clients/{client_id}", status_code=302)


# ═══════════════════════════════════════════
# UPLOAD — Step 1: parse
# ═══════════════════════════════════════════

@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    designers = db.query(Designer).filter(Designer.active == True).order_by(Designer.name).all()
    return templates.TemplateResponse("upload.html", {"request": request, "user": current_user, "designers": designers})


@app.post("/upload/parse")
async def upload_parse(file: UploadFile = File(...), current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    contents = await file.read()
    try:
        parsed = parse_studio_export(contents, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing = db.query(Period).filter(Period.filename == file.filename, Period.row_count == parsed["row_count"]).first()
    if existing:
        # Only treat as duplicate if the period actually has project data
        # An empty period means a previous upload failed — allow re-upload
        project_count = db.query(Project).filter(Project.period_id == existing.id).count()
        if project_count > 0:
            return JSONResponse({"duplicate": True, "duplicate_period": {
                "id": existing.id, "label": existing.label,
                "uploaded_at": existing.uploaded_at.strftime("%b %d, %Y")
            }})
        else:
            # Delete the empty period so a fresh upload can proceed
            db.delete(existing)
            db.commit()

    norm_index = {c.name_normalized: c for c in db.query(Client).all()}
    clients_out = []
    for c in parsed["clients"]:
        db_client = norm_index.get(c["name_normalized"])
        designer  = db_client.designer if db_client else None
        clients_out.append({
            "name":            c["name"],
            "name_normalized": c["name_normalized"],
            "revenue":         c["revenue"],
            "profit":          c["profit"],
            "time_billing":    c["time_billing"],
            "margin":          c["margin"],
            "monthly_data":    c.get("monthly_data", {}),
            "date_min":        c.get("date_min"),
            "date_max":        c.get("date_max"),
            "designer_id":     designer.id    if designer else None,
            "designer_name":   designer.name  if designer else None,
            "designer_label":  designer.label if designer else None,
            "is_new":          db_client is None,
        })

    return JSONResponse({"duplicate": False, "filename": file.filename,
                         "row_count": parsed["row_count"], "sheet": parsed["sheet"],
                         "clients": clients_out})


# ═══════════════════════════════════════════
# UPLOAD — Step 2: confirm
# ═══════════════════════════════════════════

@app.post("/upload/confirm")
async def upload_confirm(request: Request, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    body        = await request.json()
    filename    = body["filename"]
    row_count   = body["row_count"]
    label       = body.get("label") or filename
    assignments = body["assignments"]

    period = Period(label=label, filename=filename, row_count=row_count, uploaded_by=current_user.id)
    db.add(period)
    db.flush()

    norm_index = {c.name_normalized: c for c in db.query(Client).all()}

    for a in assignments:
        norm        = a["name_normalized"]
        designer_id = a.get("designer_id")
        if designer_id is not None:
            try:
                designer_id = int(designer_id)
            except (ValueError, TypeError):
                designer_id = None

        client = norm_index.get(norm)
        if not client:
            client = Client(name=a["name"], name_normalized=norm, designer_id=designer_id)
            db.add(client)
            db.flush()
            norm_index[norm] = client
        else:
            if designer_id and client.designer_id != designer_id:
                client.designer_id = designer_id

        project = Project(
            period_id=period.id, client_id=client.id,
            revenue=float(a.get("revenue") or 0),
            profit=float(a.get("profit") or 0),
            time_billing=float(a.get("time_billing") or 0),
            margin=float(a.get("margin") or 0),
            monthly_data=a.get("monthly_data") or {},
            date_min=a.get("date_min"),
            date_max=a.get("date_max"),
        )
        db.add(project)

    db.commit()
    return JSONResponse({"success": True, "period_id": period.id})


# ═══════════════════════════════════════════
# DESIGNERS
# ═══════════════════════════════════════════

@app.get("/designers", response_class=HTMLResponse)
def designers_page(request: Request, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    designers = db.query(Designer).order_by(Designer.name).all()
    return templates.TemplateResponse("designers.html", {"request": request, "user": current_user, "designers": designers})

@app.post("/designers/add")
def add_designer(name: str = Form(...), label: str = Form(...), color_hex: str = Form(...),
                 current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(Designer).filter(Designer.name == name.strip()).first()
    if existing:
        return JSONResponse({"id": existing.id, "existed": True})
    designer = Designer(name=name.strip(), label=label.strip(), color_hex=color_hex)
    db.add(designer)
    db.commit()
    db.refresh(designer)
    return JSONResponse({"id": designer.id, "existed": False})

@app.post("/designers/{designer_id}/toggle")
def toggle_designer(designer_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    d = db.query(Designer).filter(Designer.id == designer_id).first()
    if not d:
        raise HTTPException(status_code=404)
    d.active = not d.active
    db.commit()
    return RedirectResponse(url="/designers", status_code=302)

@app.post("/designers/{designer_id}/edit")
def edit_designer(designer_id: int, label: str = Form(...), color_hex: str = Form(...),
                  current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    d = db.query(Designer).filter(Designer.id == designer_id).first()
    if not d:
        raise HTTPException(status_code=404)
    d.label     = label.strip()
    d.color_hex = color_hex
    db.commit()
    return RedirectResponse(url="/designers", status_code=302)


# ═══════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.email).all()
    return templates.TemplateResponse("users.html", {"request": request, "user": current_user, "users": users})

@app.post("/users/add")
def add_user(email: str = Form(...), password: str = Form(...), role: str = Form(...),
             current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.email == email.lower().strip()).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    db.add(User(email=email.lower().strip(), password_hash=hash_password(password), role=role))
    db.commit()
    return RedirectResponse(url="/users", status_code=302)

@app.post("/users/{user_id}/delete")
def delete_user(user_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/users", status_code=302)
