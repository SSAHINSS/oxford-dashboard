"""
Run once to seed the database with initial designers and an admin user.
Usage: python seed.py --email admin@example.com --password yourpassword
"""
import argparse
import sys
from database import SessionLocal, create_tables, Designer, Client, User
from auth import hash_password
from excel import norm_name

INITIAL_DESIGNERS = [
    {"name": "Kasey/Asije",           "label": "Kasey / Asije",         "color_hex": "#444735"},
    {"name": "Jacki",                  "label": "Jacki",                  "color_hex": "#353738"},
    {"name": "Lori/Katie",             "label": "Lori / Katie",           "color_hex": "#b89653"},
    {"name": "Danielle/Joelle/Wes",   "label": "Danielle / Joelle / Wes","color_hex": "#6b6f57"},
    {"name": "Joelle",                 "label": "Joelle",                 "color_hex": "#8a7444"},
]

# Historical client → designer assignments
INITIAL_CLIENTS = {
    "ACCARDI": "Kasey/Asije",
    "AGW CAPITAL": "Jacki",
    "ALONSO": "Danielle/Joelle/Wes",
    "BRISTOL RESIDENCE": "Lori/Katie",
    "BURBY": "Kasey/Asije",
    "BURBY CONSTRUCTION": "Kasey/Asije",
    "BURNS": "Danielle/Joelle/Wes",
    "CASPER- BAYSHORE COURT": "Joelle",
    "CASPER-BAYSHORE COURT": "Joelle",
    "CLAY HILL": "Jacki",
    "DEX IMAGING": "Jacki",
    "DOYLE": "Kasey/Asije",
    "EDESIGN": "Joelle",
    "GARCIA": "Danielle/Joelle/Wes",
    "GLAZER": "Danielle/Joelle/Wes",
    "GLOEDE": "Lori/Katie",
    "GRECO": "Lori/Katie",
    "HARROD": "Kasey/Asije",
    "JETTON": "Lori/Katie",
    "KENNEDY GARCIA": "Danielle/Joelle/Wes",
    "KIMBRO - RENTAL": "Lori/Katie",
    "MALIZIA": "Lori/Katie",
    "MARNIE BAUER": "Danielle/Joelle/Wes",
    "MEAGAN BRANDRIFF": "Joelle",
    "NEWMAN": "Kasey/Asije",
    "O'CONNOR": "Danielle/Joelle/Wes",
    "PEI GLOBAL": "Jacki",
    "PEI GLOBAL NYC": "Jacki",
    "PHILLIPS": "Lori/Katie",
    "PREDALINA": "Jacki",
    "PRICE-TALLY": "Kasey/Asije",
    "PRICE- TALLY": "Kasey/Asije",
    "RICCI": "Kasey/Asije",
    "RICH": "Lori/Katie",
    "ROX": "Jacki",
    "SORIANO": "Lori/Katie",
    "WATT": "Lori/Katie",
    "WRIGHTS": "Jacki",
    "YODZIS": "Danielle/Joelle/Wes",
    "YODZIS C": "Kasey/Asije",
    "YODZIS, C": "Kasey/Asije",
}


def seed(email: str, password: str):
    create_tables()
    db = SessionLocal()

    # ── Designers ──
    designer_map = {}
    for d in INITIAL_DESIGNERS:
        existing = db.query(Designer).filter(Designer.name == d["name"]).first()
        if not existing:
            designer = Designer(**d)
            db.add(designer)
            db.flush()
            designer_map[d["name"]] = designer
            print(f"  + Designer: {d['name']}")
        else:
            designer_map[d["name"]] = existing
            print(f"  = Designer exists: {d['name']}")

    # ── Clients ──
    for client_name, designer_name in INITIAL_CLIENTS.items():
        normalized = norm_name(client_name)
        existing = db.query(Client).filter(Client.name_normalized == normalized).first()
        designer = designer_map.get(designer_name)
        if not existing:
            client = Client(
                name=client_name,
                name_normalized=normalized,
                designer_id=designer.id if designer else None
            )
            db.add(client)
            print(f"  + Client: {client_name} → {designer_name}")
        else:
            if designer and existing.designer_id != designer.id:
                existing.designer_id = designer.id
                print(f"  ~ Updated client: {client_name} → {designer_name}")

    # ── Admin user ──
    existing_user = db.query(User).filter(User.email == email).first()
    if not existing_user:
        user = User(email=email, password_hash=hash_password(password), role="admin")
        db.add(user)
        print(f"  + Admin user: {email}")
    else:
        print(f"  = User exists: {email}")

    db.commit()
    db.close()
    print("\nSeed complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    seed(args.email, args.password)
