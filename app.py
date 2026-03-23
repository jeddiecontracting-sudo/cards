"""
Card Radar — Full Site
Install: pip install flask flask-login flask-sqlalchemy google-generativeai requests pillow stripe
Run: python app.py
Set env vars in Replit Secrets:
  GEMINI_API_KEY, STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_WEBHOOK_SECRET,
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SECRET_KEY, PAYPAL_PLAN_URL
"""

import os, json, base64, datetime, secrets
import requests
import stripe
from google import genai as genai_client
from google.genai import types as genai_types
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth

GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_API_KEY_2 = os.environ.get("GEMINI_API_KEY_2") or os.environ.get("GOOGLE_API_KEY_2", "")
STRIPE_SECRET    = os.environ.get("STRIPE_SECRET_KEY", "YOUR_STRIPE_SECRET_HERE")
STRIPE_PRICE_ID  = os.environ.get("STRIPE_PRICE_ID", "YOUR_STRIPE_PRICE_ID")
STRIPE_WEBHOOK   = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SECRET_KEY       = os.environ.get("SECRET_KEY", secrets.token_hex(32))
COMMISSION_RATE  = 0.02

gemini                = genai_client.Client(api_key=GEMINI_API_KEY)   if GEMINI_API_KEY   else None
gemini_listing_client = genai_client.Client(api_key=GEMINI_API_KEY_2) if GEMINI_API_KEY_2 else None
stripe.api_key = STRIPE_SECRET

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///cardradar.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login_page"

oauth = OAuth(app)
google_oauth = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── Models ────────────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    name = db.Column(db.String(120), default="")
    is_premium = db.Column(db.Boolean, default=False)
    stripe_customer_id = db.Column(db.String(128))
    stripe_subscription_id = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    scans = db.relationship("Scan", backref="user", lazy=True)
    listings = db.relationship("MarketplaceListing", backref="user", lazy=True)

class Scan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    mode = db.Column(db.String(20))
    card_name = db.Column(db.String(200))
    set_name = db.Column(db.String(200))
    card_number = db.Column(db.String(50))
    condition = db.Column(db.String(50))
    grade_score = db.Column(db.String(10))
    price = db.Column(db.String(50))
    listing_title = db.Column(db.String(300))
    listing_desc = db.Column(db.Text)

class MarketplaceListing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    card_name = db.Column(db.String(200))
    set_name = db.Column(db.String(200))
    condition = db.Column(db.String(50))
    price = db.Column(db.Float)
    description = db.Column(db.Text)
    image_b64 = db.Column(db.Text)
    sold = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

@login_manager.user_loader
def load_user(uid): return User.query.get(int(uid))

with app.app_context(): db.create_all()

# ── Helpers ───────────────────────────────────────────────────────────────────
def fetch_price(card_name):
    try:
        url = f"https://api.pokemontcg.io/v2/cards?q=name:{requests.utils.quote(card_name)}&pageSize=3"
        data = requests.get(url, timeout=8).json()
        if data.get("data"):
            prices = data["data"][0].get("tcgplayer", {}).get("prices", {})
            for tier in ["holofoil","normal","reverseHolofoil"]:
                if tier in prices and prices[tier].get("market"):
                    return f"${prices[tier]['market']:.2f}"
    except: pass
    return "Unavailable"

def _gemini_call(prompt, b64, mime, client=None):
    active = client or gemini
    if not active:
        raise RuntimeError("Gemini API key is not configured. Add GEMINI_API_KEY or GOOGLE_API_KEY to your secrets.")
    image_part = genai_types.Part.from_bytes(data=base64.b64decode(b64), mime_type=mime)
    response = active.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt, image_part]
    )
    raw = response.text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def gemini_grade(b64, mime):
    prompt = 'Analyze this Pokémon card. Return ONLY valid JSON (no markdown): {"card_name":"","set_name":"","card_number":"","year":"","condition":"Mint/Near Mint/Lightly Played/Moderately Played/Heavily Played/Damaged","grade_score":"1-10","centering":"","corners":"","edges":"","surface":"","holo_damage":"","notes":""}'
    return _gemini_call(prompt, b64, mime, client=gemini)

def gemini_listing(b64, mime):
    if not gemini_listing_client:
        raise RuntimeError("Listing generator requires GOOGLE_API_KEY_2 to be set in Secrets.")
    prompt = 'Analyze this Pokémon card for eBay selling. Return ONLY valid JSON (no markdown): {"card_name":"","set_name":"","card_number":"","year":"","rarity":"","condition":"","ebay_title":"max 80 chars","description":"3-4 paragraphs professional","keywords":[],"suggested_price":"$X.XX","category":""}'
    return _gemini_call(prompt, b64, mime, client=gemini_listing_client)

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/auth/signup", methods=["POST"])
def do_signup():
    d = request.get_json()
    email = d.get("email","").lower().strip()
    if not email or not d.get("password"):
        return jsonify({"error":"Email and password required"}),400
    if User.query.filter_by(email=email).first():
        return jsonify({"error":"Email already registered"}),400
    user = User(email=email, password_hash=generate_password_hash(d["password"]), name=d.get("name",""))
    db.session.add(user); db.session.commit()
    login_user(user)
    return jsonify({"success":True,"redirect":"/app"})

