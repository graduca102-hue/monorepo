import "dotenv/config";
import path from "node:path";

export function loadConfig() {
  const botToken = process.env.BOT_TOKEN?.trim() || "";

  if (!botToken) throw new Error("BOT_TOKEN is required");

  return {
    botToken,
    botUsername: (process.env.BOT_USERNAME || "chessandcheckers").replace(/^@/, ""),
    dataFile: path.resolve(process.env.DATA_FILE || "./data/games.json"),
  };
}
