import { Bot, GrammyError, HttpError } from "grammy";
import {
  activeRichMessage,
  inlineResult,
  waitingRichMessage,
} from "./cards.js";

function fromTelegramUser(user) {
  return {
    id: String(user.id),
    firstName: String(user.first_name || "Игрок").slice(0, 64),
    lastName: String(user.last_name || "").slice(0, 64),
    username: String(user.username || "").slice(0, 64),
    photoUrl: String(user.photo_url || "").slice(0, 2048),
  };
}

function requestedTypes(query) {
  const normalized = query.trim().toLowerCase();
  if (/^(chess|шах)/.test(normalized)) return ["chess"];
  if (/^(checkers|draughts|шаш)/.test(normalized)) return ["checkers"];
  return ["chess", "checkers"];
}

export async function createTelegramBot(config, store) {
  const bot = new Bot(config.botToken);
  const drafts = new Map();
  const selections = new Map();
  const promotions = new Map();
  const orientations = new Map();
  const resignConfirmations = new Map();

  const me = await bot.api.getMe();
  config.botUsername = me.username;

  function gameForDraft(user, type, query) {
    const key = `${user.id}:${type}:${query.trim().toLowerCase()}`;
    const previous = drafts.get(key);
    if (previous && Date.now() - previous.createdAt < 10 * 60 * 1000) {
      const game = store.get(previous.gameId);
      if (game?.status === "waiting") return game;
    }
    const game = store.create(type, user);
    drafts.set(key, { gameId: game.id, createdAt: Date.now() });
    return game;
  }

  function renderOptions(gameId) {
    const selection = selections.get(gameId);
    return {
      orientation: orientations.get(gameId) || "white",
      selectedSquare: selection?.square || null,
      legalMoves: selection?.legalMoves || [],
      promotion: promotions.has(gameId),
    };
  }

  async function editGame(game) {
    if (!game.inlineMessageId) return;
    const richMessage = game.status === "waiting"
      ? waitingRichMessage(game)
      : activeRichMessage(game, renderOptions(game.id));
    try {
      await bot.api.raw.editMessageText({
        inline_message_id: game.inlineMessageId,
        rich_message: richMessage,
        reply_markup: { inline_keyboard: [] },
      });
    } catch (error) {
      const description = error?.description || error?.message || "";
      if (!description.includes("message is not modified")) console.error("Cannot update rich game:", description);
    }
  }

  async function answerError(ctx, error) {
    await ctx.answerCallbackQuery({ text: error.message || "Не удалось выполнить действие", show_alert: true });
  }

  bot.command("start", async (ctx) => {
    await ctx.reply(
      "♟ <b>Шахматы и шашки для двоих</b>\n\nДоска работает прямо в Rich Message Telegram. Выберите чат и отправьте приглашение от своего имени.",
      {
        parse_mode: "HTML",
        reply_markup: {
          inline_keyboard: [[{
            text: "Выбрать игру и чат",
            switch_inline_query_chosen_chat: {
              query: "",
              allow_user_chats: true,
              allow_group_chats: true,
            },
            style: "primary",
          }]],
        },
      },
    );
  });

  bot.command("help", (ctx) => ctx.reply(
    `Введите @${config.botUsername} в любом чате. После подключения соперника нажмите фигуру, затем клетку назначения.`,
  ));

  bot.on("inline_query", async (ctx) => {
    const user = fromTelegramUser(ctx.from);
    const results = requestedTypes(ctx.inlineQuery.query).map((type) => (
      inlineResult(gameForDraft(user, type, ctx.inlineQuery.query))
    ));
    await ctx.answerInlineQuery(results, { cache_time: 0, is_personal: true });
  });

  bot.on("chosen_inline_result", (ctx) => {
    const match = /^g:([A-Za-z0-9_-]{8,32})$/.exec(ctx.chosenInlineResult.result_id);
    if (!match || !ctx.chosenInlineResult.inline_message_id) return;
    try {
      store.attachInlineMessage(match[1], ctx.chosenInlineResult.inline_message_id);
    } catch {
      // Stale inline results can be ignored.
    }
  });

  bot.callbackQuery(/^j:([A-Za-z0-9_-]{8,32})$/, async (ctx) => {
    try {
      const game = store.join(
        ctx.match[1],
        fromTelegramUser(ctx.from),
        ctx.callbackQuery.inline_message_id || null,
      );
      await ctx.answerCallbackQuery({ text: "Партия началась!" });
      await editGame(game);
    } catch (error) {
      await answerError(ctx, error);
    }
  });

  bot.callbackQuery(/^s:([A-Za-z0-9_-]{8,32}):([a-h][1-8])$/, async (ctx) => {
    const [, gameId, square] = ctx.match;
    const user = fromTelegramUser(ctx.from);
    try {
      const game = store.require(gameId);
      const view = store.viewFor(game, user.id);
      if (game.status !== "playing") throw new Error("Партия уже завершена");
      if (view.turn !== view.yourColor) throw new Error("Сейчас ход соперника");

      let selection = selections.get(gameId);
      if (selection?.userId !== user.id) selection = null;
      if (!selection) {
        const legalMoves = view.legalMoves.filter((move) => move.from === square);
        if (!legalMoves.length) throw new Error("Выберите фигуру, которой можно сделать ход");
        selections.set(gameId, { userId: user.id, square, legalMoves });
        await ctx.answerCallbackQuery({ text: `Выбрано ${square}` });
        await editGame(game);
        return;
      }

      const candidates = selection.legalMoves.filter((move) => move.to === square);
      if (!candidates.length) {
        const replacement = view.legalMoves.filter((move) => move.from === square);
        if (!replacement.length) throw new Error("Эта клетка недоступна для выбранной фигуры");
        selections.set(gameId, { userId: user.id, square, legalMoves: replacement });
        await ctx.answerCallbackQuery({ text: `Выбрано ${square}` });
        await editGame(game);
        return;
      }

      if (candidates.some((move) => move.promotion)) {
        promotions.set(gameId, { userId: user.id, moves: candidates });
        await ctx.answerCallbackQuery({ text: "Выберите фигуру для превращения" });
        await editGame(game);
        return;
      }

      const updated = store.move(gameId, user.id, candidates[0]);
      selections.delete(gameId);
      promotions.delete(gameId);
      await ctx.answerCallbackQuery({ text: "Ход сделан" });
      await editGame(updated);
    } catch (error) {
      await answerError(ctx, error);
    }
  });

  bot.callbackQuery(/^p:([A-Za-z0-9_-]{8,32}):([qrbn])$/, async (ctx) => {
    const [, gameId, piece] = ctx.match;
    const user = fromTelegramUser(ctx.from);
    try {
      const pending = promotions.get(gameId);
      if (!pending || pending.userId !== user.id) throw new Error("Превращение уже недоступно");
      const move = pending.moves.find((candidate) => candidate.promotion === piece);
      if (!move) throw new Error("Недопустимая фигура");
      const updated = store.move(gameId, user.id, move);
      selections.delete(gameId);
      promotions.delete(gameId);
      await ctx.answerCallbackQuery({ text: "Фигура выбрана" });
      await editGame(updated);
    } catch (error) {
      await answerError(ctx, error);
    }
  });

  bot.callbackQuery(/^f:([A-Za-z0-9_-]{8,32})$/, async (ctx) => {
    try {
      const game = store.require(ctx.match[1]);
      store.assertCanOpen(game, String(ctx.from.id));
      orientations.set(game.id, orientations.get(game.id) === "black" ? "white" : "black");
      await ctx.answerCallbackQuery({ text: "Доска перевёрнута для всех" });
      await editGame(game);
    } catch (error) {
      await answerError(ctx, error);
    }
  });

  bot.callbackQuery(/^r:([A-Za-z0-9_-]{8,32})$/, async (ctx) => {
    const gameId = ctx.match[1];
    const userId = String(ctx.from.id);
    const key = `${gameId}:${userId}`;
    try {
      if (Date.now() - (resignConfirmations.get(key) || 0) > 8_000) {
        resignConfirmations.set(key, Date.now());
        await ctx.answerCallbackQuery({
          text: "Нажмите «Завершить игру» ещё раз в течение 8 секунд.",
          show_alert: true,
        });
        return;
      }
      resignConfirmations.delete(key);
      const updated = store.resign(gameId, userId);
      selections.delete(gameId);
      promotions.delete(gameId);
      await ctx.answerCallbackQuery({ text: "Партия завершена" });
      await editGame(updated);
    } catch (error) {
      await answerError(ctx, error);
    }
  });

  bot.catch((error) => {
    const cause = error.error;
    if (cause instanceof GrammyError) console.error("Telegram API error:", cause.description);
    else if (cause instanceof HttpError) console.error("Telegram network error:", cause.message);
    else console.error("Bot update error:", cause);
  });

  async function configure() {
    await bot.api.setMyCommands([
      { command: "start", description: "Создать игру" },
      { command: "help", description: "Как играть" },
    ]);
    await bot.api.setChatMenuButton({ menu_button: { type: "commands" } });
  }

  return {
    bot,
    async start() {
      await configure();
      for (const game of store.list()) {
        if (game.inlineMessageId) await editGame(game);
      }
      bot.start({
        allowed_updates: ["message", "inline_query", "chosen_inline_result", "callback_query"],
        onStart: () => console.log(`@${config.botUsername} is polling for Rich Message games`),
      });
    },
    stop() {
      return bot.stop();
    },
  };
}
