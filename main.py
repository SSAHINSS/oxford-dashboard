import os
import json
from datetime import datetime
from typing import Optional

from fastapi import (
    FastAPI, Request, Depends, HTTPException,
    Form, UploadFile, File, Response
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db, create_tables, User, Designer, Client, Period, Project
from auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin, get_optional_user,
    COOKIE_NAME, _LoginRedirect, _ForbiddenRedirect
)
from excel import parse_studio_export, norm_name


app = FastAPI(title="Oxford Design Studio — Profitability Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    create_tables()


# ═══════════════════════════════════════════
# ONE-TIME SETUP — creates admin + seeds data
# Protected by SETUP_KEY env variable
# Visit: /setup?key=YOUR_SETUP_KEY
# ═══════════════════════════════════════════

@app.get("/setup", response_class=HTMLResponse)
def setup(
    key:        str = "",
    email:      str = "",
    password:   str = "",
    db: Session = Depends(get_db)
):
    setup_key = os.environ.get("SETUP_KEY", "")
    if not setup_key or key != setup_key:
        return HTMLResponse("<h2>Invalid or missing setup key.</h2>", status_code=403)

    # If just visiting the page, show the form
    if not email or not password:
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;padding:20px;">
        <h2>Oxford Dashboard — First Time Setup</h2>
        <p>Create your admin account:</p>
        <form method="get">
          <input type="hidden" name="key" value="{key}">
          <p><label>Email<br><input name="email" type="email" style="width:100%;padding:8px;" required></label></p>
          <p><label>Password<br><input name="password" type="password" style="width:100%;padding:8px;" required></label></p>
          <button type="submit" style="padding:10px 20px;background:#1c1a16;color:white;border:none;cursor:pointer;">
            Create Admin &amp; Seed Data
          </button>
        </form>
        </body></html>
        """.replace("{key}", key))

    # Run seed
    from seed import seed
    try:
        seed(email, password)
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;padding:20px;">
        <h2>✓ Setup complete</h2>
        <p>Admin account created for <strong>{email}</strong></p>
        <p>All designers and historical client assignments have been loaded.</p>
        <p><a href="/login" style="background:#1c1a16;color:white;padding:10px 20px;text-decoration:none;">
          Go to Login →
        </a></p>
        <p style="margin-top:30px;color:#999;font-size:12px;">
          You can now remove the SETUP_KEY environment variable from Railway to disable this page.
        </p>
        </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"<h2>Error during setup:</h2><pre>{e}</pre>", status_code=500)


# ── Handle auth redirects cleanly ───────────────────────────────────
@app.exception_handler(_LoginRedirect)
async def login_redirect_handler(request: Request, exc: _LoginRedirect):
    return RedirectResponse(url="/login", status_code=302)

@app.exception_handler(_ForbiddenRedirect)
async def forbidden_handler(request: Request, exc: _ForbiddenRedirect):
    return RedirectResponse(url="/", status_code=302)


# ═══════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_optional_user(request, db)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(
    request: Request,
    email:    str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error":   "Invalid email or password."
        })
    token    = create_token(user.id, user.role)
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, max_age=60*60*24*7, samesite="lax"
    )
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
    request:     Request,
    period_id:   Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    periods = (
        db.query(Period)
        .filter(Period.is_duplicate == False)
        .order_by(desc(Period.uploaded_at))
        .all()
    )

    selected = None
    if period_id:
        selected = db.query(Period).filter(Period.id == period_id).first()
    elif periods:
        selected = periods[0]

    projects_data    = []
    designer_summary = []

    if selected:
        projects = (
            db.query(Project)
            .filter(Project.period_id == selected.id)
            .join(Client)
            .join(Designer, Client.designer_id == Designer.id)
            .all()
        )
        for p in projects:
            des = p.client.designer
            projects_data.append({
                "client":         p.client.name,
                "designer":       des.name  if des else "Unassigned",
                "designer_label": des.label if des else "Unassigned",
                "color_hex":      des.color_hex if des else "#888888",
                "revenue":        round(p.revenue, 2),
                "profit":         round(p.profit,  2),
                "time_billing":   round(p.time_billing, 2),
                "margin":         round(p.margin,  1),
            })

        # Aggregate by designer
        des_map: dict = {}
        for p in projects_data:
            d = p["designer"]
            if d not in des_map:
                des_map[d] = {
                    "name":      d,
                    "label":     p["designer_label"],
                    "color_hex": p["color_hex"],
                    "revenue": 0.0, "profit": 0.0,
                    "time_billing": 0.0, "count": 0
                }
            des_map[d]["revenue"]      += p["revenue"]
            des_map[d]["profit"]       += p["profit"]
            des_map[d]["time_billing"] += p["time_billing"]
            des_map[d]["count"]        += 1

        for d in des_map.values():
            d["margin"] = round(d["profit"] / d["revenue"] * 100, 1) if d["revenue"] else 0.0

        designer_summary = sorted(des_map.values(), key=lambda x: x["revenue"], reverse=True)

    designers = (
        db.query(Designer)
        .filter(Designer.active == True)
        .order_by(Designer.name)
        .all()
    )

    return templates.TemplateResponse("dashboard.html", {
        "request":          request,
        "user":             current_user,
        "periods":          periods,
        "selected_period":  selected,
        "projects":         json.dumps(projects_data),
        "designer_summary": json.dumps(designer_summary),
        "designers":        designers,
    })


# ═══════════════════════════════════════════
# UPLOAD — Step 1: parse file
# ═══════════════════════════════════════════

@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request:      Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    designers = (
        db.query(Designer)
        .filter(Designer.active == True)
        .order_by(Designer.name)
        .all()
    )
    return templates.TemplateResponse("upload.html", {
        "request":   request,
        "user":      current_user,
        "designers": designers,
    })


@app.post("/upload/parse")
async def upload_parse(
    file:         UploadFile = File(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    contents = await file.read()

    try:
        parsed = parse_studio_export(contents, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Duplicate check
    existing = db.query(Period).filter(
        Period.filename  == file.filename,
        Period.row_count == parsed["row_count"]
    ).first()
    if existing:
        return JSONResponse({
            "duplicate": True,
            "duplicate_period": {
                "id":          existing.id,
                "label":       existing.label,
                "uploaded_at": existing.uploaded_at.strftime("%b %d, %Y")
            }
        })

    # Build normalized lookup from DB clients
    db_clients = db.query(Client).all()
    norm_index = {c.name_normalized: c for c in db_clients}

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
            "designer_id":     designer.id    if designer else None,
            "designer_name":   designer.name  if designer else None,
            "designer_label":  designer.label if designer else None,
            "is_new":          db_client is None,
        })

    return JSONResponse({
        "duplicate": False,
        "filename":  file.filename,
        "row_count": parsed["row_count"],
        "sheet":     parsed["sheet"],
        "clients":   clients_out,
    })


# ═══════════════════════════════════════════
# UPLOAD — Step 2: confirm and save
# ═══════════════════════════════════════════

@app.post("/upload/confirm")
async def upload_confirm(
    request:      Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    body        = await request.json()
    filename    = body["filename"]
    row_count   = body["row_count"]
    label       = body.get("label") or filename
    assignments = body["assignments"]

    # Create period
    period = Period(
        label=label,
        filename=filename,
        row_count=row_count,
        uploaded_by=current_user.id
    )
    db.add(period)
    db.flush()

    norm_index = {c.name_normalized: c for c in db.query(Client).all()}

    for a in assignments:
        norm        = a["name_normalized"]
        designer_id = a.get("designer_id")

        # Coerce to int (JSON can send it as string)
        if designer_id is not None:
            try:
                designer_id = int(designer_id)
            except (ValueError, TypeError):
                designer_id = None

        # Upsert client
        client = norm_index.get(norm)
        if not client:
            client = Client(
                name=a["name"],
                name_normalized=norm,
                designer_id=designer_id
            )
            db.add(client)
            db.flush()
            norm_index[norm] = client
        else:
            # Update assignment if it changed
            if designer_id and client.designer_id != designer_id:
                client.designer_id = designer_id

        # Only create project row if client has a designer
        if client.designer_id:
            project = Project(
                period_id=period.id,
                client_id=client.id,
                revenue=float(a.get("revenue") or 0),
                profit=float(a.get("profit") or 0),
                time_billing=float(a.get("time_billing") or 0),
                margin=float(a.get("margin") or 0),
            )
            db.add(project)

    db.commit()
    return JSONResponse({"success": True, "period_id": period.id})


# ═══════════════════════════════════════════
# DESIGNERS
# ═══════════════════════════════════════════

@app.get("/designers", response_class=HTMLResponse)
def designers_page(
    request:      Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    designers = db.query(Designer).order_by(Designer.name).all()
    return templates.TemplateResponse("designers.html", {
        "request":   request,
        "user":      current_user,
        "designers": designers,
    })


@app.post("/designers/add")
def add_designer(
    name:         str = Form(...),
    label:        str = Form(...),
    color_hex:    str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    existing = db.query(Designer).filter(Designer.name == name.strip()).first()
    if existing:
        # Return existing id so upload page can use it immediately
        return JSONResponse({"id": existing.id, "existed": True})
    designer = Designer(name=name.strip(), label=label.strip(), color_hex=color_hex)
    db.add(designer)
    db.commit()
    db.refresh(designer)
    return JSONResponse({"id": designer.id, "existed": False})


@app.post("/designers/{designer_id}/toggle")
def toggle_designer(
    designer_id:  int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    designer = db.query(Designer).filter(Designer.id == designer_id).first()
    if not designer:
        raise HTTPException(status_code=404)
    designer.active = not designer.active
    db.commit()
    return RedirectResponse(url="/designers", status_code=302)


@app.post("/designers/{designer_id}/edit")
def edit_designer(
    designer_id:  int,
    label:        str = Form(...),
    color_hex:    str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    designer = db.query(Designer).filter(Designer.id == designer_id).first()
    if not designer:
        raise HTTPException(status_code=404)
    designer.label     = label.strip()
    designer.color_hex = color_hex
    db.commit()
    return RedirectResponse(url="/designers", status_code=302)


# ═══════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════

@app.get("/users", response_class=HTMLResponse)
def users_page(
    request:      Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    users = db.query(User).order_by(User.email).all()
    return templates.TemplateResponse("users.html", {
        "request": request,
        "user":    current_user,
        "users":   users,
    })


@app.post("/users/add")
def add_user(
    email:        str = Form(...),
    password:     str = Form(...),
    role:         str = Form(...),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    existing = db.query(User).filter(User.email == email.lower().strip()).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=email.lower().strip(),
        password_hash=hash_password(password),
        role=role
    )
    db.add(user)
    db.commit()
    return RedirectResponse(url="/users", status_code=302)


@app.post("/users/{user_id}/delete")
def delete_user(
    user_id:      int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/users", status_code=302)