@app.route("/auth/login", methods=["POST"])
def do_login():
    d = request.get_json()
    user = User.query.filter_by(email=d.get("email","").lower().strip()).first()
    if not user or not check_password_hash(user.password_hash or "", d.get("password","")):
        return jsonify({"error":"Invalid email or password"}),401
    login_user(user)
    return jsonify({"success":True,"redirect":"/app"})

@app.route("/auth/logout")
def do_logout():
    logout_user(); return redirect("/")

@app.route("/auth/google")
def auth_google():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return google_oauth.authorize_redirect(redirect_uri)

@app.route("/auth/google/callback")
def auth_google_callback():
    try:
        token = google_oauth.authorize_access_token()
        userinfo = token.get("userinfo") or google_oauth.userinfo()
        email = userinfo.get("email", "").lower().strip()
        name = userinfo.get("name", "")
        if not email:
            return redirect("/login?error=google_no_email")
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, name=name, password_hash=None)
            db.session.add(user)
            db.session.commit()
        login_user(user)
        return redirect("/app")
    except Exception as e:
        return redirect(f"/login?error=google_failed")

# ── Stripe ────────────────────────────────────────────────────────────────────
@app.route("/subscribe/stripe", methods=["POST"])
@login_required
def subscribe_stripe():
    try:
        if not current_user.stripe_customer_id:
            c = stripe.Customer.create(email=current_user.email)
            current_user.stripe_customer_id = c.id; db.session.commit()
        s = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity":1}],
            mode="subscription",
            success_url=request.host_url+"subscribe/success",
            cancel_url=request.host_url+"pricing"
        )
        return jsonify({"url": s.url})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/subscribe/paypal", methods=["POST"])
@login_required
def subscribe_paypal():
    url = os.environ.get("PAYPAL_PLAN_URL","https://www.paypal.com")
    return jsonify({"url": url})

@app.route("/subscribe/success")
@login_required
def subscribe_success():
    current_user.is_premium = True; db.session.commit()
    return redirect("/app?upgraded=1")

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    try:
        event = stripe.Webhook.construct_event(request.data, request.headers.get("Stripe-Signature",""), STRIPE_WEBHOOK)
        cid = event["data"]["object"].get("customer")
        user = User.query.filter_by(stripe_customer_id=cid).first() if cid else None
        if user:
            if event["type"] in ["invoice.payment_succeeded"]: user.is_premium = True
            elif event["type"] in ["customer.subscription.deleted"]: user.is_premium = False
            db.session.commit()
    except Exception as e: return str(e),400
    return "",200

# ── Scan API ──────────────────────────────────────────────────────────────────
@app.route("/api/grade", methods=["POST"])
@login_required
def api_grade():
    try:
        results = []
        for f in request.files.getlist("images"):
            b64 = base64.b64encode(f.read()).decode()
            mime = f.mimetype or "image/jpeg"
            data = gemini_grade(b64, mime)
            data["price"] = fetch_price(data.get("card_name",""))
            data["image_b64"] = b64
            db.session.add(Scan(user_id=current_user.id, mode="grade",
                card_name=data.get("card_name",""), set_name=data.get("set_name",""),
                card_number=data.get("card_number",""), condition=data.get("condition",""),
                grade_score=data.get("grade_score",""), price=data.get("price","")))
            results.append(data)
        db.session.commit()
        return jsonify({"success":True,"results":results})
    except Exception as e: return jsonify({"success":False,"error":str(e)})

@app.route("/api/listing", methods=["POST"])
@login_required
def api_listing():
    try:
        results = []
        for f in request.files.getlist("images"):
            b64 = base64.b64encode(f.read()).decode()
            mime = f.mimetype or "image/jpeg"
            data = gemini_listing(b64, mime)
            data["tcg_price"] = fetch_price(data.get("card_name",""))
            data["image_b64"] = b64
            db.session.add(Scan(user_id=current_user.id, mode="listing",
                card_name=data.get("card_name",""), set_name=data.get("set_name",""),
                listing_title=data.get("ebay_title",""), listing_desc=data.get("description",""),
                condition=data.get("condition",""), price=data.get("tcg_price","")))
            results.append(data)
        db.session.commit()
        return jsonify({"success":True,"results":results})
    except Exception as e: return jsonify({"success":False,"error":str(e)})

