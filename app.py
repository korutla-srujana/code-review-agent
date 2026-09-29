import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    Groq,
    RateLimitError,
)
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import requests
except ImportError:  # pragma: no cover - safe fallback if requests is unavailable
    requests = None


load_dotenv()


def clear_dead_local_proxy() -> None:
    """Remove the local port-9 proxy sentinel that makes outbound calls fail."""
    proxy_names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    for name in proxy_names:
        value = os.getenv(name, "").strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value if "://" in value else f"http://{value}")
            port = parsed.port
        except ValueError:
            continue
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and port == 9:
            os.environ.pop(name, None)


clear_dead_local_proxy()

BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
MEMORY_FILE = BASE_DIR / "memory.json"
GROQ_MODEL = (
    str(dotenv_values(BASE_DIR / ".env").get("GROQ_MODEL") or "").strip()
    or os.getenv("GROQ_MODEL", "").strip()
    or "openai/gpt-oss-20b"
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_json_file(path: Path, default_value: Any) -> None:
    if path.exists():
        return
    path.write_text(json.dumps(default_value, indent=2), encoding="utf-8")


def read_json_file(path: Path, default_value: Any) -> Any:
    ensure_json_file(path, default_value)

    try:
        raw_text = path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return default_value
        return json.loads(raw_text)
    except (OSError, json.JSONDecodeError):
        return default_value


def write_json_file(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_users() -> list[dict[str, str]]:
    data = read_json_file(USERS_FILE, [])
    if not isinstance(data, list):
        return []

    users: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username", "")).strip()
        password_hash = str(item.get("password_hash", "")).strip()
        if username and password_hash:
            users.append({"username": username, "password_hash": password_hash})
    return users


def save_users(users: list[dict[str, str]]) -> None:
    write_json_file(USERS_FILE, users)


def find_user(username: str) -> dict[str, str] | None:
    normalized_username = username.strip().lower()
    for user in load_users():
        if user["username"].lower() == normalized_username:
            return user
    return None


def register_user(username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    password = password.strip()

    if len(username) < 3:
        return False, "Username must be at least 3 characters long."
    if len(password) < 4:
        return False, "Password must be at least 4 characters long."
    if find_user(username):
        return False, "That username already exists."

    users = load_users()
    users.append(
        {
            "username": username,
            "password_hash": generate_password_hash(password),
        }
    )
    save_users(users)
    return True, "Account created successfully."


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def load_memory_records() -> list[dict[str, Any]]:
    data = read_json_file(MEMORY_FILE, [])
    if not isinstance(data, list):
        return []

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        mistakes = item.get("mistakes", [])
        suggestions = item.get("suggestions", [])

        records.append(
            {
                "username": str(item.get("username", "")).strip(),
                "language": str(item.get("language", "")).strip() or "Unknown",
                "code": str(item.get("code", "")),
                "mistakes": mistakes if isinstance(mistakes, list) else [],
                "suggestions": suggestions if isinstance(suggestions, list) else [],
                "personalized_feedback": str(
                    item.get("personalized_feedback", "No recurring issues detected")
                ).strip(),
                "timestamp": str(item.get("timestamp", "")).strip(),
                "source": str(item.get("source", "local")).strip() or "local",
            }
        )

    records.sort(key=lambda item: item.get("timestamp", ""))
    return records


def save_memory_records(records: list[dict[str, Any]]) -> None:
    write_json_file(MEMORY_FILE, records)


def hindsight_is_configured() -> bool:
    return all(
        [
            os.getenv("HINDSIGHT_API_KEY", "").strip(),
            os.getenv("HINDSIGHT_BASE_URL", "").strip(),
            os.getenv("HINDSIGHT_BANK_ID", "").strip(),
            requests is not None,
        ]
    )


def store_memory(
    username: str,
    language: str,
    code: str,
    mistakes: list[str],
    suggestions: list[str],
    personalized_feedback: str,
) -> dict[str, Any]:
    record = {
        "username": username,
        "language": language,
        "code": code,
        "mistakes": mistakes,
        "suggestions": suggestions,
        "personalized_feedback": personalized_feedback,
        "timestamp": utc_now_iso(),
        "source": "local",
    }

    if hindsight_is_configured():
        try:
            base_url = os.getenv("HINDSIGHT_BASE_URL", "").rstrip("/")
            response = requests.post(
                f"{base_url}/banks/{os.getenv('HINDSIGHT_BANK_ID')}/memories",
                headers={
                    "Authorization": f"Bearer {os.getenv('HINDSIGHT_API_KEY')}",
                    "Content-Type": "application/json",
                },
                json={
                    "content": {
                        "username": username,
                        "language": language,
                        "code": code,
                        "mistakes": mistakes,
                        "timestamp": record["timestamp"],
                    }
                },
                timeout=10,
            )
            if response.ok:
                record["source"] = "hindsight"
        except Exception:
            record["source"] = "local"

    records = load_memory_records()
    records.append(record)
    save_memory_records(records)
    return record


def retrieve_memory(username: str, limit: int = 10) -> list[dict[str, Any]]:
    records = [
        item
        for item in load_memory_records()
        if item.get("username", "").lower() == username.lower()
    ]
    return records[-limit:]


def normalize_mistake_text(mistake: str) -> str:
    cleaned = " ".join(mistake.strip().lower().split())
    if cleaned in {"none", "no major mistakes found.", "no major mistakes found"}:
        return ""
    return cleaned


def detect_patterns(records: list[dict[str, Any]]) -> dict[str, Any]:
    mistake_counter: Counter[str] = Counter()

    for record in records:
        for mistake in record.get("mistakes", []):
            normalized = normalize_mistake_text(str(mistake))
            if normalized:
                mistake_counter[normalized] += 1

    repeated_patterns = [
        {"mistake": mistake, "count": count}
        for mistake, count in mistake_counter.most_common()
        if count > 2
    ]

    return {
        "total_reviews": len(records),
        "most_common_mistakes": [
            {"mistake": mistake, "count": count}
            for mistake, count in mistake_counter.most_common(5)
        ],
        "repeated_patterns": repeated_patterns,
    }


def build_memory_context(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No past data."

    lines = []
    for record in records[-5:]:
        mistake_text = ", ".join(record.get("mistakes", [])) or "None"
        lines.append(
            f"- {record.get('timestamp', 'Unknown time')} | {record.get('language', 'Unknown')} | "
            f"Mistakes: {mistake_text}"
        )
    return "\n".join(lines)


def build_review_prompt(code: str, language: str, memory_text: str) -> str:
    return f"""You are a strict but accurate code reviewer.

Step 1: Analyze the given code carefully.

* If the code is correct and has no major issues, say:
  "No major mistakes found."

Step 2: Only list mistakes if they truly exist:

* missing edge cases
* inefficient logic
* syntax issues

Step 3: Use past mistakes ONLY IF relevant.

* Do NOT assume mistakes
* Do NOT force patterns

Past user mistakes:
{memory_text}

Now review this {language} code:
{code}

Output format:

Mistakes:
(list only if real, otherwise say "None")

Suggestions:
(relevant improvements only)

Personalized Feedback:
(only if current code matches past patterns, otherwise say "No recurring issues detected")"""


def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing GROQ_API_KEY. Add it to your .env file.")
    return Groq(api_key=api_key)


def parse_review_sections(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("The AI returned an empty review.")

    section_names = ["Mistakes:", "Suggestions:", "Personalized Feedback:"]
    sections = {name: [] for name in section_names}
    current_section = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched_section = None
        for section_name in section_names:
            if line == section_name:
                matched_section = section_name
                current_section = section_name
                break
            if line.startswith(section_name):
                matched_section = section_name
                current_section = section_name
                remainder = line[len(section_name) :].strip()
                if remainder:
                    sections[section_name].append(remainder)
                break

        if matched_section:
            continue

        if current_section:
            if line.startswith("-"):
                line = line[1:].strip()
            if line.startswith("*"):
                line = line[1:].strip()
            sections[current_section].append(line)

    mistakes = [
        item
        for item in sections["Mistakes:"]
        if normalize_mistake_text(item)
    ]

    suggestions = [
        item
        for item in sections["Suggestions:"]
        if item.lower() not in {"none", "no major mistakes found."}
    ]

    feedback_lines = sections["Personalized Feedback:"]
    personalized_feedback = (
        " ".join(feedback_lines).strip() if feedback_lines else "No recurring issues detected"
    )

    if not mistakes and "No major mistakes found." in text:
        suggestions = suggestions or []
        personalized_feedback = personalized_feedback or "No recurring issues detected"

    return {
        "mistakes": mistakes,
        "suggestions": suggestions,
        "personalized_feedback": personalized_feedback or "No recurring issues detected",
        "raw_review": text,
    }


def request_ai_review(code: str, language: str, memory_text: str) -> dict[str, Any]:
    client = get_groq_client()

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "You review code carefully and never invent mistakes "
                    "that are not present."
                ),
            },
            {
                "role": "user",
                "content": build_review_prompt(
                    code,
                    language,
                    memory_text
                ),
            },
        ],
    )

    raw_text = response.choices[0].message.content or ""

    return parse_review_sections(raw_text)


def find_warning_messages(
    repeated_patterns: list[dict[str, Any]], current_mistakes: list[str]
) -> list[str]:
    if not current_mistakes:
        return []

    current_normalized = {normalize_mistake_text(item) for item in current_mistakes}
    warnings = []

    for pattern in repeated_patterns:
        mistake = normalize_mistake_text(str(pattern.get("mistake", "")))
        if mistake and mistake in current_normalized:
            warnings.append(f"\u26a0\ufe0f Be careful - you tend to make {mistake}")

    return warnings


def count_mistake_occurrences(records: list[dict[str, Any]], target_mistake: str) -> int:
    total = 0
    normalized_target = normalize_mistake_text(target_mistake)
    if not normalized_target:
        return 0

    for record in records:
        for mistake in record.get("mistakes", []):
            if normalize_mistake_text(str(mistake)) == normalized_target:
                total += 1
    return total


def build_improvement_summary(records: list[dict[str, Any]]) -> str:
    if len(records) < 2:
        return "Complete a few reviews to see your improvement trends."

    pattern_data = detect_patterns(records)
    if not pattern_data["most_common_mistakes"]:
        return "Your recent reviews look clean so far."

    top_mistake = pattern_data["most_common_mistakes"][0]["mistake"]
    midpoint = max(1, len(records) // 2)
    older_records = records[:midpoint]
    newer_records = records[midpoint:]

    older_count = count_mistake_occurrences(older_records, top_mistake)
    newer_count = count_mistake_occurrences(newer_records, top_mistake)

    if newer_count < older_count:
        return f"Your {top_mistake} mistakes reduced over time."
    if newer_count > older_count:
        return f"Your {top_mistake} mistakes are showing up more often recently."
    return f"Your {top_mistake} mistake pattern is steady so far."


def build_dashboard_data(username: str) -> dict[str, Any]:
    records = retrieve_memory(username, limit=500)
    pattern_data = detect_patterns(records)

    last_five_reviews = list(reversed(records[-5:]))
    clean_review_count = sum(1 for record in records if not record.get("mistakes"))

    return {
        "total_reviews": pattern_data["total_reviews"],
        "most_common_mistakes": pattern_data["most_common_mistakes"],
        "last_five_reviews": last_five_reviews,
        "improvement_summary": build_improvement_summary(records),
        "clean_review_count": clean_review_count,
    }


@app.get("/register")
def register() -> str:
    if "username" in session:
        return redirect(url_for("review_page"))
    return render_template("register.html")


@app.post("/register")
def register_post():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    success, message = register_user(username, password)
    if not success:
        flash(message, "error")
        return redirect(url_for("register"))

    flash(message + " Please log in.", "success")
    return redirect(url_for("login"))


@app.get("/login")
def login() -> str:
    if "username" in session:
        return redirect(url_for("review_page"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    user = find_user(username)

    if not user or not check_password_hash(user["password_hash"], password):
        flash("Invalid username or password.", "error")
        return redirect(url_for("login"))

    session["username"] = user["username"]
    flash("Logged in successfully.", "success")
    return redirect(url_for("review_page"))


@app.get("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.get("/")
@login_required
def review_page() -> str:
    return render_template("index.html", username=session["username"])


@app.post("/review")
@login_required
def review_code():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip()
    language = str(payload.get("language", "")).strip()
    username = session["username"]

    allowed_languages = {"Python", "Java", "C++", "JavaScript"}

    if not code:
        return jsonify({"error": "Please enter some code before requesting a review."}), 400
    if language not in allowed_languages:
        return jsonify({"error": "Please choose a supported programming language."}), 400

    try:
        previous_records = retrieve_memory(username)
        memory_text = build_memory_context(previous_records)
        review_result = request_ai_review(code, language, memory_text)
        pattern_data = detect_patterns(previous_records)
        warning_messages = find_warning_messages(
            pattern_data["repeated_patterns"], review_result["mistakes"]
        )

        saved_record = store_memory(
            username=username,
            language=language,
            code=code,
            mistakes=review_result["mistakes"],
            suggestions=review_result["suggestions"],
            personalized_feedback=review_result["personalized_feedback"],
        )

        return jsonify(
            {
                "review": review_result,
                "warning_messages": warning_messages,
                "saved_at": saved_record["timestamp"],
                "memory_source": saved_record["source"],
            }
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except OSError as error:
        return jsonify({"error": f"Could not read local memory data: {error}"}), 500
    except AuthenticationError:
        app.logger.exception("Groq rejected the configured API key")
        return jsonify(
            {"error": "Groq rejected the API key. Check GROQ_API_KEY in .env, save it, and restart the app."}
        ), 401
    except RateLimitError:
        app.logger.exception("Groq rate limit or quota error")
        return jsonify(
            {"error": "Groq rate limit or quota reached. Check your Groq account usage and try again later."}
        ), 429
    except APIConnectionError as error:
        app.logger.exception("Could not connect to the Groq API")
        cause = error.__cause__ or error.__context__
        cause_type = type(cause).__name__ if cause is not None else type(error).__name__
        cause_detail = str(cause) if cause is not None else "No lower-level detail was provided."
        cause_detail = re.sub(r"(https?://)[^/@\s]+@", r"\1[redacted]@", cause_detail)
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            cause_detail = cause_detail.replace(api_key, "[redacted]")
        return jsonify(
            {
                "error": (
                    "Could not connect to Groq "
                    f"(network error: {cause_type}: {cause_detail}). "
                    "Check VPN/proxy/firewall settings and try again."
                )
            }
        ), 502
    except APIStatusError as error:
        app.logger.exception("Groq API returned HTTP %s", error.status_code)
        if error.status_code == 404:
            message = f"Groq model '{GROQ_MODEL}' was not found or is unavailable to this key. Check GROQ_MODEL in .env."
        elif error.status_code == 403:
            message = "Groq denied access to this model or account. Check model access in your Groq Console."
        else:
            message = f"Groq returned an API error (HTTP {error.status_code}). See the Flask terminal for technical details."
        return jsonify({"error": message}), 502
    except Exception as error:
        app.logger.exception("Unexpected code review error (%s)", type(error).__name__)
        return jsonify(
            {"error": "The review could not be completed because of an unexpected server error. See the Flask terminal for technical details."}
        ), 500


@app.get("/dashboard")
@login_required
def dashboard():
    dashboard_data = build_dashboard_data(session["username"])

    if request.args.get("format") == "json":
        return jsonify(dashboard_data)

    return render_template(
        "dashboard.html",
        username=session["username"],
        dashboard_data=dashboard_data,
    )


if __name__ == "__main__":
    ensure_json_file(USERS_FILE, [])
    ensure_json_file(MEMORY_FILE, [])
    app.run(debug=True)
