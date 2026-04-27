import { useEffect, useState } from "react";

type Screen = "welcome" | "start" | "loading" | "result";

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        ready: () => void;
        expand: () => void;
        setHeaderColor?: (color: string) => void;
        setBackgroundColor?: (color: string) => void;
      };
    };
  }
}

const ANALYTICS_DURATION_MS = 15_000;

const stats = [
  ["мотоциклы", "00"],
  ["машинка", "00"],
  ["пончики", "00"],
  ["яблоки", "00"]
] as const;

export default function App() {
  const [screen, setScreen] = useState<Screen>("welcome");

  useEffect(() => {
    const webApp = window.Telegram?.WebApp;
    webApp?.ready();
    webApp?.expand();
    webApp?.setHeaderColor?.("#2f2f32");
    webApp?.setBackgroundColor?.("#2f2f32");
  }, []);

  useEffect(() => {
    if (screen !== "welcome") {
      return;
    }

    const timer = window.setTimeout(() => setScreen("start"), 2_400);
    return () => window.clearTimeout(timer);
  }, [screen]);

  useEffect(() => {
    if (screen !== "loading") {
      return;
    }

    const timer = window.setTimeout(() => setScreen("result"), ANALYTICS_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [screen]);

  return (
    <main className="app-shell" aria-live="polite">
      {screen === "welcome" && <WelcomeScreen />}
      {screen === "start" && <StartScreen onStart={() => setScreen("loading")} />}
      {screen === "loading" && <LoadingScreen />}
      {screen === "result" && <ResultScreen />}
    </main>
  );
}

function WelcomeScreen() {
  return (
    <section className="screen screen-welcome screen-enter" aria-label="Приветственный экран">
      <img className="welcome-ellipse" src="/assets/welcome-ellipse.svg" alt="" draggable="false" />
      <img className="welcome-vector" src="/assets/welcome-vector.svg" alt="" draggable="false" />
      <img className="welcome-main" src="/assets/welcome-main.png" alt="" draggable="false" />
      <h1 className="welcome-title">Райн-ровер</h1>
    </section>
  );
}

function StartScreen({ onStart }: { onStart: () => void }) {
  return (
    <section className="screen screen-start screen-enter" aria-label="Старт аналитики">
      <img className="start-ellipse" src="/assets/start-ellipse.svg" alt="" draggable="false" />
      <img className="start-main" src="/assets/start-main.png" alt="" draggable="false" />
      <button className="start-button" type="button" onClick={onStart}>
        Начать
      </button>
    </section>
  );
}

function LoadingScreen() {
  return (
    <section className="screen screen-loading screen-enter" aria-label="Загрузка аналитики">
      <div className="analytics-line" />
      <img className="loading-rider" src="/assets/loading-rider.png" alt="" draggable="false" />
      <p className="loading-title">Пошла аналитика...</p>
    </section>
  );
}

function ResultScreen() {
  return (
    <section className="screen screen-result screen-enter" aria-label="Статистика">
      <article className="stats-card">
        <h2>Итого:</h2>
        <dl>
          {stats.map(([label, value]) => (
            <div className="stats-row" key={label}>
              <dt>{label}:</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </article>
      <img className="result-burst" src="/assets/result-burst.svg" alt="" draggable="false" />
      <img className="result-rover" src="/assets/result-rover.png" alt="" draggable="false" />
    </section>
  );
}