@app.route("/api/marketplace/list", methods=["POST"])
@login_required
def api_marketplace_list():
    if not current_user.is_premium:
        return jsonify({"error":"premium_required"}),403
    try:
        d = request.get_json()
        l = MarketplaceListing(user_id=current_user.id, card_name=d.get("card_name",""),
            set_name=d.get("set_name",""), condition=d.get("condition",""),
            price=float(d.get("price",0)), description=d.get("description",""), image_b64=d.get("image_b64",""))
        db.session.add(l); db.session.commit()
        return jsonify({"success":True,"listing_id":l.id})
    except Exception as e: return jsonify({"success":False,"error":str(e)})

@app.route("/api/history")
@login_required
def api_history():
    scans = Scan.query.filter_by(user_id=current_user.id).order_by(Scan.timestamp.desc()).limit(50).all()
    return jsonify([{"id":s.id,"timestamp":s.timestamp.isoformat(),"mode":s.mode,
        "card_name":s.card_name,"set_name":s.set_name,"condition":s.condition,
        "grade_score":s.grade_score,"price":s.price} for s in scans])

@app.route("/api/marketplace/listings")
def api_marketplace_listings():
    ls = MarketplaceListing.query.filter_by(sold=False).order_by(MarketplaceListing.created_at.desc()).limit(40).all()
    return jsonify([{"id":l.id,"card_name":l.card_name,"set_name":l.set_name,
        "condition":l.condition,"price":l.price,"seller":l.user.name or l.user.email.split("@")[0]} for l in ls])

@app.route("/api/user")
@login_required
def api_user():
    return jsonify({"email":current_user.email,"name":current_user.name,"is_premium":current_user.is_premium})

# ── Price Check API ───────────────────────────────────────────────────────────
@app.route("/api/price-check")
def api_price_check():
    name   = (request.args.get("name", "") or request.args.get("q", "")).strip()
    set_q  = request.args.get("set", "").strip()
    num_q  = request.args.get("number", "").strip()

    if not name and not num_q:
        return jsonify({"error": "Please enter a card name to search."}), 400

    try:
        parts = []
        if name:
            safe = name.replace('"', '')
            parts.append(f'name:"{safe}"')
        if num_q:
            # Support "08/99" style (take the part before "/") and codes like SWSH152
            raw_num = num_q.split("/")[0].strip().lstrip("0") or num_q.split("/")[0].strip()
            parts.append(f'number:{requests.utils.quote(raw_num)}')
        if set_q:
            safe_set = set_q.replace('"', '')
            parts.append(f'set.name:"{safe_set}"')

        query = " ".join(parts)
        url = (
            f"https://api.pokemontcg.io/v2/cards"
            f"?q={requests.utils.quote(query)}"
            f"&pageSize=250&orderBy=-set.releaseDate"
        )
        data = requests.get(url, timeout=15).json()
        all_tiers = [
            "holofoil", "normal", "reverseHolofoil",
            "1stEditionHolofoil", "1stEditionNormal",
            "unlimitedHolofoil", "unlimited"
        ]
        tier_labels = {
            "holofoil": "Holofoil",
            "normal": "Normal",
            "reverseHolofoil": "Reverse Holo",
            "1stEditionHolofoil": "1st Ed. Holo",
            "1stEditionNormal": "1st Ed. Normal",
            "unlimitedHolofoil": "Unlimited Holo",
            "unlimited": "Unlimited",
        }
        cards = []
        for c in data.get("data", []):
            tcg = c.get("tcgplayer", {}).get("prices", {})
            prices = {}
            for tier in all_tiers:
                if tier in tcg and tcg[tier].get("market"):
                    prices[tier_labels[tier]] = f"{tcg[tier]['market']:.2f}"
            images = c.get("images", {})
            set_info = c.get("set", {})
            cards.append({
                "name":   c.get("name", ""),
                "set":    set_info.get("name", ""),
                "series": set_info.get("series", ""),
                "number": c.get("number", ""),
                "rarity": c.get("rarity", ""),
                "prices": prices,
                "image":  images.get("small", ""),
                "tcgplayer_url": c.get("tcgplayer", {}).get("url", ""),
            })
        return jsonify({"cards": cards, "total": len(cards)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Pages ─────────────────────────────────────────────────────────────────────
@app.route("/")
def landing(): return render_template("landing.html")
@app.route("/pricing")
def pricing(): return render_template("pricing.html")
@app.route("/ai-grading")
def ai_grading(): return render_template("ai_grading.html")
@app.route("/listing-generator")
def listing_generator(): return render_template("listing_generator.html")
@app.route("/price-check")
def price_check(): return render_template("price_check.html")
@app.route("/login")
def login_page(): return render_template("auth.html", mode="login")
@app.route("/signup")
def signup_page(): return render_template("auth.html", mode="signup")
@app.route("/app")
@login_required
def app_page(): return render_template("app.html")
@app.route("/marketplace")
def marketplace(): return render_template("marketplace.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
