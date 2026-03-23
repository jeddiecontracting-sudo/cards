# Card Radar

AI-powered Pokémon card scanning, grading, and selling platform.

## Stack
- **Backend**: Flask + SQLAlchemy + Flask-Login
- **Database**: SQLite (`instance/cardradar.db`)
- **AI**: Google Gemini 2.0 Flash (via `google-generativeai`)
- **Payments**: Stripe + PayPal
- **Prices**: Pokémon TCG API + TCGPlayer

## Project Layout
- `app.py` — Main Flask application (routes, models, helpers)
- `templates/` — Jinja2 HTML templates (landing, app, auth, features, etc.)
- `requirements.txt` — Python dependencies
- `cardradar.db` — SQLite database

## Running
- Development: `python app.py` (serves on 0.0.0.0:5000)
- Production: `gunicorn --bind=0.0.0.0:5000 --reuse-port app:app`

## Pages
- `/` — Landing page
- `/pricing` — Free vs Premium pricing
- `/ai-grading` — AI Grading feature page (Premium)
- `/listing-generator` — eBay Listing Generator feature page (Free)
- `/price-check` — Searchable Pokémon card price directory
- `/marketplace` — Card Radar marketplace
- `/app` — Main dashboard (login required)
- `/login`, `/signup` — Auth

## API Routes
- `POST /api/grade` — AI card grading (login required)
- `POST /api/listing` — eBay listing generation (login required)
- `GET /api/price-check?q=<name>` — Search card prices (public)
- `GET /api/marketplace/listings` — Public marketplace listings
- `POST /api/marketplace/list` — List a card (premium required)
- `GET /api/history` — User scan history
- `POST /subscribe/stripe` — Start Stripe subscription
- `POST /subscribe/paypal` — Start PayPal subscription

## Design
- Dark background: `#080810`
- Accent (lime green): `#7fff00`
- Fonts: Syne (headings), Barlow Condensed (hero h1), Inter (body)
- Free tier badge: teal/success (`#47ffb2`)
- Premium badge: lime accent

## Feature Access
- **Free**: Limited scanning, eBay listing generator, TCGPlayer prices, price check, browse marketplace
- **Premium**: Unlimited scanning, AI condition grading (PSA estimate), sell on Card Radar marketplace

## Env Secrets Required
- `GEMINI_API_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_PRICE_ID`
- `STRIPE_WEBHOOK_SECRET`
- `SECRET_KEY`
- `PAYPAL_PLAN_URL`
