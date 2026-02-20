# Trip Master Web App - Local Development Setup

A web application that generates personalized travel guides combining photography locations, restaurant recommendations, and must-see attractions for any destination.

## Architecture

```
Frontend (HTML/JS)          Backend (Flask)            Anthropic API
   :5000                      :5001
┌─────────────┐          ┌──────────────┐          ┌─────────────┐
│  User Form  │ ─POST──> │   Flask API  │ ─POST──> │   Claude    │
│   Browser   │          │ (3 scouts)   │          │             │
└─────────────┘          └──────────────┘          └─────────────┘
       ↑                        │
       │                        │
       └────── HTML/PDF ────────┘
```

## Prerequisites

Before you start, make sure you have:

1. **Python 3.8+** installed
   - Check: `python --version`

2. **Anthropic API Key**
   - Get from: https://console.anthropic.com/
   - Create your API key if you don't have one yet

3. **Basic terminal knowledge** to run commands

## Installation Steps

### Step 1: Clone or Navigate to the Project

```bash
# If you have the files, navigate to the trip-guide-app directory
cd /path/to/trip-guide-app
```

### Step 2: Create Python Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate it (Mac/Linux)
source venv/bin/activate

# Activate it (Windows)
venv\Scripts\activate
```

You should see `(venv)` at the start of your terminal line.

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `Flask` - Web framework
- `Flask-CORS` - Cross-origin requests
- `anthropic` - Anthropic API client
- `weasyprint` - PDF generation
- `python-dotenv` - Environment variables

### Step 4: Set Up Environment Variables

```bash
# Copy the example file
cp .env.example .env

# Edit .env and add your API key
# Mac/Linux:
nano .env

# Windows:
notepad .env
```

Find this line and replace with your actual API key:
```
ANTHROPIC_API_KEY=sk-ant-...your-api-key-here...
```

Save and close the file.

### Step 5: Run the Backend

In your terminal (with venv activated):

```bash
python app.py
```

You should see:
```
Trip Master API - Local Development
==================================================
Backend running on: http://localhost:5001
Open frontend: http://localhost:5000
==================================================
 * Running on http://0.0.0.0:5001
```

**Leave this terminal running.**

### Step 6: Open the Frontend

Open a **new terminal** (keep the backend running in the first one):

```bash
# Navigate to the project directory
cd /path/to/trip-guide-app

