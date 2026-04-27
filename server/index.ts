import "dotenv/config";
import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Markup, Telegraf } from "telegraf";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const port = Number(process.env.PORT ?? 3000);
const botToken = process.env.BOT_TOKEN;
const webAppUrl = process.env.WEBAPP_URL;
const distPath = path.resolve(__dirname, "../dist");

const app = express();

app.disable("x-powered-by");

app.get("/health", (_request, response) => {
  response.json({
    ok: true,
    service: "ryan-rover-miniapp",
    bot: Boolean(botToken && webAppUrl)
  });
});

app.use(express.static(distPath, { index: false }));

app.use((request, response, next) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    next();
    return;
  }

  response.sendFile(path.join(distPath, "index.html"));
});

async function startBot() {
  if (!botToken || !webAppUrl) {
    console.warn("BOT_TOKEN or WEBAPP_URL is missing. HTTP server will run without Telegram bot.");
    return;
  }

  const bot = new Telegraf(botToken);
  const webAppButton = Markup.button.webApp("Открыть Райн-ровер", webAppUrl);

  bot.start(async (context) => {
    await context.reply(
      "Райн-ровер готов к аналитике.",
      Markup.inlineKeyboard([webAppButton])
    );
  });

  bot.command("app", async (context) => {
    await context.reply("Открывай миниапп:", Markup.inlineKeyboard([webAppButton]));
  });

  bot.catch((error) => {
    console.error("Telegram bot error:", error);
  });

  await bot.telegram.setChatMenuButton({
    menuButton: {
      type: "web_app",
      text: "Райн-ровер",
      web_app: {
        url: webAppUrl
      }
    }
  });

  await bot.launch();
  console.log("Telegram bot is running in long polling mode.");

  const stop = (signal: NodeJS.Signals) => {
    console.log(`Received ${signal}, stopping bot.`);
    bot.stop(signal);
  };

  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

app.listen(port, () => {
  console.log(`HTTP server is listening on http://0.0.0.0:${port}`);
});

void startBot();
