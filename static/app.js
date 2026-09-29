function setVisibility(element, isVisible) {
  if (!element) {
    return;
  }

  element.classList.toggle("hidden", !isVisible);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderList(items, emptyText = "No data yet.") {
  if (!items || items.length === 0) {
    return `<li>${escapeHtml(emptyText)}</li>`;
  }

  return items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
}

function reviewTemplate(review, memorySource) {
  const mistakes =
    review.mistakes && review.mistakes.length > 0
      ? review.mistakes
      : ["No major mistakes found."];

  const suggestions =
    review.suggestions && review.suggestions.length > 0
      ? review.suggestions
      : ["No extra suggestions needed."];

  return `
    <article class="review-card">
      <div class="review-card-header">
        <h3>Mistakes</h3>
        <span class="badge">${escapeHtml(memorySource)}</span>
      </div>
      <ul class="plain-list">${renderList(mistakes, "None")}</ul>
    </article>
    <article class="review-card">
      <h3>Suggestions</h3>
      <ul class="plain-list">${renderList(suggestions, "None")}</ul>
    </article>
    <article class="review-card">
      <h3>Personalized Feedback</h3>
      <p>${escapeHtml(review.personalized_feedback || "No recurring issues detected")}</p>
    </article>
  `;
}

async function handleReviewSubmit() {
  const codeInput = document.getElementById("code-input");
  const languageSelect = document.getElementById("language-select");
  const button = document.getElementById("review-button");
  const statusText = document.getElementById("status-text");
  const results = document.getElementById("review-results");
  const warningBox = document.getElementById("warning-box");
  const errorBox = document.getElementById("error-box");

  if (!codeInput || !languageSelect || !button || !results) {
    return;
  }

  const code = codeInput.value.trim();
  const language = languageSelect.value;

  if (!code) {
    errorBox.textContent = "Please enter some code before requesting a review.";
    setVisibility(errorBox, true);
    setVisibility(warningBox, false);
    return;
  }

  button.disabled = true;
  statusText.textContent = "Reviewing your code...";
  setVisibility(errorBox, false);
  setVisibility(warningBox, false);

  try {
    const response = await fetch("/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, language }),
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "The review request failed.");
    }

    if (payload.warning_messages && payload.warning_messages.length > 0) {
      warningBox.innerHTML = payload.warning_messages
        .map((warning) => escapeHtml(warning))
        .join("<br />");
      setVisibility(warningBox, true);
    }

    results.classList.remove("empty-state");
    results.innerHTML = reviewTemplate(payload.review, payload.memory_source || "local");
    statusText.textContent = `Saved on ${new Date(payload.saved_at).toLocaleString()}`;
  } catch (error) {
    errorBox.textContent = error.message;
    setVisibility(errorBox, true);
    statusText.textContent = "";
  } finally {
    button.disabled = false;
  }
}

function updateDashboardUi(data) {
  const errorBox = document.getElementById("dashboard-error");
  const totalReviews = document.getElementById("total-reviews");
  const cleanReviews = document.getElementById("clean-reviews");
  const topMistake = document.getElementById("top-mistake");
  const summaryText = document.getElementById("summary-text");
  const commonMistakeList = document.getElementById("common-mistake-list");
  const historyList = document.getElementById("history-list");

  if (!totalReviews) {
    return;
  }

  if (data.error && errorBox) {
    errorBox.textContent = data.error;
    setVisibility(errorBox, true);
  }

  totalReviews.textContent = data.total_reviews ?? 0;
  cleanReviews.textContent = data.clean_review_count ?? 0;
  topMistake.textContent =
    data.most_common_mistakes && data.most_common_mistakes.length > 0
      ? data.most_common_mistakes[0].mistake
      : "None yet";
  summaryText.textContent =
    data.improvement_summary || "Complete more reviews to build a trend.";

  commonMistakeList.innerHTML = renderList(
    (data.most_common_mistakes || []).map((item) => `${item.mistake} (${item.count})`),
    "No mistakes recorded yet."
  );

  if (!data.last_five_reviews || data.last_five_reviews.length === 0) {
    historyList.innerHTML = "<p>No review history yet.</p>";
    return;
  }

  historyList.innerHTML = data.last_five_reviews
    .map((item) => {
      const mistakes =
        item.mistakes && item.mistakes.length > 0
          ? item.mistakes.join(", ")
          : "No major mistakes found";

      return `
        <article class="history-item">
          <strong>${escapeHtml(new Date(item.timestamp).toLocaleString())}</strong>
          <p><strong>Language:</strong> ${escapeHtml(item.language || "Unknown")}</p>
          <p><strong>Mistakes:</strong> ${escapeHtml(mistakes)}</p>
          <p>${escapeHtml(item.personalized_feedback || "No recurring issues detected")}</p>
          <code>${escapeHtml((item.code || "").slice(0, 280))}</code>
        </article>
      `;
    })
    .join("");
}

async function loadDashboardData() {
  const root = document.getElementById("dashboard-root");
  if (!root) {
    return;
  }

  try {
    const response = await fetch("/dashboard?format=json");
    const data = await response.json();
    updateDashboardUi(data);
  } catch (error) {
    const initialData = JSON.parse(root.dataset.dashboard || "{}");
    initialData.error = initialData.error || error.message;
    updateDashboardUi(initialData);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const reviewButton = document.getElementById("review-button");
  if (reviewButton) {
    reviewButton.addEventListener("click", handleReviewSubmit);
  }

  loadDashboardData();
});
