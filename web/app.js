const ANALYTICS_DURATION_MS = 15000;
const WELCOME_DURATION_MS = 2400;

const screens = {
  welcome: document.querySelector('[data-screen="welcome"]'),
  start: document.querySelector('[data-screen="start"]'),
  loading: document.querySelector('[data-screen="loading"]'),
  result: document.querySelector('[data-screen="result"]'),
};

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

function startAnalytics() {
  showScreen("loading");
  window.setTimeout(() => showScreen("result"), ANALYTICS_DURATION_MS);
}

setupTelegram();
window.setTimeout(() => showScreen("start"), WELCOME_DURATION_MS);
document.querySelector(".start-button").addEventListener("click", startAnalytics);
