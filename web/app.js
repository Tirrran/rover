const ANALYTICS_DURATION_MS = 15000;
const WELCOME_DURATION_MS = 2400;
const START_API_PATH = "/api/start";

const screens = {
  welcome: document.querySelector('[data-screen="welcome"]'),
  start: document.querySelector('[data-screen="start"]'),
  loading: document.querySelector('[data-screen="loading"]'),
  result: document.querySelector('[data-screen="result"]'),
};
let started = false;

function setupTelegram() {
  const webApp = window.Telegram?.WebApp;
  if (!webApp) {
    return;
  }

  webApp.ready();
  webApp.expand();
  webApp.setHeaderColor?.("#2f2f32");
  webApp.setBackgroundColor?.("#2f2f32");
}

function showScreen(name) {
  for (const [screenName, element] of Object.entries(screens)) {
    element.hidden = screenName !== name;
    element.classList.toggle("screen-enter", screenName === name);
  }
}

async function triggerRobotSnapshot() {
  const webApp = window.Telegram?.WebApp;
  const payload = {
    initData: webApp?.initData ?? "",
  };

  const response = await fetch(START_API_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const details = await response.text();
    throw new Error(details || `Request failed: ${response.status}`);
  }

  return response.json();
}

function startAnalytics() {
  if (started) {
    return;
  }
  started = true;

  showScreen("loading");

  triggerRobotSnapshot()
    .then(() => window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.("success"))
    .catch((error) => {
      console.error("Failed to request robot screenshot:", error);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred?.("error");
    });

  window.setTimeout(() => showScreen("result"), ANALYTICS_DURATION_MS);
}

setupTelegram();
window.setTimeout(() => showScreen("start"), WELCOME_DURATION_MS);
document.querySelector(".start-button").addEventListener("click", startAnalytics);
