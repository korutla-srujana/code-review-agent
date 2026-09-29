# AI Code Review Agent with Memory

A beginner-friendly full-stack Flask app that reviews code with Groq, remembers past mistakes, supports multiple languages, and includes login plus a personal dashboard.

## Features

- Local register and login flow using `users.json`
- Session-protected routes for the review page and dashboard
- Multi-language code review for Python, Java, C++, and JavaScript
- Memory system that prefers Hindsight when configured and falls back to `memory.json`
- Predictive warnings only when a real repeated mistake appears again
- Dashboard with total reviews, most common mistakes, last five reviews, and improvement summary

## Files

```text
app.py
requirements.txt
.env.example
users.json
memory.json
templates/
  base.html
  login.html
  register.html
  index.html
  dashboard.html
static/
  app.js
  style.css
```

## Setup

1. Create and activate a virtual environment.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
py -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and update the values.

```env
SECRET_KEY=change_this_to_a_random_secret
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-20b
HINDSIGHT_API_KEY=your_hindsight_api_key_here
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_BANK_ID=ai-code-review-agent-memory
```

4. Start the app.

```powershell
py app.py
```

5. Open the site.

```text
http://127.0.0.1:5000/login
```

## How The Memory Works

- If Hindsight environment variables are present and the request succeeds, the app marks the saved memory source as `hindsight`.
- If Hindsight is unavailable or fails, the app still stores the review locally in `memory.json`.
- The dashboard always reads from `memory.json`, so the beginner-friendly local fallback continues to work.

## Example `users.json`

```json
[]
```

## Example `memory.json`

```json
[
  {
    "username": "demo",
    "language": "Python",
    "code": "def add(a, b):\n    return a + b",
    "mistakes": [],
    "suggestions": [
      "No extra suggestions needed."
    ],
    "personalized_feedback": "No recurring issues detected",
    "timestamp": "2026-04-19T00:00:00+00:00",
    "source": "local"
  },
  {
    "username": "demo",
    "language": "JavaScript",
    "code": "function first(arr) { return arr[0]; }",
    "mistakes": [
      "missing edge cases"
    ],
    "suggestions": [
      "Check whether the array is empty before reading the first item."
    ],
    "personalized_feedback": "You have hit empty-input problems before, so this may be a recurring pattern.",
    "timestamp": "2026-04-19T00:30:00+00:00",
    "source": "local"
  }
]
```

## Routes

- `GET /login`
- `GET /register`
- `GET /`
- `GET /dashboard`
- `GET /logout`
- `POST /review`

## Notes

- Correct code should not produce forced mistakes because the app uses a stricter prompt and preserves the model's ability to say `No major mistakes found.`
- If `memory.json` contains invalid JSON, the app falls back to an empty history instead of crashing.
- Passwords are stored as hashes, not plain text.
