# Quick Start Guide (5 minutes)

## TL;DR Setup

### Prerequisites
- Python 3.8+ installed
- Anthropic API key (from https://console.anthropic.com/)

### Run It (3 steps)

**Terminal 1 - Backend:**
```bash
cd trip-guide-app
python -m venv venv
source venv/bin/activate  # or: venv\Scripts\activate (Windows)
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your API key
python app.py
```

**Terminal 2 - Frontend:**
```bash
cd trip-guide-app
python -m http.server 5000
```

**Browser:**
```
http://localhost:5000
```

---

## File Structure You Should Have

```
trip-guide-app/
├── app.py                 ← Flask backend
├── index.html             ← Web interface
├── requirements.txt       ← Python packages
├── .env                   ← Your API key (create from .env.example)
├── .env.example           ← Template
├── .gitignore
├── README.md             ← Full documentation
└── QUICKSTART.md         ← This file
```

---

## What Should Happen

1. Browser loads form at `http://localhost:5000`
2. Fill in location, duration, preferences
3. Click "Generate My Trip Guide"
4. Wait 1-2 minutes (it's calling Anthropic API 3 times)
5. See HTML preview + Download PDF button
6. Done!

---

## API Key Setup

1. Go to https://console.anthropic.com/
2. Click "API Keys"
3. Create new key (copy it)
4. Open `trip-guide-app/.env`
5. Replace `your_api_key_here` with actual key
6. Save file

---

## Common Issues

| Issue | Fix |
|-------|-----|
| Connection refused :5001 | Is `python app.py` running in Terminal 1? |
| CORS error | Make sure Flask is on port 5001, frontend on 5000 |
| "Cannot GET /" | Did you run `python -m http.server 5000` in Terminal 2? |
| API key error | Check `.env` has real key (starts with `sk-ant-`) |
| Slow generation | First request is slow (1-2 min). Normal. ✅ |

---

## Commands Reference

### Create/Activate Virtual Environment
```bash
python -m venv venv
source venv/bin/activate     # Mac/Linux
venv\Scripts\activate        # Windows
```

### Install Packages
```bash
pip install -r requirements.txt
```

### Run Backend
```bash
python app.py
# Runs on http://localhost:5001
```

### Run Frontend
```bash
python -m http.server 5000
# Runs on http://localhost:5000
```

### Deactivate Virtual Environment
```bash
deactivate
```

---

## Testing the API Directly (Optional)

You can test the backend without the frontend using curl:

```bash
curl -X POST http://localhost:5001/generate \
  -H "Content-Type: application/json" \
  -d '{
    "location": "Barcelona, Spain",
    "duration": "3",
    "photo_interests": "Architecture",
    "cuisines": "Traditional Local Cuisine",
    "attractions": "Historical & Cultural Sites",
    "budget": "Moderate",
    "distance": "Up to 15 minutes"
  }'
```

Should return JSON with `status: "success"` and HTML/PDF content.

---

## Next Steps After Testing

- Want to deploy? See README.md for cloud hosting options
- Want to add features? The code is documented and extensible
- Want to monetize? Add payment integration to the form

---

## File Locations

All files are in: `/sessions/sleepy-bold-gates/trip-guide-app/`

You can copy this entire folder anywhere on your computer and run it locally!

---

**You're all set! Run the 3 commands above and visit http://localhost:5000** 🚀
