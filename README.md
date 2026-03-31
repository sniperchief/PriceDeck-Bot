# MARKIT

A community-powered, real-time commodity price intelligence tool for everyday buyers, traders, and households across Nigerian markets, starting with Enugu State.

## Setup

1. Create virtual environment:
```bash
python -m venv venv
```

2. Activate virtual environment:
```bash
# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables in `.env`

5. Run the application:
```bash
uvicorn app.main:app --reload --port 8000
```

## Project Structure

```
makit/
├── app/
│   ├── main.py              # FastAPI app and webhook handler
│   ├── config.py            # Configuration and environment variables
│   ├── database.py          # Database helper functions
│   ├── claude_tasks.py      # Claude API integration for 5 tasks
│   ├── message_router.py    # Message routing logic
│   ├── whatsapp.py          # WhatsApp message sending functions
│   └── utils.py             # Utility functions
├── .env                     # Environment variables (not in git)
├── .gitignore
├── requirements.txt
└── README.md
```

## Environment Variables

See `.env` file for required configuration.
