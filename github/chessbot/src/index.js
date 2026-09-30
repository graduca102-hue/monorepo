import { loadConfig } from "./config.js";
import { GameStore } from "./games/store.js";
import { createTelegramBot } from "./telegram/bot.js";

const config = loadConfig();
const store = new GameStore(config.dataFile);
const telegram = await createTelegramBot(config, store);
await telegram.start();

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`${signal}: shutting down`);
  await telegram.stop();
  process.exit(0);
}

process.once("SIGINT", () => shutdown("SIGINT"));
process.once("SIGTERM", () => shutdown("SIGTERM"));