# Start a simple web server for the frontend
python -m http.server 5000
```

You should see:
```
Serving HTTP on 0.0.0.0 port 5000 (http://0.0.0.0:5000/) ...
```

### Step 7: Open in Your Browser

Open your web browser and go to:
```
http://localhost:5000
```

You should see the Trip Master form!

## Using the App

### Basic Workflow

1. **Enter Location** - e.g., "Barcelona, Spain"
2. **Set Duration** - Number of days (1-14)
3. **Select Interests** - Check at least one in each category:
   - Photography (Architecture, Urban, Golden Hour, Landscapes)
   - Dining (Local, Street Food, Fine Dining, Fusion)
   - Attractions (Historical, Art, Shopping, Nature)
4. **Choose Budget** - Budget-Conscious, Moderate, Upscale, or Flexible
5. **Set Distance** - City Center, 15 min, 30 min, or Flexible
6. **Click "Generate My Trip Guide"** - Wait 1-2 minutes
7. **View Results** - See the generated guide in the iframe
8. **Download PDF** - Get a printable version

### What the App Does

When you submit the form:

1. **Photo Scout** - Generates 3 × duration photography locations with:
   - Subject and composition details
   - Lens recommendations
   - Best lighting times
   - Pro photography tips

2. **Restaurant Scout** - Generates 2-3 × duration restaurants with:
   - Cuisine and price information
   - Signature dishes
   - Hours and reservations info
   - Insider tips

3. **Attraction Scout** - Generates 4 × duration attractions with:
   - Admission prices and hours
   - Visit duration estimates
   - Best times to visit
   - Skip-the-line tips

4. **Master Guide** - Combines all three into:
   - Professional HTML document (viewable in browser)
   - Beautiful PDF (downloadable and shareable)
   - Integrated daily itinerary
   - Practical planning section

## Troubleshooting

### "Connection refused" on localhost:5001

**Problem:** Backend isn't running
**Solution:**
- Check that `python app.py` is running in the first terminal
- Look for error messages about the port
- Try a different port: edit `app.py` line ~395 and change `port=5001` to `port=5002`

### "CORS error" or "Failed to fetch"

**Problem:** Frontend can't reach backend
**Solution:**
- Make sure backend is running (`python app.py`)
- Check backend is on port 5001
- Check frontend URL is `http://localhost:5000` (not `127.0.0.1`)
- Verify both are running on localhost

### "Invalid API key" error

**Problem:** Anthropic API key isn't set correctly
**Solution:**
- Check `.env` file has your real API key (starts with `sk-ant-`)
- Not a typo or extra spaces
- Restart the Flask server after changing `.env`

### "PDF generation failed"

**Problem:** weasyprint can't create PDF
**Solution:**
- Make sure all Python packages installed: `pip install -r requirements.txt`
- On Mac, may need: `brew install weasyprint`
- On Linux (Ubuntu): `sudo apt-get install python3-weasyprint`

### Browser shows "Cannot GET /" on port 5000

**Problem:** Frontend server not running
**Solution:**
- In a new terminal, run: `python -m http.server 5000`
- Make sure you're in the `trip-guide-app` directory

### API calls are very slow

**Problem:** Anthropic API is slow or network issue
**Solution:**
- First call is slowest (1-2 minutes normal)
- Check your internet connection
- API is working if you see "Creating your personalized travel guide..."
- Wait for completion

## Project Files Explained

```
trip-guide-app/
├── app.py                 # Flask backend (main API)
├── index.html             # Frontend (HTML form + JavaScript)
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
├── .env                  # Your actual API key (created from .env.example)
└── README.md             # This file
```

### app.py

The Flask backend that:
- Receives form data from frontend
- Calls Anthropic API 3 times (Photo, Restaurant, Attraction scouts)
- Orchestrates responses
- Generates unified HTML and PDF
- Returns results to frontend

### index.html

The web interface that:
- Collects user preferences
- Sends requests to Flask backend
- Displays HTML preview
- Handles PDF download
- Shows loading states and errors

## API Endpoints

### Health Check
```
GET http://localhost:5001/health
```
Returns: `{"status": "ok"}`

### Generate Trip Guide
```
POST http://localhost:5001/generate
Content-Type: application/json

{
  "location": "Barcelona, Spain",
  "duration": "3",
  "photo_interests": "Architecture & Buildings, Sunrise & Sunset",
  "cuisines": "Traditional Local Cuisine, Street Food & Casual",
  "attractions": "Historical & Cultural Sites, Art Museums",
  "budget": "Moderate",
  "distance": "Up to 15 minutes"
}
```

Returns:
```json
{
  "status": "success",
  "location": "Barcelona, Spain",
  "duration": 3,
  "html": "<html>...</html>",
  "pdf_base64": "JVBERi0xLjQK...",
  "photo_count": 9,
  "restaurant_count": 9,
  "attraction_count": 12
}
```

## Next Steps (After Testing)

Once you confirm everything works locally, you can:

1. **Deploy Backend**
   - Heroku: `git push heroku main`
   - AWS Lambda: Use Zappa wrapper
   - DigitalOcean: Simple VPS deployment

2. **Deploy Frontend**
   - Vercel: Push to GitHub, auto-deploy
   - Netlify: Drag & drop or Git integration
   - AWS S3 + CloudFront: Static hosting

3. **Add Features**
   - User accounts (store saved guides)
   - Email delivery
   - Custom templates
   - Payment integration (charge for guides)
   - Analytics dashboard

## Support & Issues

If you encounter issues:

1. Check the error message carefully
2. Review the Troubleshooting section above
3. Verify:
   - Both terminals are running (frontend & backend)
   - API key is correct in `.env`
   - Port 5000 and 5001 are available
   - Python 3.8+ installed
   - All packages installed: `pip list`

## Performance Notes

- **First API call:** 30-60 seconds (slower due to model loading)
- **Subsequent calls:** 20-40 seconds
- **Total generation:** 1-2 minutes for complete guide
- **PDF generation:** 10-20 seconds
- **File sizes:** HTML ~200KB, PDF ~100KB

## API Costs

Running this locally uses Anthropic API:
- Each guide generation ≈ 150k tokens
- At $3/1M input tokens = ~$0.45 per guide
- Subsequent guides will be faster and cheaper

## License

This is a personal project built locally. Free to use for yourself!

---

**Happy travels! 🌍✈️**
